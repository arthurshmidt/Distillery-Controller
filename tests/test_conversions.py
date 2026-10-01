from still.conversions import celsius_to_fahrenheit, percent_to_da


def test_celsius_to_fahrenheit_freezing():
    assert celsius_to_fahrenheit(0) == 32


def test_celsius_to_fahrenheit_boiling():
    assert celsius_to_fahrenheit(100) == 212


def test_percent_to_da_bounds():
    assert percent_to_da(0) == 800
    assert percent_to_da(100) == 4000


def test_percent_to_da_midpoint():
    assert percent_to_da(50) == 2400


import pytest

from still.conversions import SensorError, check_counts


def test_check_counts_accepts_normal_readings():
    assert check_counts(2048, 4095) == 2048
    assert check_counts(140, 4095) == 140


@pytest.mark.parametrize("counts", [0, 5, 4090, 4095])
def test_check_counts_rejects_open_or_shorted_sensor(counts):
    with pytest.raises(SensorError):
        check_counts(counts, 4095, "deph_return")
