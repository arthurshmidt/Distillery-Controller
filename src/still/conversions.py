"""Temperature and valve-signal conversions.

celsius_to_fahrenheit and percent_to_da are ported unchanged from
original/whiskey_distillation.py and original/supply.py (celcius_to_fahrnheit,
percent_to_da); check_counts and SensorError are new. The Steinhart-Hart
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


# A thermistor channel reading this close to either end of the ADC range is an
# open or shorted sensor, not a temperature. Real readings stay well inside it:
# at 150 C the 10k thermistor still reads ~140 counts from the rail.
COUNTS_MARGIN = 10


class SensorError(RuntimeError):
    """A thermistor channel returned a raw count outside its valid range."""


def check_counts(counts: float, adc_max: float, channel: str = "") -> float:
    """Return `counts` unchanged, or raise SensorError if it is within
    COUNTS_MARGIN of either end of the ADC range (open or shorted sensor)."""
    if not (COUNTS_MARGIN <= counts <= adc_max - COUNTS_MARGIN):
        raise SensorError(f"{channel or 'channel'} raw reading {counts} is out of range (open or shorted sensor?)")
    return counts
