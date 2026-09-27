"""The real PI-SPI-DIN boards, as used in original/*_distillation.py.

This is a direct port of those scripts' read_temperatures()/command_valves()
onto the HardwareInterface, using the same wildcard imports the originals
used so the exact widgetlords submodule each name comes from does not need
to be guessed at here. It can only be exercised on the Raspberry Pi with the
boards attached; see PLAN.md phase 3 for verifying it there.
"""

from __future__ import annotations

from ..config import AppConfig
from ..conversions import celsius_to_fahrenheit, percent_to_da
from .base import HardwareInterface, Temperatures

try:
    from widgetlords.pi_spi_din import *  # noqa: F401,F403 - Mod8AI, Mod4AO, ChipEnable
    from widgetlords import *  # noqa: F401,F403 - init, steinhart_hart
except ImportError as exc:  # pragma: no cover - only available on the Pi
    _import_error: Exception | None = exc
else:
    _import_error = None


class WidgetlordsHW(HardwareInterface):
    def __init__(self, config: AppConfig):
        if _import_error is not None:
            raise RuntimeError(
                "the widgetlords package is required for WidgetlordsHW "
                "(only available on the Raspberry Pi)"
            ) from _import_error

        init()
        self._ai = Mod8AI(ChipEnable.CE0)
        self._ao = Mod4AO()
        self._thermistor = config.thermistor
        self._ai_channels = config.channels.ai
        self._ao_channels = config.channels.ao

    def read_temperatures(self) -> Temperatures:
        t = self._thermistor

        def read_f(channel_name: str) -> float:
            da = self._ai.read_single(self._ai_channels[channel_name])
            c = steinhart_hart(t.r_fixed, t.beta, t.adc_max, da) - t.calibration_factor
            return celsius_to_fahrenheit(c)

        return Temperatures(
            deph_supply=read_f("deph_supply"),
            deph_return=read_f("deph_return"),
            cond_supply=read_f("cond_supply"),
            cond_return=read_f("cond_return"),
        )

    def write_valve(self, name: str, percent: float) -> None:
        da = int(percent_to_da(percent))
        self._ao.write_single(self._ao_channels[name], da)
