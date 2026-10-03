import time

import pytest

from still.config import load_config
from still.controller import Controller, ControllerState, Mode
from still.hardware import SimulatedHW
from still.hardware.base import Temperatures
from still.store import Store


@pytest.fixture
def config():
    return load_config("config/still.yaml")


def _state(**kw):
    return ControllerState(
        mode=Mode.AUTO, profile="whiskey", setpoint_f=150.0,
        temps_f=Temperatures(1, 2, 3, 4), pid_terms=(1.0, 2.0, 3.0), pid_gains=(-1.0, -0.01, 0.0), **kw,
    )


def test_history_round_trip_and_queries():
    store = Store()
    for i in range(5):
        store.add_history(1000.0 + i, _state(fault="x" if i == 4 else None))
    pts = store.history()
    assert [p.timestamp for p in pts] == [1000.0 + i for i in range(5)]
    assert pts[0].state.temps_f == Temperatures(1, 2, 3, 4)
    assert pts[0].state.mode is Mode.AUTO and pts[4].state.fault == "x"
    assert [p.timestamp for p in store.history(limit=2)] == [1003.0, 1004.0]
    assert [p.timestamp for p in store.history(since=1002.0)] == [1003.0, 1004.0]
    assert store.history(limit=0) == []


def test_history_keeps_null_temps():
    store = Store()
    store.add_history(1.0, ControllerState())
    assert store.history()[0].state.temps_f is None


def test_prune_drops_old_rows():
    store = Store(retention_days=1)
    store.add_history(1000.0, _state())
    store.add_history(1000.0 + 2 * 86400, _state())
    store.prune(now=1000.0 + 2 * 86400)
    assert [p.timestamp for p in store.history()] == [1000.0 + 2 * 86400]


def test_settings_persist_across_reopen(tmp_path):
    path = str(tmp_path / "s.db")
    a = Store(path)
    a.set_setting("k", {"x": [1, 2]})
    a.add_history(time.time(), _state())
    a.close()
    b = Store(path)
    assert b.get_setting("k") == {"x": [1, 2]}
    assert b.get_setting("missing", 7) == 7
    assert len(b.history()) == 1


def test_controller_restores_profile_setpoint_and_gains(config, tmp_path):
    path = str(tmp_path / "s.db")
    names = list(config.profiles)
    first, second = names[0], names[1]
    c = Controller(SimulatedHW(seed=1), config, profile_name=first, store=Store(path))
    c.set_setpoint(123.0)
    c.set_pid_gains(-2.0, -0.5, 0.1)
    c.set_profile(second)
    default_second = c.state().setpoint_f

    c2 = Controller(SimulatedHW(seed=1), config, store=Store(path))
    assert c2.state().profile == second
    assert c2.state().setpoint_f == default_second
    c2.set_profile(first)
    assert c2.state().setpoint_f == 123.0
    assert c2.state().pid_gains == (-2.0, -0.5, 0.1)


def test_controller_always_starts_in_auto_and_explicit_profile_wins(config, tmp_path):
    path = str(tmp_path / "s.db")
    names = list(config.profiles)
    c = Controller(SimulatedHW(seed=1), config, profile_name=names[1], store=Store(path))
    c.set_mode(Mode.MANUAL)
    c2 = Controller(SimulatedHW(seed=1), config, profile_name=names[0], store=Store(path))
    assert c2.state().mode is Mode.AUTO
    assert c2.state().profile == names[0]


def test_unknown_saved_profile_falls_back_to_default(config):
    store = Store()
    store.set_setting("active_profile", "removed")
    store.set_setting("overrides", {"removed": {"setpoint_f": 1}})
    c = Controller(SimulatedHW(seed=1), config, store=store)
    assert c.state().profile == config.default_profile


def test_storage_failure_does_not_block_a_change(config):
    store = Store()
    c = Controller(SimulatedHW(seed=1), config, store=store)
    store.close()
    c.set_setpoint(140.0)
    assert c.state().setpoint_f == 140.0


def test_controller_restores_supply_setpoint_and_gains(config, tmp_path):
    path = str(tmp_path / "s.db")
    c = Controller(SimulatedHW(seed=1), config, store=Store(path))
    c.set_supply_setpoint(84.0)
    c.set_supply_pid_gains(-3.0, -0.2, 0.0)
    c2 = Controller(SimulatedHW(seed=1), config, store=Store(path))
    assert c2.state().supply_setpoint_f == 84.0
    assert c2.state().supply_pid_gains == (-3.0, -0.2, 0.0)


def test_history_round_trips_supply_fields():
    store = Store()
    store.add_history(
        1.0,
        _state(valves_pct={"dephlegmator": 1.0, "condenser": 2.0, "supply": 3.0},
               supply_setpoint_f=90.0, supply_pid_terms=(1.0, 2.0, 3.0), supply_pid_gains=(-1.0, -0.01, 0.0)),
    )
    s = store.history()[0].state
    assert s.valves_pct["supply"] == 3.0
    assert s.supply_setpoint_f == 90.0
    assert s.supply_pid_terms == (1.0, 2.0, 3.0)


def test_old_database_gets_supply_columns(tmp_path):
    import sqlite3

    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE history (ts REAL NOT NULL, mode TEXT NOT NULL, profile TEXT NOT NULL, "
        "setpoint_f REAL NOT NULL, deph_supply REAL, deph_return REAL, cond_supply REAL, cond_return REAL, "
        "valve_deph REAL NOT NULL, valve_cond REAL NOT NULL, term_p REAL NOT NULL, term_i REAL NOT NULL, "
        "term_d REAL NOT NULL, gain_p REAL NOT NULL, gain_i REAL NOT NULL, gain_d REAL NOT NULL, fault TEXT)"
    )
    db.execute(
        "INSERT INTO history VALUES (?,'auto','whiskey',130,1,2,3,4,50,100,0,0,0,-1,-0.01,0,NULL)",
        (time.time(),),
    )
    db.commit()
    db.close()
    store = Store(path)
    assert "supply" not in store.history()[0].state.valves_pct
    store.add_history(time.time() + 1, _state())
    assert len(store.history()) == 2


def test_controller_restores_output_limits(config, tmp_path):
    path = str(tmp_path / "s.db")
    first = list(config.profiles)[0]
    c = Controller(SimulatedHW(seed=1), config, profile_name=first, store=Store(path))
    c.set_output_limits(45, 85)
    c.set_supply_output_limits(5, 55)

    c2 = Controller(SimulatedHW(seed=1), config, profile_name=first, store=Store(path))
    assert c2.state().output_limits == (45, 85)
    assert c2.state().supply_output_limits == (5, 55)


def _fill(store, n=100, t0=1000.0, **kw):
    for i in range(n):
        store.add_history(t0 + i, _state(supply_setpoint_f=90.0, **kw))


def test_history_until_bounds_the_window():
    store = Store()
    _fill(store, 10)
    assert [p.timestamp for p in store.history(until=1003.0)] == [1000.0, 1001.0, 1002.0, 1003.0]
    assert [p.timestamp for p in store.history(since=1001.0, until=1003.0)] == [1002.0, 1003.0]
    assert [p.timestamp for p in store.history(until=1003.0, limit=2)] == [1002.0, 1003.0]


def test_history_step_keeps_the_earliest_row_per_bucket_in_order():
    store = Store()
    _fill(store, 100)
    pts = store.history(step=10)
    assert [p.timestamp for p in pts] == [1000.0 + 10 * i for i in range(10)]
    # buckets are aligned to multiples of step, so an offset window still thins evenly
    pts = store.history(since=1004.0, step=10)
    assert [p.timestamp for p in pts] == [1005.0] + [1010.0 + 10 * i for i in range(9)]
    assert len(store.history(step=1)) == 100
    assert [p.timestamp for p in store.history(step=10, limit=3)] == [1070.0, 1080.0, 1090.0]


def test_history_step_with_a_real_file_uses_the_read_connection(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    _fill(store, 30)
    assert len(store.history(step=10)) == 3
    store.add_history(2000.0, _state())  # writes still work beside reads
    assert store.history(limit=1)[0].timestamp == 2000.0


def _row(store, t, **kw):
    store.add_history(t, _state(supply_setpoint_f=kw.pop("ssp", 90.0), **kw))


def test_events_for_a_scripted_sequence():
    store = Store()
    t = 1000.0
    for i in range(5):
        _row(store, t + i)
    store.add_history(t + 5, ControllerState(
        mode=Mode.MANUAL, profile="whiskey", setpoint_f=150.0, temps_f=Temperatures(1, 2, 3, 4),
        supply_setpoint_f=90.0))
    store.add_history(t + 6, ControllerState(
        mode=Mode.MANUAL, profile="whiskey", setpoint_f=155.0, temps_f=Temperatures(1, 2, 3, 4),
        supply_setpoint_f=88.0))
    store.add_history(t + 7, ControllerState(
        mode=Mode.MANUAL, profile="gin", setpoint_f=155.0, supply_setpoint_f=88.0, fault="read error: x"))
    store.add_history(t + 8, ControllerState(
        mode=Mode.MANUAL, profile="gin", setpoint_f=155.0, supply_setpoint_f=88.0, fault="read error: x"))
    store.add_history(t + 12, ControllerState(
        mode=Mode.AUTO, profile="gin", setpoint_f=155.0, temps_f=Temperatures(1, 2, 3, 4), supply_setpoint_f=88.0))
    # a long silence, then logging resumes
    _row(store, t + 500)

    events, segments = store.events()
    summary = [(e["timestamp"] - t, e["kind"], e["text"]) for e in events]
    assert summary == [
        (0, "log", "Logging started · auto, whiskey profile"),
        (5, "mode", "Mode auto → manual"),
        (6, "setpoint", "Dephlegmator setpoint 150.0 → 155.0 °F"),
        (6, "setpoint", "Supply setpoint 90.0 → 88.0 °F"),
        (7, "profile", "Profile whiskey → gin"),
        (7, "fault", "read error: x"),
        (12, "mode", "Mode manual → auto"),
        (12, "fault", "Fault cleared after 5 s"),
        (12, "log", "Logging stopped"),
        (500, "log", "Logging started · auto, whiskey profile"),
    ]
    tones = {e["text"]: e["tone"] for e in events}
    assert tones["Mode auto → manual"] == "amber"
    assert tones["read error: x"] == "red"
    assert tones["Mode manual → auto"] is None

    kinds = [(s["start"] - t, s["end"] - t, s["kind"]) for s in segments]
    assert kinds == [(0, 5, "auto"), (5, 7, "manual"), (7, 12, "fault"), (12, 12, "auto"), (500, 500, "auto")]


def test_events_see_a_short_fault_even_when_history_is_thinned():
    store = Store()
    for i in range(1000):
        store.add_history(5000.0 + i, _state(fault="boom" if 500 <= i < 503 else None))
    assert len(store.history(step=100)) == 10
    events, _ = store.events()
    assert [e["text"] for e in events if e["kind"] == "fault"] == ["boom", "Fault cleared after 3 s"]


def test_events_window_uses_the_previous_row_for_context():
    store = Store()
    _fill(store, 5)
    store.add_history(1005.0, ControllerState(
        mode=Mode.OFF, profile="whiskey", setpoint_f=150.0, temps_f=Temperatures(1, 2, 3, 4), supply_setpoint_f=90.0))
    # the window starts at the row after the change: no "logging started" noise before it
    events, _ = store.events(since=1005.0)
    assert [(e["kind"], e["text"]) for e in events] == [("mode", "Mode auto → off")]
    # a window that ends before the change shows nothing but the start
    events, _ = store.events(since=1001.0, until=1003.0)
    assert events == []


def test_scan_chunks_do_not_drop_or_repeat_rows(monkeypatch):
    import still.store as st

    monkeypatch.setattr(st, "SCAN_CHUNK", 7)
    store = Store()
    _fill(store, 30)
    rows = list(store.rows_for_export(None, None))
    assert [r[0] for r in rows] == [1000.0 + i for i in range(30)]
    assert [r[0] for r in store.rows_for_export(1010.0, 1020.0)] == [1010.0 + i for i in range(11)]


def test_thinning_by_seeks_matches_thinning_by_grouping(monkeypatch):
    import still.store as st

    store = Store()
    for i in range(500):
        if 200 <= i < 260:
            continue  # a logging gap leaves empty buckets
        store.add_history(1000.0 + i, _state())
    window = dict(since=1010.0, until=1450.0, step=17.0)
    by_seeks = [p.timestamp for p in store.history(**window)]
    monkeypatch.setattr(st, "MAX_SEEK_BUCKETS", 0)
    by_grouping = [p.timestamp for p in store.history(**window)]
    assert by_seeks == by_grouping
    assert len(by_seeks) > 20
    assert by_seeks == sorted(set(by_seeks))
