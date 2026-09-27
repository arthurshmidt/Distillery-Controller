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
