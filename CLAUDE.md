# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Controller code for the Shmidt Spirits still, running on a Raspberry Pi. Python scripts talk to `widgetlords` SPI DIN boards (`Mod8AI` thermistor inputs on `ChipEnable.CE0`, `Mod4AO` 4-20 mA valve outputs) and use `simple_pid` for control. The scripts need the Pi hardware and cannot run elsewhere.

## Layout and branches

- `original/` is a frozen copy of the pre-update code, kept for reference. Don't edit it.
- New work happens on `development` (code at the repo root) and is merged to `master`.

## Commands

The `original/` scripts have no build, lint, or test setup. Run them on the Pi with `python3 <script>.py`, from the folder that holds the setpoint and CSV files (filenames are relative).

- `original/display_temperatures.py`: print all four temperatures
- `original/testing_cmd_vlv_all.py` / `original/testing_cmd_vlv_individual.py`: drive valve outputs by hand
- `original/system_graphing_{whiskey,gin}.py`: live plot of the data CSV (pandas + matplotlib, needs a display)

Every script runs its main loop at import time, so nothing is importable or unit-testable. See below for the new daemon package, which is testable.

## Architecture (original/)

`original/whiskey_distillation.py`, `original/gin_distillation.py` and `original/supply.py` are standalone scripts with the same shape: init hardware, build PID objects, then loop (read temps, PID, write valve, print, sleep 2s, clear screen).

- **Signal chain:** thermistor counts go through `steinhart_hart(10000, 3380, 4095, x)` to Celsius, minus `therm_calibration_factor` (3.0), then to Fahrenheit. Valve percent maps to DA counts as `800 + 32 * percent` (800 = 4 mA, 4000 = 20 mA).
- **Channel map:** AI 0 = dephlegmator return, 1 = condenser return, 2 = dephlegmator supply, 3 = condenser supply. AO 0 = dephlegmator valve, 1 = condenser valve, 2 = supply valve.
- **Setpoints:** re-read every loop from `stpt-whiskey.txt` / `stpt-gin.txt` (first token of the first line), so they can be changed while the controller runs.
- **Logging:** the whiskey/gin scripts truncate `data-<spirit>.csv` at start, then append `time_stamp,temp_st,temp_supply,temp_return` each loop. `system_graphing_*.py` polls that CSV.
- **What each script controls:** `supply.py` drives the supply valve from dephlegmator supply temperature (output limits 0-60). The whiskey/gin scripts drive the dephlegmator valve from return temperature (limits 30-100 for whiskey, 40-100 for gin); the condenser PID is commented out.

## Gotchas (original/)

- The whiskey and gin scripts are near-duplicates (they differ only in filenames and the PID output floor), so a fix in one usually belongs in both.
- `gin_distillation.py` writes to an undefined `outputs` at startup; it should be `valve_outputs`.
- `animate()` in the distillation scripts uses `pd` without importing it, and `yd_*` are list literals rather than DataFrame columns. It is currently unused (plotting is commented out).
- `original/.stpt-whiskey.txt.swp` is a stray vim swap file.

## Daemon (src/still/)

New work replacing `original/` with a daemon plus a web GUI; see `PLAN.md` for the full design and phased roadmap. Currently implemented (phase 1): config loading, the `Controller` (dephlegmator PID, condenser held fully open, auto/manual/off modes, failsafe), and a `SimulatedHW`/`WidgetlordsHW` hardware split so the controller is testable off the Pi.

- **Dev setup:** the system Python is externally managed, so use a venv: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
- **Tests:** `.venv/bin/python -m pytest`. These run entirely against `SimulatedHW` and don't need any hardware.
- **Testing against real hardware:** only possible on the Pi with the boards attached, running `WidgetlordsHW` (a direct port of the `original/` board access code) instead of `SimulatedHW`. This is phase 3 of `PLAN.md`, once the API (phase 4) is far enough along to drive the controller, or by exercising `WidgetlordsHW` directly the way `original/display_temperatures.py` and `original/testing_cmd_vlv_*.py` were used. Until then, `widgetlords` isn't installed here, and `WidgetlordsHW` raises a clear `RuntimeError` if instantiated off the Pi.
