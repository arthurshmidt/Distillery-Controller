# Still Controller Daemon: Plan

## Goal
A Python daemon that controls the still (dephlegmator and condenser) on the Raspberry Pi, based on the code in `original/`, with a web GUI reachable on the local network. The GUI is built separately with Claude Design against the daemon's API contract.

## Decisions
- **Scope:** the daemon controls the supply (city water) valve with a PID loop on the bath outlet temperature (`deph_supply`, from `supply.py`), the dephlegmator with a PID loop on its return temperature, and the condenser valve too, but for now the condenser is **held 100% open** (no condenser PID).
- **Profiles:** selected from the UI (whiskey, gin, ...), replacing the duplicated `whiskey_distillation.py` / `gin_distillation.py`.
- **Failsafe:** the dephlegmator and condenser valves fully open (4000 DA = 100%), as in the original scripts' initial position. Applied on startup, shutdown, crash, sensor fault, and in `off` mode. The supply valve is not moved by it: it holds its last position, and is not commanded until the first auto tick.
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
- Channels: AI 0 = deph return, 1 = cond return, 2 = deph supply, 3 = cond supply. AO 0 = deph valve, 1 = cond valve, 2 = supply valve.
- PID defaults (`simple_pid`):
  - Dephlegmator: input = return temp, P=-1, I=-0.01, D=0, limits 30-100 (whiskey) / 40-100 (gin), 1 s sample.
  - Condenser: held at 100% open for now. The originals' condenser PID (P=1, I=0.1, D=0.05, limits 0-100, 5 s sample, setpoint 150 F) was commented out and never run; it is not used, and no condenser gains or setpoint are exposed until control is needed.

## Phases
Status: phases 1, 2, 4, 5 and 6 are done, and phase 7 (front end) is built but has never been opened in a real browser. Phase 3 needs the Pi.

1. **Skeleton (done):** package layout, config and profile loading, hardware interface plus simulator, controller reproducing the whiskey dephlegmator behaviour.
2. **Condenser and profiles (done):** condenser valve held 100% open (structured so a PID can be added later), profile switching, failsafe and sensor sanity checks (out-of-range counts trigger the failsafe). Implemented as `check_counts()` in the hardware layer plus a temperature range check in the controller.
3. **Real hardware:** the `widgetlords` implementation; verify on the Pi against `display_temperatures.py` and the `testing_cmd_vlv_*` behaviour. Also confirm the `check_counts()` margin (10 counts) against real open, shorted and hot-thermistor readings.
4. **API (done):** endpoints, SSE stream, token auth (`Authorization: Bearer`, or `?token=` for the SSE stream). Spec exported to `docs/openapi.json`; regenerate it when the API changes.
5. **Persistence and logging (done):** `store.py`. SQLite history (one row per loop, 14 days kept, `/api/history` returns the newest 7200 points unless `limit` says otherwise) and saved settings: the active profile plus each profile's setpoint and PID gains. The mode is deliberately not saved; the daemon always starts in auto. `--db` sets the file (default `still.db`).
6. **Daemonize (done):** `deploy/still.service` (token from `/etc/still/still.env`, DB in `/var/lib/still`, `Restart=on-failure`; logs go to the journal via stderr). On SIGTERM `main()` opens both valves, then closes the hardware and store, each step guarded so one failure cannot skip the others. Getting there needs two things uvicorn does not do on its own, both in `__main__.py`: a no-op SIGTERM handler installed before `uvicorn.run()` (otherwise uvicorn re-raises SIGTERM through the OS-default handler once its own shutdown finishes, which kills the process before `main()`'s `finally` runs) and `timeout_graceful_shutdown=5` (otherwise uvicorn waits forever for the open `/api/stream` connection to close, which it only does when the client disconnects). Verified by hand in the simulator (SIGTERM with a browser tab open now opens the valves and exits in ~5s, well under the unit's `TimeoutStopSec=15`); the unit itself is still untested on a Pi.
7. **Front end (built, not yet seen in a browser or on the Pi):** designed in Claude Design; the handoff is `docs/still-control-frontend-handoff.md`. Plain static files in `src/still/static/` (ES modules, no build step, bundled fonts) served at `/` by `create_app()`. Dashboard, History and Settings are built and the daemon changes B1 to B8 are done: static serving; manual mode holds the last valve positions; PID output limits (state fields, `PUT /api/output-limits`, `/api/supply/output-limits`, Advanced tab); history `until`/`step`, `/api/events`, `/api/history.csv`; saved-versus-default values in `/api/profiles`; `PUT /api/profiles/{name}/setpoint`; `DELETE .../overrides`; `GET /api/info`. Remaining: the handoff's acceptance checklist in a real browser (laptop, 11-inch tablet, phone) and on the Pi. Not done: the optional bumpless hand-back to auto (seeding the PID integral from the valve position would also change startup).
8. **Tests:** unit tests on the simulator (conversions, PID limits, failsafe) and API tests.

## Workflow
Every change is made on a branch created from `development` and merged back when the user approves; nothing is committed directly to `development` or `master` (also in `CLAUDE.md`).

## Open items
- The supply valve has no commanded startup position (the original homed it closed). Decide on the Pi whether it needs one.
- Whether the API should refuse a dephlegmator output minimum below some floor (a 0% minimum could cut all cooling flow in auto).
- If condenser control is wanted later, its gains and setpoint will need tuning on the real still.

## Where we left off (2026-10-02)

Everything below is on both `development` and `master` (merged and pushed on 2026-10-02, before the front end had been checked in a browser).

**State:** the daemon and all three screens (dashboard, History, Settings) are built, and handoff items B1 to B8 are done. 102 tests pass (`.venv/bin/python -m pytest`). The front end was only ever exercised with throwaway jsdom scripts against `still --simulate`, never in a real browser. Nobody has seen how it looks.

**Next steps, in order:**
1. **Run it and look at it** (needs a browser, which the previous machine did not have):
   ```
   python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
   STILL_TOKEN=x .venv/bin/still --simulate
   ```
   Open http://127.0.0.1:8000/ and enter the token `x`. Compare against the screenshots in `docs/still-control-frontend-handoff.zip` (`screenshots/`, 16 PNGs) and the checklist in section 8 of `docs/still-control-frontend-handoff.md`. The history database is `still.db` in the current directory; it fills at one row per second, so the History screen is mostly empty on a fresh start.
2. **Fix what looks wrong.** Likely trouble spots, since none of it has been rendered: the process mimic (SVG plus percent-positioned tags, sized with container query units) at laptop, 11-inch tablet and phone widths; the History grid layout (`.hgrid` in `styles.css`); the chart cursor and tooltip; slider styling (`.slider`); focus rings and the 44px touch targets. The design source is `design/*.dc.html` inside the zip; the markup was copied from it, so differences are most likely in the CSS classes that replaced its inline styles.
3. **Check the stream and auth paths by hand:** stop the daemon and confirm the amber STREAM DISCONNECTED banner appears within about 5 s, values grey out and controls lock, and that restarting clears it without a reload. Start with a wrong token and confirm the login overlay shows "Token rejected". Switch to Off and confirm the amber CONTROL OFF banner (not the red failsafe banner). Unplug nothing: to see a fault, there is no switch in the simulator yet.
4. **Phase 3 on the Pi** (still the main unverified piece of the project): run against the real boards, confirm the `check_counts()` margin, try `deploy/still.service` and the SIGTERM shutdown, and measure the History queries (see below). Install `widgetlords` there and run without `--simulate`.

**Things deliberately left open:**
- No floor on the dephlegmator output minimum. Any `0 <= min < max <= 100` is accepted, so a 0% minimum can cut all cooling flow in auto. Decide whether to enforce one.
- No bumpless hand-back to auto: returning from manual or off starts from the PID's old integral, so the valve can jump. `simple_pid`'s `set_auto_mode(True, last_output=...)` would fix it, but seeding from the valve position also changes startup behavior, so it was skipped.
- The supply valve has no commanded startup position (see Open items).
- History rows do not log the output limits, so history points report 0 to 100 for them. Nothing on screen uses that.
- Performance of the long History queries is only measured on a fast desktop (14 days of 1 s rows: thinned history 0.01 s, events 0.9 s, CSV export 5 s). The Pi will be several times slower; the CSV streams, so only its total time is affected. If events are too slow there, the next step is storing events in their own table as they happen.
- Events rely on rows being inserted in time order, so a backwards clock step (a Pi booting before it has the time) shows as a logging gap. The oldest retained row can also produce one false "Logging started".

**Where to look:** `CLAUDE.md` (Daemon section) lists the files and their roles; `docs/still-control-frontend-handoff.md` is the front-end spec; `docs/openapi.json` is the API contract (regenerate when the API changes); `README.md` has the endpoint table.

**Testing the front end without a browser:** the earlier sessions used jsdom with a fake `EventSource` to drive the page against a running `still --simulate`. Those scripts lived in a temporary directory and are not in the repository.
