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
