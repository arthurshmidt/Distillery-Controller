# Front end with Claude Design: what to bring and how to proceed

A checklist for building the web GUI (phase 7 of `PLAN.md`). The OpenAPI spec only describes the data. It says nothing about the screens, so give Claude Design a written brief and reference images as well.

## What to bring

1. **`docs/openapi.json`** is the API contract: endpoints, request and response shapes, auth, and the SSE stream. Regenerate it if the API changes.
2. **Reference images** of the look you want, such as dashboards or HMI panels you like. Label each one with what you like about it (layout, colors, gauges). An unlabeled image is ambiguous.
3. **A brief of about a page**, covering the points below.
4. **Build constraints**, listed after the brief.

### Brief

- **Who and where:** one operator on a phone or tablet near the still, over the LAN. Large touch targets, readable at a distance, and a dark theme if used in a dim room.
- **Screens or panels:**
  - Live temperatures: dephlegmator and condenser, supply and return. The supply temperature is the water bath outlet and is shared by both stages.
  - Setpoint with a +/- control, for the dephlegmator and separately for the supply (city water) loop (`/api/supply/setpoint`).
  - Mode switch (`auto`, `manual`, `off`) and manual valve sliders (`dephlegmator`, `condenser`, `supply`).
  - Profile selector (whiskey or gin).
  - PID gains for the dephlegmator and the supply loop, in an advanced section.
  - History chart from `/api/history`.
  - A failsafe or fault banner that can't be missed (the `fault` field of the state).
- **Safety behavior:**
  - Confirm before switching to `off` or `manual`.
  - Show clearly when the valves are in failsafe (dephlegmator and condenser fully open; the supply valve holds its last position, and `valves_pct.supply` is absent until it is first commanded).
  - Valve sliders only work in manual mode (the API returns 409 otherwise), so disable them in other modes.
- **Connection handling:** show a clear state when the SSE stream drops. The token comes from a login field stored in `localStorage`.

### Build constraints

- Plain static files (HTML, JS and CSS, or a small bundle) that FastAPI can serve. No Node server at runtime.
- No CDN dependencies, so it works offline on the LAN.
- Call relative `/api/...` URLs so it works at whatever host and port the daemon runs on.
- Browser `EventSource` can't set headers, so the stream is opened as `/api/stream?token=<token>`. All other calls use `Authorization: Bearer <token>`.

## How to proceed

1. Start Claude Design with the brief, the spec and the images. Ask for **one main dashboard first** and iterate on it before adding the history and settings screens.
2. Develop against the simulator so the data is live and no Pi is needed:
   `STILL_TOKEN=<token> .venv/bin/still --simulate`
3. When you're happy, bring the output back to Claude Code. The daemon doesn't serve static files yet. That work happens on a new branch from `development`:
   - Add static serving to the daemon.
   - Add a test for it.
   - Update `CLAUDE.md` and `PLAN.md`.
   - Regenerate `docs/openapi.json` if the spec needed changes (for example a missing field).
4. Check it on the Pi (phase 3) before relying on it with the real still.
