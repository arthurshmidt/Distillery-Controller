# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Controller code for the Shmidt Spirits still, running on a Raspberry Pi. Python scripts talk to `widgetlords` SPI DIN boards (`Mod8AI` thermistor inputs on `ChipEnable.CE0`, `Mod4AO` 4-20 mA valve outputs) and use `simple_pid` for control. The scripts need the Pi hardware and cannot run elsewhere.

## Layout and branches

- `original/` is a frozen copy of the pre-update code, kept for reference. Don't edit it.
- `development` (code at the repo root) is the integration branch and is merged to `master`.
- **Never commit directly to `development` or `master`.** Make every change, including docs-only ones, on a new branch created from `development` (e.g. `docs/<topic>`, `phase-3-hardware`). Commit there, and only push or merge into `development` when the user asks.

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

New work replacing `original/` with a daemon plus a web GUI; see `PLAN.md` for the full design and phased roadmap. Currently implemented (phases 1, 2, 4, 5 and 6, plus the supply loop): config loading, the `Controller` (supply PID on `deph_supply` (shared `supply:` config section, limits 0-60), dephlegmator PID, condenser held fully open via `_condenser_percent()`, auto/manual/off modes, profile switching, failsafe), and a `SimulatedHW`/`WidgetlordsHW` hardware split so the controller is testable off the Pi.

- **Failsafe:** the dephlegmator and condenser valves fully open; the supply valve holds its last position and is not commanded until the first auto tick. Each valve write is attempted independently, and a read error, out-of-range or NaN temperature, or valve write error in auto/manual mode triggers it. It records the temperatures `tick()` already read and does not re-read the sensors.
- **Manual mode and limits:** entering manual seeds the held valve positions from the last commanded ones (`set_mode`); leaving manual clears them. PID output limits (`set_output_limits`, `set_supply_output_limits`, validated `0 <= min < max <= 100`) are saved in the overrides alongside the gains and read by `_build_pid()`/`_build_supply_pid()`. History rows don't log limits, so history points report the 0-100 default.
- **Profile switching:** `set_profile()` rebuilds the PID and setpoint but keeps the current mode and any manual valve positions.
- **Sensor check:** `WidgetlordsHW` passes raw counts through `check_counts()` (`conversions.py`), which raises `SensorError` within 10 counts of either end of the ADC range (open or shorted thermistor). The 10-count margin is an estimate and still needs checking on the Pi.
- **API and running:** `api.py` (FastAPI app factory), `runner.py` (control loop thread), `store.py` (SQLite history plus saved profile/setpoint/gains, `--db`, default `still.db`; the mode is never restored) and `__main__.py`. Run with `STILL_TOKEN=<token> .venv/bin/still --simulate` (add `--host <lan-ip>` to expose it; it binds to 127.0.0.1 by default). `docs/openapi.json` is the exported contract for the front end.
- **Front end:** `src/still/static/` (`index.html`, `app.js` entry/stream/login/banners, `dashboard.js`, `chart.js`, `api.js`, `state.js`, `styles.css`, bundled fonts). `create_app()` mounts it at `/` after the `/api` router, unauthenticated. Plain ES modules, no build step. Design source and screenshots are in `docs/still-control-frontend-handoff.zip`; the plan is `docs/still-control-frontend-handoff.md`. Only the dashboard is built (B1 to B3 of the handoff are done); History and Settings are placeholders. It has not been looked at in a real browser yet (checked only with a jsdom script).
- **Deployment:** `deploy/still.service` is the systemd unit (edit the user and paths for the Pi). Shutdown on SIGTERM opens both valves via `ControlLoop.stop()`; it hasn't been tried on the Pi yet.
- **Dev setup:** the system Python is externally managed, so use a venv: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
- **Tests:** `.venv/bin/python -m pytest`. These run entirely against `SimulatedHW` and don't need any hardware.
- **Testing against real hardware:** only possible on the Pi with the boards attached, running `WidgetlordsHW` (a direct port of the `original/` board access code) instead of `SimulatedHW`. This is phase 3 of `PLAN.md`, once the API (phase 4) is far enough along to drive the controller, or by exercising `WidgetlordsHW` directly the way `original/display_temperatures.py` and `original/testing_cmd_vlv_*.py` were used. Until then, `widgetlords` isn't installed here, and `WidgetlordsHW` raises a clear `RuntimeError` if instantiated off the Pi.
