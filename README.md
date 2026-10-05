# Distillery Controller

Controller for the Shmidt Spirits still, running on a Raspberry Pi. A daemon holds the water bath and the dephlegmator at temperature setpoints with PID loops and exposes an HTTP API for a web GUI on the local network.

The `original/` folder holds the earlier standalone scripts. It is a frozen copy kept for reference and is not edited. The daemon in `src/still/` replaces those scripts.

## How it works

City water feeds a **water bath** through the **supply valve**. The bath also receives the warm return water from the two water-cooled stages, the **dephlegmator** and the **condenser**, and its outlet is their inlet. Each stage has a supply and a return thermistor and a 4-20 mA valve.

- **Supply (city water):** a PID loop reads the dephlegmator supply temperature (the bath outlet, which is also the inlet of both stages) and moves the supply valve to hold the setpoint (default 90 °F; P=-1, I=-0.01, D=0; valve limited to 0-60%). This is the `original/supply.py` loop. It is one shared setting in `config/still.yaml`, not per profile.
- **Dephlegmator:** a PID loop reads the return temperature and moves the valve to hold the setpoint. The default gains are P=-1, I=-0.01, D=0. The valve is limited to 30-100% for whiskey and 40-100% for gin.
- **Condenser:** held fully open. There is no condenser PID yet.
- **Profiles:** `whiskey` and `gin` each carry their own setpoint, PID gains and valve limits (`config/still.yaml`). The active profile is chosen from the GUI.
- **Modes:**
  - `auto`: the PID runs.
  - `manual`: the operator sets the valve positions. Entering manual holds every valve where it was last commanded (from auto, the PID's last output; from off, 100%), and a valve moves only when the operator sets it. Manual moves are not limited by the PID output limits.
  - `off`: the dephlegmator and condenser valves open; the supply valve holds its last position.
- **Failsafe:** the dephlegmator and condenser valves fully open. The supply valve is not moved: it holds its last position (it is not commanded at all until the first auto tick after startup). It is applied in `off` mode (not reported as a fault), on shutdown, and when a fault is detected: a sensor read error, an out-of-range or NaN temperature, or a valve write error. The reason for a fault is reported in the `fault` field of the state.
- **Sensor check:** raw ADC counts within 10 counts of either end of the range are treated as an open or shorted thermistor. The 10-count margin is an estimate and still needs checking on the Pi.

### Signal chain

Thermistor counts go through the Steinhart-Hart conversion (10 kΩ, beta 3380, 12-bit ADC), minus a 3.0 °C calibration offset, then to Fahrenheit. Valve percent maps to DA counts as `800 + 32 * percent` (800 = 4 mA, 4000 = 20 mA).

Channels (see `config/still.yaml`):

| Input | Sensor | Output | Valve |
|---|---|---|---|
| AI 0 | dephlegmator return | AO 0 | dephlegmator |
| AI 1 | condenser return | AO 1 | condenser |
| AI 2 | dephlegmator supply | | |
| AI 3 | condenser supply | AO 2 | supply (city water) |

### Architecture

```
web GUI ──HTTP/SSE──> FastAPI ──> Controller (state snapshot + commands)
                                        │
                        control loop thread ──> hardware layer
                                        │          ├─ WidgetlordsHW (real boards)
                                        └─> SQLite └─ SimulatedHW (dev/test)
```

The control loop runs in its own thread, so the web server can never stall the PID. The API reads a lock-protected state snapshot.

| File | Role |
|---|---|
| `src/still/config.py` | loads `config/still.yaml` |
| `src/still/controller.py` | PID, modes, profiles, failsafe |
| `src/still/hardware/` | `base.py` interface, `simulated.py`, `widgetlords.py` |
| `src/still/conversions.py` | thermistor and valve conversions, sensor check |
| `src/still/runner.py` | control loop thread |
| `src/still/store.py` | SQLite history and saved settings |
| `src/still/api.py` | FastAPI app |
| `src/still/__main__.py` | the `still` command |
| `deploy/still.service` | systemd unit |

## Setup

The system Python is externally managed, so use a virtual environment:

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Running

```
STILL_TOKEN=<token> .venv/bin/still --simulate
```

`--simulate` uses the simulated hardware, so it runs on any machine. Without it the daemon uses the real boards and only works on the Pi with `widgetlords` installed.

| Option | Default | Meaning |
|---|---|---|
| `--config` | `config/still.yaml` | config file |
| `--host` | `127.0.0.1` | bind address; use the LAN address (or `0.0.0.0`) to expose it |
| `--port` | `8000` | port |
| `--simulate` | off | use `SimulatedHW` |
| `--db` | `still.db` | SQLite file for history and saved settings |
| `--interval` | `1.0` | control loop period, seconds |

`STILL_TOKEN` is required. Interactive API docs are at `/docs`.

### Persistence

Each loop is logged to SQLite, and 14 days are kept. The active profile, each profile's setpoint and PID gains, and the supply setpoint and gains are saved and restored on restart. The mode is never saved: the daemon always starts in `auto`.

### Running as a service

`deploy/still.service` is a systemd unit. Edit the user and paths for the Pi. The token goes in `/etc/still/still.env` as `STILL_TOKEN=<token>` (chmod 600). On stop, SIGTERM makes the daemon open the dephlegmator and condenser valves before it exits. This has not been tried on a Pi yet.

## API

All `/api` routes require the token as `Authorization: Bearer <token>`. The SSE stream also accepts `?token=<token>`, because a browser `EventSource` cannot set headers. The full contract is `docs/openapi.json`. Regenerate it when the API changes.

| Endpoint | Purpose |
|---|---|
| `GET /api/state` | temperatures, valve %, setpoints, mode, profile, PID terms, gains and output limits (dephlegmator and supply), fault; `valves_pct.supply` is absent until it is first commanded |
| `GET /api/stream` | server-sent events: the full state once a second |
| `GET /api/history` | logged states with `since` < time <= `until` (unix seconds); `step` (seconds) keeps at most one point per step-second bucket (the earliest), so a long window stays small; `limit` (default 7200, newest kept) |
| `GET /api/events` | `since`, `until` (inclusive): `{events, segments}`. Events (`mode`, `profile`, `setpoint`, `fault`, `log`) come from the full-resolution rows, so thinning never hides a short fault; segments are the mode/fault timeline, with logging gaps left out |
| `GET /api/history.csv` | `since`, `until`: every logged row in the window as CSV, streamed. Fetch it with the `Authorization` header and save the blob |
| `GET /api/profiles` | the active profile and every profile: `setpoint_f`, `pid` and `output_limits` are the `still.yaml` defaults; `saved` is what it uses now (defaults with saved changes on top). `supply` has the same shape for the shared supply loop |
| `GET /api/info` | read-only: version, hardware (`simulated` or `widgetlords`), loop interval, history retention, default profile, startup mode, thermistor constants, channel map |
| `PUT /api/profile` | `{"name": ...}` select the active profile; keeps the current mode and manual valves |
| `PUT /api/setpoint` | `{"setpoint_f": ...}` between -40 and 300 |
| `PUT /api/mode` | `{"mode": "auto" \| "manual" \| "off"}` |
| `PUT /api/valves/{name}` | `{"percent": 0-100}` for `dephlegmator`, `condenser` or `supply`; manual mode only, otherwise 409 |
| `PUT /api/pid` | `{"p": ..., "i": ..., "d": ...}` for the dephlegmator |
| `PUT /api/output-limits` | `{"min": ..., "max": ...}` PID output limits for the active profile; `0 <= min < max <= 100`, otherwise 422; saved per profile |
| `PUT /api/supply/output-limits` | same, for the supply loop |
| `PUT /api/supply/setpoint` | `{"setpoint_f": ...}` for the supply loop |
| `PUT /api/supply/pid` | `{"p": ..., "i": ..., "d": ...}` for the supply loop |
| `PUT /api/profiles/{name}/setpoint` | `{"setpoint_f": ...}` saves a profile's setpoint, active or not (404 for an unknown profile) |
| `DELETE /api/profiles/{name}/overrides` | forget the profile's saved setpoint, gains and limits (back to `still.yaml`); applies now if it is active |
| `DELETE /api/supply/overrides` | the same for the supply loop |
| `GET` / `PUT` / `DELETE /api/sim/fault` | simulator only (`--simulate`; 404 otherwise, and not in `docs/openapi.json`). `PUT {"sensor": "deph_supply" \| "deph_return" \| "cond_supply" \| "cond_return", "kind": "open" \| "shorted" \| "nan"}` pretends that thermistor has failed (open/shorted raise `SensorError` like the real boards; nan returns NaN for that sensor); `DELETE` clears it |

## Tests

```
.venv/bin/python -m pytest
```

The tests run entirely against `SimulatedHW` and need no hardware.

## Status

Phases 1, 2, 4, 5 and 6 of [`PLAN.md`](PLAN.md) are done. Still to do:

- **Phase 3:** verify against the real boards on the Pi, including the sensor-check margin and the systemd shutdown.
- **Phase 7:** the web front end (the dashboard, History and Settings screens), designed in Claude Design (see [`docs/still-control-frontend-handoff.md`](docs/still-control-frontend-handoff.md)) and served at `/` from `src/still/static/`; B1 to B8 of the handoff are done. It has not been looked at in a real browser, on a tablet or phone, or on the Pi yet.

## Safety

The API can move valves, so keep it on the LAN and keep the token secret. Test changes with `--simulate` before running them on the still.
