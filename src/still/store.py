"""SQLite persistence: the history behind `/api/history` and the settings that
survive a restart (active profile, per-profile setpoint and PID gains).

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
from typing import Any, List, Optional

from .controller import ControllerState, Mode
from .hardware.base import Temperatures

DEFAULT_RETENTION_DAYS = 14
PRUNE_EVERY_S = 3600.0

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
    fault TEXT
);
CREATE INDEX IF NOT EXISTS history_ts ON history (ts);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_COLUMNS = (
    "ts, mode, profile, setpoint_f, deph_supply, deph_return, cond_supply, cond_return, "
    "valve_deph, valve_cond, term_p, term_i, term_d, gain_p, gain_i, gain_d, fault"
)


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
        with self._lock:
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
        self.prune()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- history -----------------------------------------------------------

    def add_history(self, timestamp: float, s: ControllerState) -> None:
        t = s.temps_f
        row = (
            timestamp, s.mode.value, s.profile, s.setpoint_f,
            t.deph_supply if t else None, t.deph_return if t else None,
            t.cond_supply if t else None, t.cond_return if t else None,
            s.valves_pct["dephlegmator"], s.valves_pct["condenser"],
            *s.pid_terms, *s.pid_gains, s.fault,
        )  # fmt: skip
        with self._lock:
            self._db.execute(f"INSERT INTO history ({_COLUMNS}) VALUES ({','.join('?' * 17)})", row)
            self._db.commit()
        if timestamp - self._last_prune > PRUNE_EVERY_S:
            self.prune(now=timestamp)

    def history(self, since: Optional[float] = None, limit: Optional[int] = None) -> List[HistoryPoint]:
        """Points after `since`, oldest first; with `limit`, the newest `limit` of them."""
        if limit is not None and limit <= 0:
            return []
        sql = f"SELECT {_COLUMNS} FROM history"
        args: List[Any] = []
        if since is not None:
            sql += " WHERE ts > ?"
            args.append(since)
        sql += " ORDER BY ts DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [_point(r) for r in reversed(rows)]

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
    state = ControllerState(
        mode=Mode(r[1]),
        profile=r[2],
        setpoint_f=r[3],
        temps_f=temps,
        valves_pct={"dephlegmator": r[8], "condenser": r[9]},
        pid_terms=(r[10], r[11], r[12]),
        pid_gains=(r[13], r[14], r[15]),
        fault=r[16],
    )
    return HistoryPoint(r[0], state)
