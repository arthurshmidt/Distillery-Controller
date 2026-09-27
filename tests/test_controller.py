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
    for _ in range(300):
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
    assert state.fault == "off"


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
