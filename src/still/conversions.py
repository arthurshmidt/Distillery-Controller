"""Temperature and valve-signal conversions.

Ported unchanged from original/whiskey_distillation.py and
original/supply.py (celcius_to_fahrnheit, percent_to_da). The Steinhart-Hart
thermistor conversion is not reimplemented here: it is provided by the
vendor's `widgetlords` package and used directly in hardware/widgetlords.py,
so this module never has to guess at that formula.
"""

from __future__ import annotations


def celsius_to_fahrenheit(temp_c: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return (temp_c * (9 / 5)) + 32


def percent_to_da(valve_percent: float) -> float:
    """Convert a 0-100% valve command to the 4-20 mA DA signal the valve
    controllers expect (800 = 4 mA / fully closed, 4000 = 20 mA / fully open).
    """
    return ((4000 - 800) / (100 - 0)) * valve_percent + 800
