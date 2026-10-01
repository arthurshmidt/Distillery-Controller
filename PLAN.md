# Still Controller Daemon: Plan

## Goal
A Python daemon that controls the still (dephlegmator and condenser) on the Raspberry Pi, based on the code in `original/`, with a web GUI reachable on the local network. The GUI is built separately with Claude Design against the daemon's API contract.

## Decisions
- **Scope:** the daemon controls the dephlegmator with a PID loop and the condenser valve too, but for now the condenser is **held 100% open** (no condenser PID). The supply valve (`supply.py`) is out of scope for now.
- **Profiles:** selected from the UI (whiskey, gin, ...), replacing the duplicated `whiskey_distillation.py` / `gin_distillation.py`.
- **Failsafe:** same as the original scripts' initial position: both valves fully open (4000 DA = 100%). Applied on startup, shutdown, crash, sensor fault, and in `manual`/`off` modes.
- **Access:** LAN only. Bind to the local interface and require a simple token or password, since the API can move valves.
- **Packaging:** normal Python package (`src/still/`, `pyproject.toml`).

## Communication
FastAPI (uvicorn) runs inside the daemon and also serves the built front end (one process, one port).

| Endpoint | Purpose |
|---|---|
| `GET /api/state` | temps, valve %, setpoints, mode, active profile, PID terms, faults |
| `GET /api/stream` | SSE, pushes state every second |
| `GET /api/history` | logged data for charts |
| `GET /api/profiles`, `PUT /api/profile` | list and select the active profile |
| `PUT /api/setpoint` | change the setpoint(s) for the active profile |
| `PUT /api/mode` | `auto` / `manual` / `off` |
| `PUT /api/valves/{name}` | manual valve position (manual mode only) |
| `PUT /api/pid` | change PID gains |

FastAPI generates the OpenAPI spec (`/openapi.json`, `/docs`). That spec is the contract handed to Claude Design.

## Architecture
```
web GUI ──HTTP/SSE──> FastAPI ──> Controller (state snapshot + command queue)
                                        │
                        control loop thread ──> hardware layer
                                        │          ├─ WidgetlordsHW (real)
                                        └─> logger └─ SimulatedHW (dev/test)
```
- The control loop runs in its own thread, so the web server can never stall the PID. The API reads a lock-protected state snapshot and sends commands through a queue.
- Hardware interface: `read_temperatures()`, `write_valve(name, percent)`. The simulated implementation allows development and tests off the Pi.
- Config file (YAML): channel map, thermistor constants, calibration factor and profiles. Persist runtime changes (active profile, setpoints).
- Logging: SQLite, one row per loop, feeding `/api/history`.
- Service: systemd unit with restart on failure.

## Carried over from `original/`
- Steinhart-Hart conversion `steinhart_hart(10000, 3380, 4095, x)`, calibration factor 3.0, C to F.
- Percent to DA: `800 + 32 * percent` (800 = 4 mA, 4000 = 20 mA).
- Channels: AI 0 = deph return, 1 = cond return, 2 = deph supply, 3 = cond supply. AO 0 = deph valve, 1 = cond valve.
- PID defaults (`simple_pid`):
  - Dephlegmator: input = return temp, P=-1, I=-0.01, D=0, limits 30-100 (whiskey) / 40-100 (gin), 1 s sample.
  - Condenser: held at 100% open for now. The originals' condenser PID (P=1, I=0.1, D=0.05, limits 0-100, 5 s sample, setpoint 150 F) was commented out and never run; it is not used, and no condenser gains or setpoint are exposed until control is needed.

## Phases
Status: phases 1, 2, 4, 5 and 6 are done. Phase 3 needs the Pi.

1. **Skeleton (done):** package layout, config and profile loading, hardware interface plus simulator, controller reproducing the whiskey dephlegmator behaviour.
2. **Condenser and profiles (done):** condenser valve held 100% open (structured so a PID can be added later), profile switching, failsafe and sensor sanity checks (out-of-range counts trigger the failsafe). Implemented as `check_counts()` in the hardware layer plus a temperature range check in the controller.
3. **Real hardware:** the `widgetlords` implementation; verify on the Pi against `display_temperatures.py` and the `testing_cmd_vlv_*` behaviour. Also confirm the `check_counts()` margin (10 counts) against real open, shorted and hot-thermistor readings.
4. **API (done):** endpoints, SSE stream, token auth (`Authorization: Bearer`, or `?token=` for the SSE stream). Spec exported to `docs/openapi.json`; regenerate it when the API changes.
5. **Persistence and logging (done):** `store.py`. SQLite history (one row per loop, 14 days kept, `/api/history` returns the newest 7200 points unless `limit` says otherwise) and saved settings: the active profile plus each profile's setpoint and PID gains. The mode is deliberately not saved; the daemon always starts in auto. `--db` sets the file (default `still.db`).
6. **Daemonize (done):** `deploy/still.service` (token from `/etc/still/still.env`, DB in `/var/lib/still`, `Restart=on-failure`; logs go to the journal via stderr). On SIGTERM uvicorn returns normally and `main()` opens both valves, then closes the hardware and store, each step guarded so one failure cannot skip the others. The unit is untested on a Pi.
7. **Front end:** built with Claude Design against the spec, served by the daemon.
8. **Tests:** unit tests on the simulator (conversions, PID limits, failsafe) and API tests.

## Workflow
Every change is made on a branch created from `development` and merged back when the user approves; nothing is committed directly to `development` or `master` (also in `CLAUDE.md`).

## Open items
- Whether the supply valve joins the daemon later.
- If condenser control is wanted later, its gains and setpoint will need tuning on the real still.
