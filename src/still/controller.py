"""The dephlegmator PID control loop.

Each call to `Controller.tick()` is one iteration of what the original
scripts (original/whiskey_distillation.py, original/gin_distillation.py) did
in their `while True:` loop: read temperatures, run the PID, command the
valves, and record state for the API to read.

The condenser is held fully open (CONDENSER_PERCENT) rather than run under
its own PID -- see PLAN.md. FAILSAFE_PERCENT (both valves fully open) matches
the position the original scripts commanded at startup, and is used here for
startup, shutdown, manual/off modes, and sensor faults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Callable, Dict, Optional

from simple_pid import PID

from .config import AppConfig, ProfileConfig
from .hardware.base import HardwareInterface, Temperatures

logger = logging.getLogger(__name__)

FAILSAFE_PERCENT = 100.0
CONDENSER_PERCENT = 100.0

# A reading outside this range means a bad thermistor channel, not a real
# temperature, so the controller fails safe rather than acting on it.
SANE_TEMP_RANGE_F = (-40.0, 300.0)


class Mode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    OFF = "off"


@dataclass
class ControllerState:
    mode: Mode = Mode.OFF
    profile: str = ""
    setpoint_f: float = 0.0
    temps_f: Optional[Temperatures] = None
    valves_pct: Dict[str, float] = field(
        default_factory=lambda: {"dephlegmator": FAILSAFE_PERCENT, "condenser": CONDENSER_PERCENT}
    )
    pid_terms: tuple = (0.0, 0.0, 0.0)
    fault: Optional[str] = None


class Controller:
    """Thread-safety: `tick()` is meant to be called from a single control
    thread. `state()`, `set_setpoint()`, `set_mode()`, `set_profile()` and
    `set_manual_valve()` may be called from other threads (e.g. the API).
    """

    def __init__(
        self,
        hw: HardwareInterface,
        config: AppConfig,
        profile_name: Optional[str] = None,
        pid_time_fn: Optional[Callable[[], float]] = None,
    ):
        self._hw = hw
        self._config = config
        self._pid_time_fn = pid_time_fn
        self._lock = Lock()
        self._pid: Optional[PID] = None
        self._manual_valves: Dict[str, float] = {}
        self._state = ControllerState()
        self.set_profile(profile_name or config.default_profile)
        self.failsafe("startup", is_fault=False)

    # -- commands, safe to call from any thread ---------------------------

    def set_profile(self, name: str) -> None:
        profile = self._config.profiles[name]
        with self._lock:
            self._pid = self._build_pid(profile)
            self._state.profile = name
            self._state.setpoint_f = profile.setpoint_f
            self._state.mode = Mode.AUTO

    def set_setpoint(self, setpoint_f: float) -> None:
        with self._lock:
            self._pid.setpoint = setpoint_f
            self._state.setpoint_f = setpoint_f

    def set_mode(self, mode: Mode) -> None:
        with self._lock:
            self._state.mode = mode
            if mode != Mode.MANUAL:
                self._manual_valves.clear()

    def set_manual_valve(self, name: str, percent: float) -> None:
        with self._lock:
            if self._state.mode != Mode.MANUAL:
                raise ValueError("a valve can only be set by hand in manual mode")
            self._manual_valves[name] = max(0.0, min(100.0, percent))

    def state(self) -> ControllerState:
        with self._lock:
            return self._copy_state()

    # -- the control loop ---------------------------------------------------

    def tick(self) -> ControllerState:
        """Run one control iteration and return the resulting state."""
        try:
            temps = self._hw.read_temperatures()
        except Exception as exc:
            logger.exception("failed to read temperatures")
            return self.failsafe(f"read error: {exc}")

        if not self._is_sane(temps):
            return self.failsafe(f"temperature reading out of range: {temps}")

        with self._lock:
            mode = self._state.mode

        if mode == Mode.OFF:
            return self.failsafe("off")
        if mode == Mode.MANUAL:
            return self._apply_manual(temps)
        return self._apply_auto(temps)

    def failsafe(self, reason: str, *, is_fault: bool = True) -> ControllerState:
        if is_fault:
            logger.warning("failsafe: %s", reason)
        valves = {"dephlegmator": FAILSAFE_PERCENT, "condenser": FAILSAFE_PERCENT}
        self._write_valves(valves)
        try:
            temps = self._hw.read_temperatures()
        except Exception:
            temps = None
        return self._record(temps, valves, (0.0, 0.0, 0.0), fault=reason if is_fault else None)

    def _apply_auto(self, temps: Temperatures) -> ControllerState:
        with self._lock:
            deph_pct = self._pid(temps.deph_return)
            p, i, d = self._pid.components
        valves = {"dephlegmator": deph_pct, "condenser": CONDENSER_PERCENT}
        self._write_valves(valves)
        return self._record(temps, valves, (p, i, d), fault=None)

    def _apply_manual(self, temps: Temperatures) -> ControllerState:
        with self._lock:
            valves = {
                "dephlegmator": self._manual_valves.get("dephlegmator", FAILSAFE_PERCENT),
                "condenser": self._manual_valves.get("condenser", CONDENSER_PERCENT),
            }
        self._write_valves(valves)
        return self._record(temps, valves, (0.0, 0.0, 0.0), fault=None)

    # -- helpers -------------------------------------------------------------

    def _build_pid(self, profile: ProfileConfig) -> PID:
        pid = PID(
            profile.pid.p,
            profile.pid.i,
            profile.pid.d,
            setpoint=profile.setpoint_f,
            time_fn=self._pid_time_fn,
        )
        pid.sample_time = profile.sample_time
        pid.output_limits = tuple(profile.output_limits)
        return pid

    def _write_valves(self, valves: Dict[str, float]) -> None:
        for name, percent in valves.items():
            self._hw.write_valve(name, percent)

    def _record(
        self,
        temps: Optional[Temperatures],
        valves: Dict[str, float],
        pid_terms: tuple,
        fault: Optional[str],
    ) -> ControllerState:
        with self._lock:
            self._state.temps_f = temps
            self._state.valves_pct = dict(valves)
            self._state.pid_terms = pid_terms
            self._state.fault = fault
            return self._copy_state()

    def _copy_state(self) -> ControllerState:
        s = self._state
        return ControllerState(
            mode=s.mode,
            profile=s.profile,
            setpoint_f=s.setpoint_f,
            temps_f=s.temps_f,
            valves_pct=dict(s.valves_pct),
            pid_terms=s.pid_terms,
            fault=s.fault,
        )

    @staticmethod
    def _is_sane(temps: Temperatures) -> bool:
        lo, hi = SANE_TEMP_RANGE_F
        return all(
            lo <= t <= hi
            for t in (temps.deph_supply, temps.deph_return, temps.cond_supply, temps.cond_return)
        )
