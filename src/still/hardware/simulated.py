"""A rough thermal model for development and tests, off the Raspberry Pi.

This is not a physical simulation of the still. It just moves each loop's
return temperature toward a target that falls as the matching valve opens
further, so a PID loop run against it behaves qualitatively like the real
still: opening a valve increases coolant flow and cools the loop, closing it
lets the loop heat back up. The supply valve does the same to the shared
water bath, whose temperature is both loops' supply reading. That is enough
to exercise the controller logic end-to-end without the Pi or the widgetlords
boards.
"""

from __future__ import annotations

import random
from typing import Optional

from ..conversions import SensorError
from .base import VALVE_NAMES, HardwareInterface, Temperatures

SENSOR_NAMES = ("deph_supply", "deph_return", "cond_supply", "cond_return")
FAULT_KINDS = ("open", "shorted", "nan")


class SimulatedHW(HardwareInterface):
    kind = "simulated"

    def __init__(
        self,
        *,
        ambient_f: float = 70.0,
        noise_f: float = 0.1,
        response: float = 0.2,
        seed: Optional[int] = None,
    ) -> None:
        """
        ambient_f: starting temperature for both loops.
        noise_f: +/- amplitude of random measurement noise.
        response: how fast (0-1) each return temp moves toward its target
            per read_temperatures() call; smaller is slower/more damped.
        """
        self._rng = random.Random(seed)
        self._noise_f = noise_f
        self._response = response
        self._valves = {name: 100.0 for name in VALVE_NAMES}
        self._valves["supply"] = 50.0
        self._bath_f = ambient_f
        self._deph_return_f = ambient_f
        self._cond_return_f = ambient_f
        self._fault: Optional[tuple] = None  # (sensor, kind) while a fault is injected

    def set_fault(self, sensor: str, kind: str) -> None:
        """Pretend a thermistor has failed, until clear_fault().

        "open" and "shorted" raise SensorError from read_temperatures(), as
        WidgetlordsHW's check_counts() does for the whole read. "nan" returns
        NaN for that one sensor instead.
        """
        if sensor not in SENSOR_NAMES:
            raise ValueError(f"unknown sensor {sensor!r}")
        if kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind {kind!r}")
        self._fault = (sensor, kind)

    def clear_fault(self) -> None:
        self._fault = None

    @property
    def fault(self) -> Optional[tuple]:
        return self._fault

    @staticmethod
    def _target_f(valve_percent: float) -> float:
        # Linear stand-in for "more open == more cooling == lower temp".
        return 220.0 - 1.2 * valve_percent

    @staticmethod
    def _bath_target_f(supply_percent: float) -> float:
        # More city water == cooler bath. 50% holds about 90 F.
        return 130.0 - 0.8 * supply_percent

    def read_temperatures(self) -> Temperatures:
        deph_target = self._target_f(self._valves["dephlegmator"])
        cond_target = self._target_f(self._valves["condenser"])
        self._deph_return_f += (deph_target - self._deph_return_f) * self._response
        self._cond_return_f += (cond_target - self._cond_return_f) * self._response
        bath_target = self._bath_target_f(self._valves["supply"])
        self._bath_f += (bath_target - self._bath_f) * self._response

        def noise() -> float:
            return self._rng.uniform(-self._noise_f, self._noise_f)

        readings = {
            "deph_supply": self._bath_f + noise(),
            "deph_return": self._deph_return_f + noise(),
            "cond_supply": self._bath_f + noise(),
            "cond_return": self._cond_return_f + noise(),
        }
        fault = self._fault
        if fault is not None:
            sensor, kind = fault
            if kind == "nan":
                readings[sensor] = float("nan")
            else:
                raise SensorError(f"{sensor} raw reading is out of range ({kind} sensor, injected)")
        return Temperatures(**readings)

    def write_valve(self, name: str, percent: float) -> None:
        if name not in VALVE_NAMES:
            raise ValueError(f"unknown valve {name!r}")
        self._valves[name] = max(0.0, min(100.0, percent))
