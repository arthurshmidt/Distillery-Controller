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
