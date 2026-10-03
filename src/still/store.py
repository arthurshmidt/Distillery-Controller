"""SQLite persistence: the history behind `/api/history` and the settings that
survive a restart (active profile, per-profile setpoint and PID gains, and
the supply setpoint and gains).

One connection is shared between the control thread (writes) and API threads
(reads), serialised by a lock. WAL with `synchronous=NORMAL` avoids an fsync on
every commit, which matters for the Pi's SD card at one row per second.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .controller import ControllerState, Mode
from .hardware.base import Temperatures

DEFAULT_RETENTION_DAYS = 14
PRUNE_EVERY_S = 3600.0

# Consecutive rows further apart than this mean the daemon was not logging.
LOG_GAP_S = 60.0
SCAN_CHUNK = 20000
MAX_SEEK_BUCKETS = 5000
MAX_EVENTS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    ts REAL NOT NULL,
    mode TEXT NOT NULL,
    profile TEXT NOT NULL,
    setpoint_f REAL NOT NULL,
    deph_supply REAL, deph_return REAL, cond_supply REAL, cond_return REAL,
    valve_deph REAL NOT NULL, valve_cond REAL NOT NULL,
    term_p REAL NOT NULL, term_i REAL NOT NULL, term_d REAL NOT NULL,
    gain_p REAL NOT NULL, gain_i REAL NOT NULL, gain_d REAL NOT NULL,
    fault TEXT,
    valve_supply REAL,
    supply_setpoint_f REAL,
    supply_term_p REAL, supply_term_i REAL, supply_term_d REAL,
    supply_gain_p REAL, supply_gain_i REAL, supply_gain_d REAL
);
CREATE INDEX IF NOT EXISTS history_ts ON history (ts);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_COLUMNS = (
    "ts, mode, profile, setpoint_f, deph_supply, deph_return, cond_supply, cond_return, "
    "valve_deph, valve_cond, term_p, term_i, term_d, gain_p, gain_i, gain_d, fault, "
    "valve_supply, supply_setpoint_f, supply_term_p, supply_term_i, supply_term_d, "
    "supply_gain_p, supply_gain_i, supply_gain_d"
)
_EVENT_COLUMNS = "ts, mode, profile, setpoint_f, supply_setpoint_f, fault"
_EXPORT_COLUMNS = (
    "ts, mode, profile, setpoint_f, deph_supply, deph_return, cond_supply, cond_return, "
    "valve_deph, valve_cond, valve_supply, supply_setpoint_f, fault"
)
# Columns added after the first release; older database files get them on open.
_ADDED_COLUMNS = (
    "valve_supply", "supply_setpoint_f", "supply_term_p", "supply_term_i", "supply_term_d",
    "supply_gain_p", "supply_gain_i", "supply_gain_d",
)  # fmt: skip


@dataclass(frozen=True)
class HistoryPoint:
    timestamp: float
    state: ControllerState


class Store:
    def __init__(self, path: str = ":memory:", retention_days: float = DEFAULT_RETENTION_DAYS):
        self._lock = threading.Lock()
        self._retention_s = retention_days * 86400
        self._last_prune = 0.0
        self._db = sqlite3.connect(path, check_same_thread=False)
        # Long history reads use their own connection (WAL lets them run beside
        # writes) so a 14-day scan never holds up the control thread's insert.
        if path == ":memory:":
            self._rdb, self._rlock = self._db, self._lock
        else:
            self._rdb = None  # opened below, once the schema exists
            self._rlock = threading.Lock()
        with self._lock:
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            have = {r[1] for r in self._db.execute("PRAGMA table_info(history)")}
            for column in _ADDED_COLUMNS:
                if column not in have:
                    self._db.execute(f"ALTER TABLE history ADD COLUMN {column} REAL")
            self._db.commit()
        if self._rdb is None:
            self._rdb = sqlite3.connect(path, check_same_thread=False)
        self.prune()

    def close(self) -> None:
        with self._lock:
            self._db.close()
        if self._rdb is not self._db:
            with self._rlock:
                self._rdb.close()

    # -- history -----------------------------------------------------------

    def add_history(self, timestamp: float, s: ControllerState) -> None:
        t = s.temps_f
        row = (
            timestamp, s.mode.value, s.profile, s.setpoint_f,
            t.deph_supply if t else None, t.deph_return if t else None,
            t.cond_supply if t else None, t.cond_return if t else None,
            s.valves_pct["dephlegmator"], s.valves_pct["condenser"],
            *s.pid_terms, *s.pid_gains, s.fault,
            s.valves_pct.get("supply"), s.supply_setpoint_f, *s.supply_pid_terms, *s.supply_pid_gains,
        )  # fmt: skip
        with self._lock:
            self._db.execute(f"INSERT INTO history ({_COLUMNS}) VALUES ({','.join('?' * 25)})", row)
            self._db.commit()
        if timestamp - self._last_prune > PRUNE_EVERY_S:
            self.prune(now=timestamp)

    def history(
        self,
        since: Optional[float] = None,
        limit: Optional[int] = None,
        until: Optional[float] = None,
        step: Optional[float] = None,
    ) -> List[HistoryPoint]:
        """Points with `since` < time <= `until`, oldest first. With `step`
        (seconds), at most one point per step-second bucket, the earliest in
        it. With `limit`, the newest `limit` of what remains."""
        if limit is not None and limit <= 0:
            return []
        where, args = _window(since, until, inclusive_since=False)
        if step is not None and step > 0:
            rows = self._thinned_rows(since, until, float(step))
            if limit is not None:
                rows = rows[-limit:]
            return [_point(r) for r in rows]
        sql = f"SELECT {_COLUMNS} FROM history{where}"
        sql += " ORDER BY ts DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self._rlock:
            rows = self._rdb.execute(sql, args).fetchall()
        return [_point(r) for r in reversed(rows)]

    def _thinned_rows(self, since: Optional[float], until: Optional[float], step: float) -> List[tuple]:
        """The earliest row of each step-second bucket (buckets are multiples
        of `step`), oldest first. With both bounds known and a modest number of
        buckets this is one index seek per bucket, which stays fast over 14
        days of one-second rows; otherwise one pass groups the whole window."""
        where, args = _window(since, until, inclusive_since=False)
        if since is not None and until is not None and (until - since) / step <= MAX_SEEK_BUCKETS:
            first = int(since // step)
            last = int(until // step)
            sql = f"SELECT {_COLUMNS} FROM history WHERE ts >= ? AND ts < ? AND ts > ? AND ts <= ? ORDER BY ts LIMIT 1"
            rows = []
            with self._rlock:
                for k in range(first, last + 1):
                    r = self._rdb.execute(sql, (k * step, (k + 1) * step, since, until)).fetchone()
                    if r is not None:
                        rows.append(r)
            return rows
        # Bare columns beside MIN() come from the row that holds the minimum.
        sql = f"SELECT {_COLUMNS}, MIN(ts) FROM history{where} GROUP BY CAST(ts / ? AS INTEGER) ORDER BY ts"
        with self._rlock:
            return self._rdb.execute(sql, args + [step]).fetchall()

    def _scan(self, columns: str, since: Optional[float], until: Optional[float]) -> Iterator[tuple]:
        """Every row with `since` <= time <= `until`, oldest first, read in
        chunks so the lock is never held for long and memory stays small."""
        last = None
        while True:
            where, args = _window(since, until, inclusive_since=True)
            if last is not None:
                where = (where + " AND" if where else " WHERE") + " ts > ?"
                args.append(last)
            with self._rlock:
                rows = self._rdb.execute(
                    f"SELECT {columns} FROM history{where} ORDER BY ts LIMIT {SCAN_CHUNK}", args
                ).fetchall()
            yield from rows
            if len(rows) < SCAN_CHUNK:
                return
            last = rows[-1][0]

    def rows_for_export(self, since: Optional[float], until: Optional[float]) -> Iterator[tuple]:
        """Full-resolution rows for the CSV export: (ts, mode, profile,
        setpoint_f, deph_supply, deph_return, cond_supply, cond_return,
        valve_deph, valve_cond, valve_supply, supply_setpoint_f, fault)."""
        return self._scan(_EXPORT_COLUMNS, since, until)

    def events(self, since: Optional[float] = None, until: Optional[float] = None) -> Tuple[List[dict], List[dict]]:
        """Changes found by comparing consecutive full-resolution rows, plus
        the mode/fault segments for the timeline. Gaps in logging leave
        segments out. Returns (events oldest first, segments).

        SQLite does the comparing (each row joined to the one logged just before
        it, by rowid) and returns only the rows where something changed, so a
        14-day window is not pulled into Python."""
        with self._rlock:
            first_ts, last_ts = self._rdb.execute(
                "SELECT MIN(ts), MAX(ts) FROM history WHERE ts >= ? AND ts <= ?",
                (-1e18 if since is None else since, 1e18 if until is None else until),
            ).fetchone()
            if first_ts is None:
                return [], []
            rows = self._rdb.execute(
                f"""
                SELECT a.ts, a.mode, a.profile, a.setpoint_f, a.supply_setpoint_f, a.fault,
                       b.ts, b.mode, b.profile, b.setpoint_f, b.supply_setpoint_f, b.fault
                FROM history a LEFT JOIN history b ON b.rowid = a.rowid - 1
                WHERE a.ts >= ? AND a.ts <= ?
                  AND (a.ts = ? OR b.ts IS NULL OR ABS(a.ts - b.ts) > {LOG_GAP_S}
                       OR a.mode != b.mode OR a.profile != b.profile OR a.setpoint_f != b.setpoint_f
                       OR a.supply_setpoint_f IS NOT b.supply_setpoint_f
                       OR (a.fault IS NULL) != (b.fault IS NULL))
                ORDER BY a.ts
                """,
                (first_ts, last_ts, first_ts),
            ).fetchall()

        events: List[dict] = []
        segments: List[dict] = []
        fault_start: Optional[float] = None
        seg: Optional[dict] = None

        def add(t, kind, text, tone=None):
            events.append({"timestamp": t, "kind": kind, "text": text, "tone": tone})

        for r in rows:
            t, mode, profile, sp, ssp, fault, pts, pmode, pprofile, psp, pssp, pfault = r
            key = "fault" if fault else mode
            if pts is None or abs(t - pts) > LOG_GAP_S:
                if pts is not None and pts >= first_ts:
                    add(pts, "log", "Logging stopped")
                add(t, "log", f"Logging started \u00b7 {mode}, {profile} profile")
                if seg is not None:
                    seg["end"] = pts if pts is not None else seg["end"]
                seg = None
                fault_start = t if fault else None
            else:
                if mode != pmode:
                    add(t, "mode", f"Mode {pmode} \u2192 {mode}", None if mode == "auto" else "amber")
                if profile != pprofile:
                    add(t, "profile", f"Profile {pprofile} \u2192 {profile}")
                if sp != psp:
                    add(t, "setpoint", f"Dephlegmator setpoint {psp:.1f} \u2192 {sp:.1f} \u00b0F")
                if ssp != pssp and ssp is not None and pssp is not None:
                    add(t, "setpoint", f"Supply setpoint {pssp:.1f} \u2192 {ssp:.1f} \u00b0F")
                if fault and not pfault:
                    fault_start = t
                    add(t, "fault", fault, "red")
                elif pfault and not fault:
                    add(t, "fault", "Fault cleared" if fault_start is None else f"Fault cleared after {int(t - fault_start)} s")
                    fault_start = None
            if seg is not None and seg["kind"] == key:
                continue
            if seg is not None:
                seg["end"] = t  # touching segments share the boundary
            seg = {"start": t, "end": t, "kind": key}
            segments.append(seg)
        if seg is not None:
            seg["end"] = last_ts
        return events[-MAX_EVENTS:], segments

    def prune(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._db.execute("DELETE FROM history WHERE ts < ?", (now - self._retention_s,))
            self._db.commit()
        self._last_prune = now

    # -- settings ----------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
            self._db.commit()


def _point(r: tuple) -> HistoryPoint:
    temps = None if r[4] is None else Temperatures(r[4], r[5], r[6], r[7])
    valves = {"dephlegmator": r[8], "condenser": r[9]}
    if r[17] is not None:  # rows from before the supply loop, or before it was first commanded
        valves["supply"] = r[17]
    state = ControllerState(
        mode=Mode(r[1]),
        profile=r[2],
        setpoint_f=r[3],
        temps_f=temps,
        valves_pct=valves,
        pid_terms=(r[10], r[11], r[12]),
        pid_gains=(r[13], r[14], r[15]),
        supply_setpoint_f=r[18] if r[18] is not None else 0.0,
        supply_pid_terms=tuple(0.0 if v is None else v for v in r[19:22]),
        supply_pid_gains=tuple(0.0 if v is None else v for v in r[22:25]),
        fault=r[16],
    )
    return HistoryPoint(r[0], state)


def _window(since: Optional[float], until: Optional[float], inclusive_since: bool) -> Tuple[str, List[Any]]:
    clauses, args = [], []
    if since is not None:
        clauses.append("ts >= ?" if inclusive_since else "ts > ?")
        args.append(since)
    if until is not None:
        clauses.append("ts <= ?")
        args.append(until)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), args
