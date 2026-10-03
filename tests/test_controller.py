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


def test_entering_manual_from_auto_holds_positions():
    controller, hw, _ = make_controller()
    for _ in range(3):
        auto = controller.tick()
    assert auto.valves_pct["dephlegmator"] < FAILSAFE_PERCENT
    controller.set_mode(Mode.MANUAL)
    held = controller.tick()
    assert held.valves_pct["dephlegmator"] == auto.valves_pct["dephlegmator"]
    assert held.valves_pct["condenser"] == auto.valves_pct["condenser"]
    assert held.valves_pct["supply"] == auto.valves_pct["supply"]
    # and they stay put
    assert controller.tick().valves_pct == held.valves_pct


def test_entering_manual_from_off_holds_failsafe_positions():
    controller, hw, _ = make_controller()
    controller.tick()
    controller.set_mode(Mode.OFF)
    controller.tick()
    controller.set_mode(Mode.MANUAL)
    state = controller.tick()
    assert state.valves_pct["dephlegmator"] == FAILSAFE_PERCENT
    assert state.valves_pct["condenser"] == FAILSAFE_PERCENT


def test_manual_valve_moves_only_the_one_set():
    controller, hw, _ = make_controller()
    for _ in range(3):
        auto = controller.tick()
    controller.set_mode(Mode.MANUAL)
    controller.set_manual_valve("dephlegmator", 42)
    state = controller.tick()
    assert state.valves_pct["dephlegmator"] == 42
    assert state.valves_pct["supply"] == auto.valves_pct["supply"]


def test_leaving_manual_then_reentering_reseeds_from_current_positions():
    controller, hw, _ = make_controller()
    controller.tick()
    controller.set_mode(Mode.MANUAL)
    controller.set_manual_valve("dephlegmator", 42)
    controller.tick()
    controller.set_mode(Mode.OFF)
    controller.tick()
    controller.set_mode(Mode.MANUAL)
    assert controller.tick().valves_pct["dephlegmator"] == FAILSAFE_PERCENT


def test_output_limits_are_applied_and_reported():
    controller, hw, config = make_controller()
    default = tuple(config.profiles["whiskey"].output_limits)
    assert controller.state().output_limits == default
    controller.set_output_limits(50, 60)
    assert controller.state().output_limits == (50, 60)
    for _ in range(5):
        deph = controller.tick().valves_pct["dephlegmator"]
        assert 50 <= deph <= 60


def test_supply_output_limits_are_applied_and_reported():
    controller, hw, config = make_controller()
    assert controller.state().supply_output_limits == tuple(config.supply.output_limits)
    controller.set_supply_output_limits(10, 20)
    assert controller.state().supply_output_limits == (10, 20)
    for _ in range(5):
        assert 10 <= controller.tick().valves_pct["supply"] <= 20


@pytest.mark.parametrize("low,high", [(50, 50), (60, 50), (-1, 50), (0, 101)])
def test_invalid_output_limits_are_rejected(low, high):
    controller, hw, _ = make_controller()
    before = controller.state().output_limits
    with pytest.raises(ValueError):
        controller.set_output_limits(low, high)
    with pytest.raises(ValueError):
        controller.set_supply_output_limits(low, high)
    assert controller.state().output_limits == before


def test_output_limits_are_per_profile():
    controller, hw, config = make_controller("whiskey")
    controller.set_output_limits(50, 60)
    controller.set_profile("gin")
    assert controller.state().output_limits == tuple(config.profiles["gin"].output_limits)
    controller.set_profile("whiskey")
    assert controller.state().output_limits == (50, 60)


def test_saved_values_are_defaults_with_changes_on_top():
    controller, hw, config = make_controller()
    default = config.profiles["whiskey"]
    saved = controller.saved_profile("whiskey")
    assert saved["setpoint_f"] == default.setpoint_f
    assert saved["pid"] == {"p": default.pid.p, "i": default.pid.i, "d": default.pid.d}
    assert saved["output_limits"] == list(default.output_limits)

    controller.set_setpoint(125.0)
    controller.set_pid_gains(-2.0, -0.5, 0.1)
    controller.set_output_limits(35, 95)
    saved = controller.saved_profile("whiskey")
    assert saved == {"setpoint_f": 125.0, "pid": {"p": -2.0, "i": -0.5, "d": 0.1}, "output_limits": [35, 95]}
    assert controller.saved_profile("gin")["setpoint_f"] == config.profiles["gin"].setpoint_f

    controller.set_supply_setpoint(80.0)
    assert controller.saved_supply()["setpoint_f"] == 80.0


def test_set_setpoint_of_an_inactive_profile_leaves_the_active_one_alone():
    controller, hw, config = make_controller("whiskey")
    controller.set_profile_setpoint("gin", 118.0)
    assert controller.state().setpoint_f == config.profiles["whiskey"].setpoint_f
    assert controller.saved_profile("gin")["setpoint_f"] == 118.0
    controller.set_profile("gin")
    assert controller.state().setpoint_f == 118.0
    with pytest.raises(KeyError):
        controller.set_profile_setpoint("nope", 100.0)


def test_set_setpoint_of_the_active_profile_applies_now():
    controller, hw, _ = make_controller("whiskey")
    controller.set_profile_setpoint("whiskey", 127.0)
    assert controller.state().setpoint_f == 127.0


def test_reset_active_profile_applies_defaults_and_keeps_mode():
    controller, hw, config = make_controller("whiskey")
    default = config.profiles["whiskey"]
    controller.set_setpoint(120.0)
    controller.set_pid_gains(-3.0, -1.0, 0.5)
    controller.set_output_limits(50, 60)
    controller.set_mode(Mode.MANUAL)
    controller.reset_profile("whiskey")
    s = controller.state()
    assert s.mode == Mode.MANUAL
    assert s.setpoint_f == default.setpoint_f
    assert s.pid_gains == (default.pid.p, default.pid.i, default.pid.d)
    assert s.output_limits == tuple(default.output_limits)
    assert controller.saved_profile("whiskey")["setpoint_f"] == default.setpoint_f


def test_reset_inactive_profile_does_not_touch_the_running_pid():
    controller, hw, config = make_controller("whiskey")
    controller.set_setpoint(125.0)
    controller.set_profile_setpoint("gin", 111.0)
    controller.reset_profile("gin")
    assert controller.state().setpoint_f == 125.0
    assert controller.saved_profile("gin")["setpoint_f"] == config.profiles["gin"].setpoint_f
    with pytest.raises(KeyError):
        controller.reset_profile("nope")


def test_reset_supply_restores_defaults():
    controller, hw, config = make_controller()
    sup = config.supply
    controller.set_supply_setpoint(70.0)
    controller.set_supply_pid_gains(-5.0, -1.0, 0.0)
    controller.set_supply_output_limits(10, 20)
    controller.reset_supply()
    s = controller.state()
    assert s.supply_setpoint_f == sup.setpoint_f
    assert s.supply_pid_gains == (sup.pid.p, sup.pid.i, sup.pid.d)
    assert s.supply_output_limits == tuple(sup.output_limits)
    for _ in range(3):
        assert sup.output_limits[0] <= controller.tick().valves_pct["supply"] <= sup.output_limits[1]


def test_hardware_kind_is_reported():
    controller, hw, _ = make_controller()
    assert controller.hardware_kind == "simulated"
