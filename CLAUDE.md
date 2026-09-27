# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Controller code for the Shmidt Spirits still, running on a Raspberry Pi. Python scripts talk to `widgetlords` SPI DIN boards (`Mod8AI` thermistor inputs on `ChipEnable.CE0`, `Mod4AO` 4-20 mA valve outputs) and use `simple_pid` for control. The scripts need the Pi hardware and cannot run elsewhere.

## Layout and branches

- `original/` is a frozen copy of the pre-update code, kept for reference. Don't edit it.
- New work happens on `development` (code at the repo root) and is merged to `master`.

## Commands

There is no build, lint, or test setup. Run scripts on the Pi with `python3 <script>.py`, from the folder that holds the setpoint and CSV files (filenames are relative).

- `display_temperatures.py`: print all four temperatures
- `testing_cmd_vlv_all.py` / `testing_cmd_vlv_individual.py`: drive valve outputs by hand
- `system_graphing_{whiskey,gin}.py`: live plot of the data CSV (pandas + matplotlib, needs a display)

Every script runs its main loop at import time, so nothing is importable or unit-testable.

## Architecture

`whiskey_distillation.py`, `gin_distillation.py` and `supply.py` are standalone scripts with the same shape: init hardware, build PID objects, then loop (read temps, PID, write valve, print, sleep 2s, clear screen).

- **Signal chain:** thermistor counts go through `steinhart_hart(10000, 3380, 4095, x)` to Celsius, minus `therm_calibration_factor` (3.0), then to Fahrenheit. Valve percent maps to DA counts as `800 + 32 * percent` (800 = 4 mA, 4000 = 20 mA).
- **Channel map:** AI 0 = dephlegmator return, 1 = condenser return, 2 = dephlegmator supply, 3 = condenser supply. AO 0 = dephlegmator valve, 1 = condenser valve, 2 = supply valve.
- **Setpoints:** re-read every loop from `stpt-whiskey.txt` / `stpt-gin.txt` (first token of the first line), so they can be changed while the controller runs.
- **Logging:** the whiskey/gin scripts truncate `data-<spirit>.csv` at start, then append `time_stamp,temp_st,temp_supply,temp_return` each loop. `system_graphing_*.py` polls that CSV.
- **What each script controls:** `supply.py` drives the supply valve from dephlegmator supply temperature (output limits 0-60). The whiskey/gin scripts drive the dephlegmator valve from return temperature (limits 30-100 for whiskey, 40-100 for gin); the condenser PID is commented out.

## Gotchas

- The whiskey and gin scripts are near-duplicates (they differ only in filenames and the PID output floor), so a fix in one usually belongs in both.
- `gin_distillation.py` writes to an undefined `outputs` at startup; it should be `valve_outputs`.
- `animate()` in the distillation scripts uses `pd` without importing it, and `yd_*` are list literals rather than DataFrame columns. It is currently unused (plotting is commented out).
- `original/.stpt-whiskey.txt.swp` is a stray vim swap file.
