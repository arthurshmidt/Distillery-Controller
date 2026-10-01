"""Loading and validating the daemon's YAML config file (config/still.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

import yaml

# Kept here rather than imported from hardware.base, which would be circular.
REQUIRED_VALVES = ("dephlegmator", "condenser", "supply")


@dataclass(frozen=True)
class ThermistorConfig:
    r_fixed: float
    beta: float
    adc_max: float
    calibration_factor: float


@dataclass(frozen=True)
class ChannelMap:
    ai: Dict[str, int]  # deph_return, deph_supply, cond_return, cond_supply
    ao: Dict[str, int]  # dephlegmator, condenser, supply


@dataclass(frozen=True)
class PidGains:
    p: float
    i: float
    d: float


@dataclass(frozen=True)
class ProfileConfig:
    setpoint_f: float
    pid: PidGains
    output_limits: Tuple[float, float]
    sample_time: float


@dataclass(frozen=True)
class SupplyConfig:
    """The city water supply valve loop. One shared setting, not per profile:
    the water bath serves every profile."""

    setpoint_f: float
    pid: PidGains
    output_limits: Tuple[float, float]
    sample_time: float


@dataclass(frozen=True)
class AppConfig:
    thermistor: ThermistorConfig
    channels: ChannelMap
    default_profile: str
    profiles: Dict[str, ProfileConfig]
    supply: SupplyConfig


def load_config(path: str | Path) -> AppConfig:
    """Load and validate the daemon's YAML config file."""
    data = yaml.safe_load(Path(path).read_text())

    thermistor = ThermistorConfig(**data["thermistor"])
    channels = ChannelMap(ai=dict(data["channels"]["ai"]), ao=dict(data["channels"]["ao"]))
    missing = [name for name in REQUIRED_VALVES if name not in channels.ao]
    if missing:
        raise ValueError(f"channels.ao is missing valves: {missing}")
    sup = data["supply"]
    supply = SupplyConfig(
        setpoint_f=sup["setpoint_f"],
        pid=PidGains(**sup["pid"]),
        output_limits=tuple(sup["output_limits"]),
        sample_time=sup.get("sample_time", 1.0),
    )

    profiles: Dict[str, ProfileConfig] = {}
    for name, p in data["profiles"].items():
        profiles[name] = ProfileConfig(
            setpoint_f=p["setpoint_f"],
            pid=PidGains(**p["pid"]),
            output_limits=tuple(p["output_limits"]),
            sample_time=p.get("sample_time", 1.0),
        )

    default_profile = data["default_profile"]
    if default_profile not in profiles:
        raise ValueError(f"default_profile {default_profile!r} is not in profiles")

    return AppConfig(
        thermistor=thermistor,
        channels=channels,
        default_profile=default_profile,
        profiles=profiles,
        supply=supply,
    )
