from still.hardware.simulated import SimulatedHW


def _settle(hw, n=50):
    reading = None
    for _ in range(n):
        reading = hw.read_temperatures()
    return reading


def test_opening_dephlegmator_valve_cools_return_temp():
    closed = SimulatedHW(seed=1, noise_f=0.0)
    closed.write_valve("dephlegmator", 30)
    hot = _settle(closed).deph_return

    open_ = SimulatedHW(seed=1, noise_f=0.0)
    open_.write_valve("dephlegmator", 100)
    cool = _settle(open_).deph_return

    assert cool < hot


def test_write_valve_rejects_unknown_name():
    hw = SimulatedHW(seed=1)
    try:
        hw.write_valve("nope", 50)
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown valve name")


def test_injected_open_or_shorted_fault_raises_sensor_error():
    import pytest
    from still.conversions import SensorError

    hw = SimulatedHW(seed=1)
    for kind in ("open", "shorted"):
        hw.set_fault("deph_return", kind)
        with pytest.raises(SensorError):
            hw.read_temperatures()
    hw.clear_fault()
    hw.read_temperatures()  # reads normally again


def test_injected_nan_fault_affects_only_that_sensor():
    import math

    hw = SimulatedHW(seed=1)
    hw.set_fault("cond_return", "nan")
    t = hw.read_temperatures()
    assert math.isnan(t.cond_return)
    assert not math.isnan(t.deph_return)
    hw.clear_fault()
    assert not math.isnan(hw.read_temperatures().cond_return)


def test_set_fault_rejects_unknown_sensor_and_kind():
    import pytest

    hw = SimulatedHW(seed=1)
    with pytest.raises(ValueError):
        hw.set_fault("nope", "open")
    with pytest.raises(ValueError):
        hw.set_fault("deph_return", "nope")
