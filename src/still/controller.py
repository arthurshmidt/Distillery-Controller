"""The still's control loops: dephlegmator, condenser and supply.

Each call to `Controller.tick()` is one iteration of what the original
scripts (original/whiskey_distillation.py, original/gin_distillation.py) did
in their `while True:` loop: read temperatures, run the PID, command the
valves, and record state for the API to read.

City water feeds the supply valve into the water bath, which also receives
the warm return water. The supply PID (original/supply.py) holds the bath
outlet temperature (`deph_supply`), which is the inlet temperature of both the
dephlegmator and the condenser; the dephlegmator PID then holds its return
temperature.

The condenser is held fully open (CONDENSER_PERCENT) rather than run under
its own PID -- see PLAN.md. FAILSAFE_PERCENT (dephlegmator and condenser fully
open) matches the position the original scripts commanded at startup, and is
used here for startup, shutdown, off mode, and sensor faults. The
supply valve is deliberately not moved by the failsafe: it holds its last
position. Entering manual mode holds all valves where they were last
commanded; they move only when the operator sets one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING, Callable, Dict, Optional

from simple_pid import PID

from .config import AppConfig, ProfileConfig
from .hardware.base import HardwareInterface, Temperatures

if TYPE_CHECKING:
    from .store import Store

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
    pid_gains: tuple = (0.0, 0.0, 0.0)
    supply_setpoint_f: float = 0.0
    supply_pid_terms: tuple = (0.0, 0.0, 0.0)
    supply_pid_gains: tuple = (0.0, 0.0, 0.0)
    output_limits: tuple = (0.0, 100.0)
    supply_output_limits: tuple = (0.0, 100.0)
    fault: Optional[str] = None


def _merged(setpoint_f: float, pid, output_limits, override: dict) -> dict:
    p, i, d = override.get("pid") or (pid.p, pid.i, pid.d)
    return {
        "setpoint_f": override.get("setpoint_f", setpoint_f),
        "pid": {"p": p, "i": i, "d": d},
        "output_limits": list(override.get("output_limits") or output_limits),
    }


class Controller:
    """Thread-safety: `tick()` is meant to be called from a single control
    thread. `state()`, `set_setpoint()`, `set_mode()`, `set_profile()` and
    `set_manual_valve()`, `set_pid_gains()`, `set_supply_setpoint()` and
    `set_supply_pid_gains()` may be called from other threads (e.g. the API).
    """

    def __init__(
        self,
        hw: HardwareInterface,
        config: AppConfig,
        profile_name: Optional[str] = None,
        pid_time_fn: Optional[Callable[[], float]] = None,
        store: Optional["Store"] = None,
    ):
        """With a `store`, the active profile, each profile's setpoint and
        gains, and the supply setpoint and gains are saved on change and
        restored here. An explicit `profile_name`
        wins over the saved one. The mode is never restored: the controller
        always starts in auto."""
        self._hw = hw
        self._config = config
        self._pid_time_fn = pid_time_fn
        self._store = store
        self._overrides: Dict[str, dict] = {}
        self._supply_override: dict = {}
        saved_profile = None
        if store is not None:
            saved_supply = store.get_setting("supply_override", {})
            if isinstance(saved_supply, dict):
                self._supply_override = saved_supply
            saved = store.get_setting("overrides", {})
            if isinstance(saved, dict):
                self._overrides = {k: v for k, v in saved.items() if k in config.profiles and isinstance(v, dict)}
            saved_profile = store.get_setting("active_profile")
            if saved_profile not in config.profiles:
                saved_profile = None
        self._lock = Lock()
        self._pid: Optional[PID] = None
        self._supply_pid: PID = self._build_supply_pid()
        self._supply_last: Optional[float] = None  # last supply position written
        self._manual_valves: Dict[str, float] = {}
        self._state = ControllerState(
            supply_setpoint_f=self._supply_pid.setpoint,
            supply_pid_gains=self._supply_pid.tunings,
            supply_output_limits=self._supply_pid.output_limits,
        )
        self.set_profile(profile_name or saved_profile or config.default_profile)
        self.set_mode(Mode.AUTO)
        self.failsafe("startup", is_fault=False)

    # -- commands, safe to call from any thread ---------------------------

    def set_profile(self, name: str) -> None:
        profile = self._config.profiles[name]
        with self._lock:
            self._pid = self._build_pid(profile, self._overrides.get(name, {}))
            self._state.profile = name
            self._state.setpoint_f = self._pid.setpoint
            self._state.pid_gains = self._pid.tunings
            self._state.output_limits = self._pid.output_limits
        self._save("active_profile", name)

    def set_setpoint(self, setpoint_f: float) -> None:
        with self._lock:
            self._pid.setpoint = setpoint_f
            self._state.setpoint_f = setpoint_f
            self._overrides.setdefault(self._state.profile, {})["setpoint_f"] = setpoint_f
            overrides = self._snapshot_overrides()
        self._save("overrides", overrides)

    def set_pid_gains(self, p: float, i: float, d: float) -> None:
        with self._lock:
            self._pid.tunings = (p, i, d)
            self._state.pid_gains = (p, i, d)
            self._overrides.setdefault(self._state.profile, {})["pid"] = [p, i, d]
            overrides = self._snapshot_overrides()
        self._save("overrides", overrides)

    def set_supply_setpoint(self, setpoint_f: float) -> None:
        with self._lock:
            self._supply_pid.setpoint = setpoint_f
            self._state.supply_setpoint_f = setpoint_f
            self._supply_override["setpoint_f"] = setpoint_f
            override = dict(self._supply_override)
        self._save("supply_override", override)

    def set_supply_pid_gains(self, p: float, i: float, d: float) -> None:
        with self._lock:
            self._supply_pid.tunings = (p, i, d)
            self._state.supply_pid_gains = (p, i, d)
            self._supply_override["pid"] = [p, i, d]
            override = dict(self._supply_override)
        self._save("supply_override", override)

    def set_profile_setpoint(self, name: str, setpoint_f: float) -> None:
        """Save a profile's setpoint whether or not it is the active one."""
        if name not in self._config.profiles:
            raise KeyError(name)
        with self._lock:
            active = name == self._state.profile
        if active:
            self.set_setpoint(setpoint_f)
            return
        with self._lock:
            self._overrides.setdefault(name, {})["setpoint_f"] = setpoint_f
            overrides = self._snapshot_overrides()
        self._save("overrides", overrides)

    def reset_profile(self, name: str) -> None:
        """Forget the saved setpoint, gains and limits of a profile, returning it
        to the config file's values. If it is active its PID is rebuilt now."""
        if name not in self._config.profiles:
            raise KeyError(name)
        with self._lock:
            self._overrides.pop(name, None)
            overrides = self._snapshot_overrides()
            active = name == self._state.profile
        self._save("overrides", overrides)
        if active:
            self.set_profile(name)

    def reset_supply(self) -> None:
        """Forget the supply loop's saved setpoint, gains and limits."""
        with self._lock:
            self._supply_override = {}
            self._supply_pid = self._build_supply_pid()
            self._state.supply_setpoint_f = self._supply_pid.setpoint
            self._state.supply_pid_gains = self._supply_pid.tunings
            self._state.supply_output_limits = self._supply_pid.output_limits
        self._save("supply_override", {})

    def saved_profile(self, name: str) -> dict:
        """The profile's settings now: config defaults with saved changes on top."""
        profile = self._config.profiles[name]
        with self._lock:
            override = dict(self._overrides.get(name, {}))
        return _merged(profile.setpoint_f, profile.pid, profile.output_limits, override)

    def saved_supply(self) -> dict:
        cfg = self._config.supply
        with self._lock:
            override = dict(self._supply_override)
        return _merged(cfg.setpoint_f, cfg.pid, cfg.output_limits, override)

    @property
    def hardware_kind(self) -> str:
        return self._hw.kind

    def set_output_limits(self, low: float, high: float) -> None:
        """Clamp the active profile's dephlegmator PID output. Manual moves
        are not limited."""
        self._check_limits(low, high)
        with self._lock:
            self._pid.output_limits = (low, high)
            self._state.output_limits = (low, high)
            self._overrides.setdefault(self._state.profile, {})["output_limits"] = [low, high]
            overrides = self._snapshot_overrides()
        self._save("overrides", overrides)

    def set_supply_output_limits(self, low: float, high: float) -> None:
        self._check_limits(low, high)
        with self._lock:
            self._supply_pid.output_limits = (low, high)
            self._state.supply_output_limits = (low, high)
            self._supply_override["output_limits"] = [low, high]
            override = dict(self._supply_override)
        self._save("supply_override", override)

    @staticmethod
    def _check_limits(low: float, high: float) -> None:
        if not 0 <= low < high <= 100:
            raise ValueError("output limits must satisfy 0 <= min < max <= 100")

    def set_mode(self, mode: Mode) -> None:
        with self._lock:
            if mode == Mode.MANUAL and self._state.mode != Mode.MANUAL:
                # Hold every valve where it was last commanded.
                self._manual_valves = dict(self._state.valves_pct)
            elif mode != Mode.MANUAL:
                self._manual_valves.clear()
            self._state.mode = mode

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
            return self.failsafe(f"temperature reading out of range: {temps}", temps=temps)

        with self._lock:
            mode = self._state.mode

        if mode == Mode.OFF:
            return self.failsafe("off", is_fault=False, temps=temps)
        try:
            if mode == Mode.MANUAL:
                return self._apply_manual(temps)
            return self._apply_auto(temps)
        except Exception as exc:
            logger.exception("failed to command valves")
            return self.failsafe(f"write error: {exc}", temps=temps)

    def failsafe(
        self,
        reason: str,
        *,
        is_fault: bool = True,
        temps: Optional[Temperatures] = None,
    ) -> ControllerState:
        """Open the dephlegmator and condenser valves fully; the supply valve
        holds its last position. Each valve is attempted independently, so one
        failing write does not stop the other from being commanded."""
        if is_fault:
            logger.warning("failsafe: %s", reason)
        valves = {"dephlegmator": FAILSAFE_PERCENT, "condenser": FAILSAFE_PERCENT}
        errors = []
        for name, percent in valves.items():
            try:
                self._hw.write_valve(name, percent)
            except Exception as exc:
                logger.exception("failsafe could not command the %s valve", name)
                errors.append(f"{name} valve write failed: {exc}")
        fault = reason if is_fault else None
        if errors:
            fault = "; ".join(filter(None, [fault, *errors]))
        return self._record(temps, valves, (0.0, 0.0, 0.0), fault=fault)

    def _apply_auto(self, temps: Temperatures) -> ControllerState:
        with self._lock:
            deph_pct = self._pid(temps.deph_return)
            p, i, d = self._pid.components
            supply_pct = self._supply_pid(temps.deph_supply)
            supply_terms = self._supply_pid.components
        valves = {
            "dephlegmator": deph_pct,
            "condenser": self._condenser_percent(temps),
            "supply": supply_pct,
        }
        self._write_valves(valves)
        return self._record(temps, valves, (p, i, d), fault=None, supply_terms=supply_terms)

    def _apply_manual(self, temps: Temperatures) -> ControllerState:
        with self._lock:
            valves = {
                "dephlegmator": self._manual_valves.get("dephlegmator", FAILSAFE_PERCENT),
                "condenser": self._manual_valves.get("condenser", self._condenser_percent(temps)),
            }
            if "supply" in self._manual_valves:
                valves["supply"] = self._manual_valves["supply"]
        self._write_valves(valves)
        return self._record(temps, valves, (0.0, 0.0, 0.0), fault=None)

    # -- helpers -------------------------------------------------------------

    def _snapshot_overrides(self) -> Dict[str, dict]:
        return {k: dict(v) for k, v in self._overrides.items()}

    def _save(self, key: str, value) -> None:
        """Persist a setting. A storage failure must not stop the controller
        from taking the change, so it is logged rather than raised."""
        if self._store is None:
            return
        try:
            self._store.set_setting(key, value)
        except Exception:
            logger.exception("could not save setting %r", key)

    def _build_pid(self, profile: ProfileConfig, override: Optional[dict] = None) -> PID:
        override = override or {}
        p, i, d = override.get("pid") or (profile.pid.p, profile.pid.i, profile.pid.d)
        pid = PID(
            p,
            i,
            d,
            setpoint=override.get("setpoint_f", profile.setpoint_f),
            time_fn=self._pid_time_fn,
        )
        pid.sample_time = profile.sample_time
        pid.output_limits = tuple(override.get("output_limits") or profile.output_limits)
        return pid

    def _build_supply_pid(self) -> PID:
        cfg = self._config.supply
        override = self._supply_override
        p, i, d = override.get("pid") or (cfg.pid.p, cfg.pid.i, cfg.pid.d)
        pid = PID(
            p,
            i,
            d,
            setpoint=override.get("setpoint_f", cfg.setpoint_f),
            time_fn=self._pid_time_fn,
        )
        pid.sample_time = cfg.sample_time
        pid.output_limits = tuple(override.get("output_limits") or cfg.output_limits)
        return pid

    def _condenser_percent(self, temps: Temperatures) -> float:
        """Condenser valve position in auto mode. Held fully open for now; a
        condenser PID would replace this body (see PLAN.md)."""
        return CONDENSER_PERCENT

    def _write_valves(self, valves: Dict[str, float]) -> None:
        for name, percent in valves.items():
            self._hw.write_valve(name, percent)
            if name == "supply":
                self._supply_last = percent

    def _record(
        self,
        temps: Optional[Temperatures],
        valves: Dict[str, float],
        pid_terms: tuple,
        fault: Optional[str],
        supply_terms: tuple = (0.0, 0.0, 0.0),
    ) -> ControllerState:
        with self._lock:
            self._state.temps_f = temps
            self._state.valves_pct = dict(valves)
            if self._supply_last is not None:
                self._state.valves_pct["supply"] = self._supply_last
            self._state.pid_terms = pid_terms
            self._state.supply_pid_terms = supply_terms
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
            pid_gains=s.pid_gains,
            supply_setpoint_f=s.supply_setpoint_f,
            supply_pid_terms=s.supply_pid_terms,
            supply_pid_gains=s.supply_pid_gains,
            output_limits=s.output_limits,
            supply_output_limits=s.supply_output_limits,
            fault=s.fault,
        )

    @staticmethod
    def _is_sane(temps: Temperatures) -> bool:
        lo, hi = SANE_TEMP_RANGE_F
        return all(
            lo <= t <= hi
            for t in (temps.deph_supply, temps.deph_return, temps.cond_supply, temps.cond_return)
        )
