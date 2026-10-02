from pathlib import Path

import pytest

from still.config import load_config
from still.controller import FAILSAFE_PERCENT, Controller, Mode
from still.hardware.base import Temperatures
from still.hardware.simulated import SimulatedHW

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "still.yaml"


class FakeClock:
    """Advances by a fixed step each call, so simple_pid's sample_time gate
    doesn't force tests to wait on real wall-clock time."""

    def __init__(self, step: float = 1.0):
        self._t = 0.0
        self._step = step

    def __call__(self) -> float:
        self._t += self._step
        return self._t


def make_controller(profile="whiskey"):
    config = load_config(CONFIG_PATH)
    hw = SimulatedHW(seed=1)
    controller = Controller(hw, config, profile_name=profile, pid_time_fn=FakeClock())
    return controller, hw, config


def test_starts_in_failsafe():
    controller, hw, _ = make_controller()
    state = controller.state()
    assert state.valves_pct["dephlegmator"] == FAILSAFE_PERCENT
    assert state.valves_pct["condenser"] == FAILSAFE_PERCENT
    assert state.fault is None  # startup failsafe is not reported as a fault


def test_auto_mode_converges_toward_setpoint():
    controller, hw, config = make_controller("whiskey")
    state = None
    for _ in range(600):
        state = controller.tick()

    setpoint = config.profiles["whiskey"].setpoint_f
    assert abs(state.temps_f.deph_return - setpoint) < 5
    lo, hi = config.profiles["whiskey"].output_limits
    assert lo <= state.valves_pct["dephlegmator"] <= hi


def test_condenser_is_always_held_open():
    controller, hw, _ = make_controller()
    for _ in range(10):
        state = controller.tick()
    assert state.valves_pct["condenser"] == 100.0


def test_off_mode_forces_failsafe():
    controller, hw, _ = make_controller()
    controller.tick()
    controller.set_mode(Mode.OFF)
    state = controller.tick()
    assert state.valves_pct["dephlegmator"] == FAILSAFE_PERCENT
    assert state.valves_pct["condenser"] == FAILSAFE_PERCENT
    assert state.fault is None  # off is a chosen state, not a fault


def test_manual_valve_requires_manual_mode():
    controller, hw, _ = make_controller()
    with pytest.raises(ValueError):
        controller.set_manual_valve("dephlegmator", 50)

    controller.set_mode(Mode.MANUAL)
    controller.set_manual_valve("dephlegmator", 55)
    state = controller.tick()
    assert state.valves_pct["dephlegmator"] == 55


def test_switching_out_of_manual_clears_manual_valves():
    controller, hw, _ = make_controller()
    controller.set_mode(Mode.MANUAL)
    controller.set_manual_valve("dephlegmator", 55)
    controller.set_mode(Mode.AUTO)
    with pytest.raises(ValueError):
        controller.set_manual_valve("dephlegmator", 55)


def test_sensor_fault_triggers_failsafe(monkeypatch):
    controller, hw, _ = make_controller()
    controller.tick()

    def bad_read():
        return Temperatures(deph_supply=999, deph_return=999, cond_supply=999, cond_return=999)

    monkeypatch.setattr(hw, "read_temperatures", bad_read)
    state = controller.tick()
    assert state.fault is not None
    assert state.valves_pct["dephlegmator"] == FAILSAFE_PERCENT


def test_switching_profile_changes_pid_and_setpoint():
    controller, hw, config = make_controller("whiskey")
    controller.set_profile("gin")
    state = controller.state()
    assert state.profile == "gin"
    assert state.setpoint_f == config.profiles["gin"].setpoint_f
    for _ in range(300):
        state = controller.tick()
    lo, hi = config.profiles["gin"].output_limits
    assert lo <= state.valves_pct["dephlegmator"] <= hi


def test_switching_profile_keeps_mode():
    controller, hw, _ = make_controller("whiskey")
    controller.set_mode(Mode.OFF)
    controller.set_profile("gin")
    assert controller.state().mode == Mode.OFF

    controller.set_mode(Mode.MANUAL)
    controller.set_manual_valve("dephlegmator", 55)
    controller.set_profile("whiskey")
    assert controller.state().mode == Mode.MANUAL
    assert controller.tick().valves_pct["dephlegmator"] == 55


def test_read_error_triggers_failsafe(monkeypatch):
    controller, hw, _ = make_controller()
    controller.tick()

    def broken_read():
        raise OSError("spi bus error")

    monkeypatch.setattr(hw, "read_temperatures", broken_read)
    state = controller.tick()
    assert "read error" in state.fault
    assert state.valves_pct["dephlegmator"] == FAILSAFE_PERCENT
    assert state.temps_f is None


def test_nan_reading_triggers_failsafe(monkeypatch):
    controller, hw, _ = make_controller()
    nan = float("nan")
    monkeypatch.setattr(
        hw,
        "read_temperatures",
        lambda: Temperatures(deph_supply=nan, deph_return=nan, cond_supply=nan, cond_return=nan),
    )
    assert controller.tick().fault is not None


def test_fault_clears_when_readings_recover(monkeypatch):
    controller, hw, _ = make_controller()
    good_read = hw.read_temperatures

    monkeypatch.setattr(
        hw,
        "read_temperatures",
        lambda: Temperatures(deph_supply=999, deph_return=999, cond_supply=999, cond_return=999),
    )
    assert controller.tick().fault is not None

    monkeypatch.setattr(hw, "read_temperatures", good_read)
    assert controller.tick().fault is None


def test_failsafe_does_not_reread_sensors(monkeypatch):
    controller, hw, _ = make_controller()
    reads = []
    real_read = hw.read_temperatures
    monkeypatch.setattr(hw, "read_temperatures", lambda: reads.append(1) or real_read())
    controller.set_mode(Mode.OFF)
    controller.tick()
    assert len(reads) == 1


def test_failsafe_commands_both_valves_when_one_write_fails(monkeypatch):
    controller, hw, _ = make_controller()
    written = []
    real_write = hw.write_valve

    def flaky_write(name, percent):
        if name == "dephlegmator":
            raise OSError("spi bus error")
        written.append(name)
        real_write(name, percent)

    monkeypatch.setattr(hw, "write_valve", flaky_write)
    state = controller.failsafe("test")
    assert written == ["condenser"]
    assert "dephlegmator valve write failed" in state.fault


def test_valve_write_error_in_auto_triggers_failsafe(monkeypatch):
    controller, hw, _ = make_controller()
    real_write = hw.write_valve
    calls = {"n": 0}

    def write(name, percent):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("spi bus error")
        real_write(name, percent)

    monkeypatch.setattr(hw, "write_valve", write)
    state = controller.tick()
    assert "write error" in state.fault
    assert state.valves_pct["dephlegmator"] == FAILSAFE_PERCENT


def test_supply_loop_holds_bath_at_setpoint():
    controller, hw, config = make_controller()
    state = None
    for _ in range(600):
        state = controller.tick()
    assert abs(state.temps_f.deph_supply - config.supply.setpoint_f) < 3
    lo, hi = config.supply.output_limits
    assert lo <= state.valves_pct["supply"] <= hi


def test_supply_valve_not_commanded_until_first_auto_tick():
    controller, hw, _ = make_controller()
    assert "supply" not in controller.state().valves_pct
    assert controller.tick().valves_pct["supply"] is not None


def test_failsafe_holds_supply_valve(monkeypatch):
    controller, hw, _ = make_controller()
    for _ in range(20):
        state = controller.tick()
    held = state.valves_pct["supply"]
    written = []
    real_write = hw.write_valve
    monkeypatch.setattr(hw, "write_valve", lambda n, p: written.append(n) or real_write(n, p))

    controller.set_mode(Mode.OFF)
    state = controller.tick()
    assert state.valves_pct["supply"] == held
    assert state.valves_pct["dephlegmator"] == FAILSAFE_PERCENT
    assert "supply" not in written


def test_supply_setpoint_and_gains_are_applied():
    controller, hw, _ = make_controller()
    controller.set_supply_setpoint(80.0)
    controller.set_supply_pid_gains(-2.0, -0.5, 0.1)
    s = controller.state()
    assert s.supply_setpoint_f == 80.0
    assert s.supply_pid_gains == (-2.0, -0.5, 0.1)


def test_manual_supply_valve():
    controller, hw, _ = make_controller()
    controller.set_mode(Mode.MANUAL)
    controller.set_manual_valve("supply", 33)
    assert controller.tick().valves_pct["supply"] == 33
    controller.set_mode(Mode.AUTO)
    assert controller.tick().valves_pct["supply"] != 33
