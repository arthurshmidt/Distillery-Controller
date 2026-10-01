"""The hardware interface the controller depends on.

Two implementations: `SimulatedHW` (simulated.py) for development and tests
off the Raspberry Pi, and `WidgetlordsHW` (widgetlords.py) for the real
PI-SPI-DIN boards.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

VALVE_NAMES = ("dephlegmator", "condenser", "supply")


@dataclass(frozen=True)
class Temperatures:
    """The still's four thermistor readings, in Fahrenheit."""

    deph_supply: float
    deph_return: float
    cond_supply: float
    cond_return: float


class HardwareInterface(ABC):
    """What the controller needs from the still hardware."""

    @abstractmethod
    def read_temperatures(self) -> Temperatures:
        """Read and convert all four thermistor channels, in Fahrenheit."""

    @abstractmethod
    def write_valve(self, name: str, percent: float) -> None:
        """Command a valve (one of VALVE_NAMES) to a position, 0-100%."""

    def close(self) -> None:
        """Release hardware resources. No-op unless overridden."""
        return None
