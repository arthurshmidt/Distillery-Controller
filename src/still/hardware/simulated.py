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

from .base import VALVE_NAMES, HardwareInterface, Temperatures


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

        return Temperatures(
            deph_supply=self._bath_f + noise(),
            deph_return=self._deph_return_f + noise(),
            cond_supply=self._bath_f + noise(),
            cond_return=self._cond_return_f + noise(),
        )

    def write_valve(self, name: str, percent: float) -> None:
        if name not in VALVE_NAMES:
            raise ValueError(f"unknown valve {name!r}")
        self._valves[name] = max(0.0, min(100.0, percent))
