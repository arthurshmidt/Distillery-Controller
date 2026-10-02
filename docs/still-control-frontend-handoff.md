# Still Control front end: handoff to Claude Code

Phase 7 of `PLAN.md`. This document describes the approved design for the still controller's web GUI, how it maps onto the API, and the changes the daemon needs before the GUI can behave as designed.

- **Repository:** https://github.com/arthurshmidt/Distillery-Controller (work on a new branch from `development`, per `CLAUDE.md`)
- **Design canvas:** https://claude.ai/artifact/H4W5mQNERS8NcsLx9WWQaw (private to Arthur's account)
- **Design date:** 2026-10-02, against API version 0.1.0 (`docs/openapi.json`)

## 1. What is in this package

| Path | What it is |
|---|---|
| `HANDOFF.md` | This document |
| `design/Main.dc.html` | Dashboard design source |
| `design/History.dc.html` | History screen design source |
| `design/Settings.dc.html` | Settings screen design source |
| `screenshots/*.png` | 16 reference renders of every screen and state |
| `fonts/` | Chakra Petch and JetBrains Mono as woff2 (SIL OFL 1.1), plus `fonts.css` |

### How to read the design files

The `.dc.html` files are design sources, not production code. Use them as the exact reference for layout, sizes, colors and copy, and rebuild the behavior against the real API.

- Markup lives inside `<x-dc>`. Every visual value is an inline `style`, so sizes, colors and spacing can be copied exactly.
- `{{name}}` is a value supplied by the logic class. `<sc-if value="{{x}}">` is a conditional block and `<sc-for list="{{xs}}" as="x">` is a loop.
- The logic is the `class Component` in the `<script type="text/x-dc">` block at the end of each file. `renderVals()` shows how every displayed value, label and color is derived from state, which makes it the spec for the UI rules.
- Each file loads `./support.js`, the canvas runtime. It is not part of the product. Do not ship it.
- **All data in the designs is simulated.** `Main.dc.html` contains a JavaScript port of `SimulatedHW` and the two PI loops. `History.dc.html` generates five fake runs. `Settings.dc.html` shows the whiskey setpoint saved at 128 °F against a default of 130 °F only to demonstrate the "differs from default" marker. Replace all of it with API calls.

## 2. Build constraints

From `docs/frontend-brief.md`, unchanged:

- Plain static files (HTML, JS, CSS) that FastAPI serves. No Node server at runtime.
- No CDN dependencies. It must work offline on the LAN, which is why the fonts are bundled here.
- Call relative `/api/...` URLs.
- `EventSource` cannot set headers, so the stream opens as `/api/stream?token=<token>`. Every other call sends `Authorization: Bearer <token>`.
- One operator on a phone or tablet near the still: large touch targets, readable at a distance, dark theme.

### Suggested structure

A single page with hash routes (`#/`, `#/history`, `#/settings`) is recommended over three separate pages, so that one `EventSource` and one copy of the state survive navigation and the fault banner is global.

```
src/still/static/
  index.html
  app.js            entry, router, stream and state store
  api.js            fetch wrapper (token, 401/409/422 handling)
  dashboard.js  history.js  settings.js  chart.js
  styles.css
  fonts/            from this package
```

No build step is needed: plain ES modules are enough. The charts in the design are hand-drawn SVG paths, so no chart library is required.

## 3. Design system

### Color

| Role | Value |
|---|---|
| Page background | `#06080b` |
| Panel background / border | `#0c0f13` / `#232a33` |
| Control background / border | `#161b22` / `#56616f` |
| Hairline, input border | `#3a4452`, row rules `#1a2028` |
| Text primary / secondary / muted / dim | `#e6ebf1` / `#c9d2dc` / `#9aa7b5` / `#7d8997` |
| Selected control | fill `#e6ebf1`, text `#06080b` |
| Attention (amber) | `#fab219`, text on it `#06080b` |
| Fault (red) | fill `#b3261e`, border `#ff9d94`, text `#ffffff`; timeline segment `#e5484d` |
| Live dot | `#2bbf5a` |
| Focus ring | `3px solid #7fb4ff`, offset 2px |
| Mimic line art / vapor / tubes | `#dfe6ee` / `#aab4c0` / `#5f6b78` |
| Water bath fill | `#10243f` |
| Slider track / fill in auto / fill in manual | `#2a323c` / `#8fa0b3` / `#fab219` |
| Chart grid / baseline | `#1e252e` / `#3a4452` |

Rules that matter:

- **Red is reserved for faults.** Nothing else on any screen is red.
- **Amber means "not normal, operator attention":** manual mode, off mode, stream disconnected, token rejected, a saved value that differs from its default.
- **Auto is neutral.** The selected Auto button is white, not green.
- Status is never carried by color alone. Every chip, banner and marker also has a text label.

### Chart series

The four hues passed a color-vision-deficiency check against the dark surface. Keep the mapping fixed everywhere, including the mimic pipes and the history readout.

| Series | Color | Notes |
|---|---|---|
| Dephlegmator return | `#d95926` | 2.5px line, the controlled variable |
| Dephlegmator supply (bath outlet) | `#3987e5` | 2px |
| Condenser return | `#c98500` | 2px |
| Condenser supply | `#199e70` | 2px, usually hidden under deph supply because it is the same water |
| Dephlegmator setpoint | `#d95926` | 1.5px, dashed `6 5` |
| Supply setpoint | `#3987e5` | 1.5px, dashed `6 5` |

On the valve chart: dephlegmator `#d95926`, condenser `#c98500`, supply `#3987e5`. Two measures never share an axis: temperatures and valve percent are separate plots.

### Type

- **Chakra Petch** (400, 500, 600, 700) for all labels and text. Fallback: `'Segoe UI', system-ui, sans-serif`.
- **JetBrains Mono** (500, 700) for every number, time and fault string. Fallback: `ui-monospace, Menlo, Consolas, monospace`.
- The bundled files are the Latin subset. A few symbols (Δ, β, Ω, →, −) fall back to the system font, which is fine.
- Labels are 12px, weight 600, letter-spacing 0.14em, uppercase, muted. Screen title 22px/700. Main readings 46px mono 700. Setpoint 24px mono 700.

### Sizes

- Touch targets are never under 44px. Mode and profile buttons 48px high, setpoint ± buttons 56px high, confirm dialog buttons 64px high, slider thumb 36px on a 44px-high input.
- Panels: 1px border, 6px radius, 12 to 16px padding, 12px gaps. Buttons: 4px radius.

## 4. Screens

### 4.1 Dashboard (`design/Main.dc.html`)

Layout at laptop width: header, optional banner, then two columns. Left: the process mimic with a second tab for PID tuning. Right: the trend chart above three loop cards. The two columns wrap into one below about 950px; the loop cards wrap from three columns to one on a phone.

**Header**

- Mode switch: Auto, Manual, Off. `PUT /api/mode`.
  - Manual and Off open a confirm dialog first. Returning to Auto does not.
  - Selected Auto is white; selected Manual or Off is amber.
- Profile switch: Whiskey, Gin. `PUT /api/profile`. No confirmation. Build the buttons from `GET /api/profiles` rather than hard-coding two.
- Stream status: green dot and "LIVE, Updated hh:mm:ss", or amber dot and "NO STREAM, Last data hh:mm:ss".
- Gear button: opens Settings.

**Banners** (full width, under the header)

| Condition | Banner |
|---|---|
| `state.fault` is not null | Red "FAILSAFE ACTIVE" with the fault string verbatim in mono, a blinking warning icon, and three lines: "Dephlegmator valve 100%, full flow", "Condenser valve 100%, full flow", and "Supply valve holding at N%" or "Supply valve not commanded yet". `role="alert"`. |
| Stream dropped | Amber-bordered "STREAM DISCONNECTED" with last-data time, seconds since, "Reconnecting…" and a Retry now button. All values on screen turn grey and every control is disabled. |
| `mode == "off"` and no fault | Amber-bordered "CONTROL OFF" explaining the failsafe position. |

The fault banner must appear on every screen. On History and Settings it carries a "Go to dashboard" button.

**Process mimic**

White line art on black: pot, four-plate column, dephlegmator, vapor line to the condenser, distillate out, water bath, and the cooling water piping. Supply water is solid blue, return water is dashed orange.

- The dephlegmator and condenser valves are **3-way valves**. At 100% all water goes to the stage; at 0% it is bypassed back to the water bath. Each is drawn on its supply riser with a bypass leg teeing into that stage's return line.
- The supply (city water) valve is drawn as a 2-way valve feeding the bath.
- Seven tags sit on the piping: four temperatures (`temps_f.deph_supply`, `deph_return`, `cond_supply`, `cond_return`) and three valve positions (`valves_pct.dephlegmator`, `condenser`, `supply`).
- Valve tag states: normal "VALVE"; amber border in manual; "FAILSAFE" in off (amber) or fault (red fill); the supply tag reads "HOLDING" in off or fault, and "NO CMD" with a dash when `valves_pct.supply` is absent.
- When `temps_f` is null, every temperature shows an em dash.

The mimic scales as one unit. In the design the artwork is a single SVG with HTML tags positioned over it in percentages and sized in container-query units. Rebuilding it as one SVG with `<text>` is equally acceptable.

**Advanced · PID tab** (replaces the mimic in the left panel, so the trend stays visible while tuning)

- Two groups: dephlegmator loop (labelled with the active profile) and supply loop.
- Each has P, I, D gain inputs, Output min % and Output max % inputs, one Apply button, and the live P, I and D terms plus valve position.
- Apply is disabled until something changes, and blocked with an amber message when min is not below max, either limit is outside 0 to 100, or a gain is not a number.
- Apply sends `PUT /api/pid` or `PUT /api/supply/pid`, plus the new output-limits call (section 6, B3).

**Trend chart**

- Four temperatures and two dashed setpoints. Ranges: 5 min, 15 min, 1 hr.
- Seed from `GET /api/history?since=<now − range>` on load and on range change, then append from the stream.
- Y axis auto-ranges to the visible series, rounded out to tens, five ticks. X axis shows five time labels.
- Legend entries are toggle buttons. Hiding a temperature also hides its setpoint.
- Touch or hover shows a crosshair and a tooltip with the time and all six values.
- A "History" button in the chart header opens the History screen.
- Null temperatures (during a read fault) break the line; do not interpolate across them.

**Loop cards**

| Card | Main reading | Setpoint | Valve |
|---|---|---|---|
| Dephlegmator | `temps_f.deph_return`, with `deph_supply` small beside it | ± buttons, `PUT /api/setpoint` | `valves_pct.dephlegmator`, `PUT /api/valves/dephlegmator` |
| Supply loop | `temps_f.deph_supply`, labelled "Bath outlet" | ± buttons, `PUT /api/supply/setpoint` | `valves_pct.supply`, `PUT /api/valves/supply` |
| Condenser | `temps_f.cond_return`, with `cond_supply` small beside it | none; a note says there is no condenser PID yet | `valves_pct.condenser`, `PUT /api/valves/condenser` |

- Setpoint step is 1 °F per tap, clamped to −40..300 to match the API. "Δ +0.2°" beside the label is reading minus setpoint.
- Valve sliders are enabled **only in manual mode** (the API returns 409 otherwise) and are also disabled during a fault or a stream loss. Outside manual they act as position indicators with a narrow grey marker; in manual the fill and chip turn amber and the thumb becomes a 36px circle.
- Slider scale labels: "0 · BYPASS" to "100 · FULL FLOW" for the two 3-way valves, "0 · CLOSED" to "100 · OPEN" for the supply valve.
- Hint line under each slider: in auto "PID output, limits 30–100%" (dephlegmator), "PID output, limits 0–60%" (supply), "Auto: held at 100%" (condenser); in manual "Manual: drag to set"; in off or fault the failsafe wording; "Not commanded yet" when the supply position is absent.
- Card chip: PID (or "HELD 100%" for the condenser) in auto, MANUAL in manual, "OFF · 100%" or HOLDING in off, FAILSAFE (red) or HOLDING in a fault, "NO COMMAND" when the supply position is absent.

**Confirm dialogs** (amber border, Cancel and a 64px amber confirm button)

- "Switch to MANUAL?": PID control stops on both loops. All three valves hold their current positions until you move them.
- "Switch control OFF?": PID control stops on both loops. Dephlegmator and condenser valves go to 100%: full flow to each stage, no bypass (failsafe position). Supply valve holds its last position.

**Login overlay**

Shown when there is no token or the API answers 401. One password field labelled "Access token", a Connect button, and the note that the token is the one set as `STILL_TOKEN` and is kept on this device only.

### 4.2 History (`design/History.dc.html`)

- **Header:** Dashboard back button, title, live status with the current mode and profile.
- **Window bar:** lengths 1 H, 4 H, 12 H, 24 H, 3 D, 7 D, 14 D; earlier and later buttons that move by half a window; Now; the window's start and end; Export CSV.
  - The window cannot start earlier than 14 days ago (the retention period).
  - When the window ends at now it follows live data.
  - Changing the length re-centres the window on the cursor if one is set. This is how the operator zooms: tap a run in the 14-day view, then pick 4 H.
- **Three plots on one time axis:**
  1. Temperatures with dashed setpoints, auto-ranged, with toggling legend.
  2. Valve positions, fixed 0 to 100%.
  3. A mode strip: Auto dark grey, Manual amber, Off mid grey, fault red. Short segments keep a minimum width of 4px so a 40-second fault is still visible in a 14-day window.
  - Gaps where nothing was logged stay blank. Do not draw a line across them.
- **Cursor:** tap or drag on any plot to pin a vertical cursor through all three. It stays where it was left.
- **At cursor card:** time, mode and profile chip (or FAULT), the fault string if any, then all six temperatures and setpoints and three valve positions at the nearest logged point.
- **Events list:** newest first. Mode changes, profile changes, setpoint changes for either loop, fault start (with the fault string) and fault cleared (with duration), logging started and logging stopped. Each has a kind tag and a time. Tapping one moves the cursor to it.
- **States:** loading, no data in window, load failed (amber banner with Retry), and the global fault banner.

### 4.3 Settings (`design/Settings.dc.html`)

- **Profiles and loops:** one card each for Whiskey, Gin and the shared Supply loop.
  - Whiskey and Gin show an ACTIVE chip or a Make active button (`PUT /api/profile`). The active card has a brighter border.
  - Setpoint with ± buttons and "Default N" beside the label. This edits the saved setpoint of that profile, active or not.
  - A read-only table of P gain, I gain, D gain and Output limits, with Saved and Default columns. An amber square marks any saved value that differs from the default in `still.yaml`.
  - Reset button, enabled only when something differs. It confirms first and says whether the change takes effect now (active profile or supply loop) or only when the profile is made active.
  - "Tune on dashboard" link on the active profile and the supply loop. It should open the dashboard with the Advanced · PID tab selected. The inactive profile shows "Make active to tune" instead.
- **Connection:** stream status, token status, API location; the access token field with Show/Hide and Save; Sign out. A rejected token shows an amber message and disables the profile controls.
- **Controller:** read-only list of version, hardware (simulated or real boards), loop interval, history retention, mode at startup, thermistor constants, and the sensor and valve channel map.

## 5. Front-end behavior

### Token and connection

- Store the token in `localStorage` (suggested key `still.token`). No token: show the login overlay.
- Any 401 from the API: keep the stored token so it can be corrected, and show the login overlay with a "token rejected" message.
- `EventSource` reports errors without a status code. On an `error` event, probe `GET /api/state` once to tell a 401 from a network drop.
- Treat the stream as disconnected when the `EventSource` errors or when no message has arrived for about 5 seconds (the stream sends once a second). Show the amber banner, grey the values, disable every control, and keep retrying with backoff. Clear the banner on the next message.
- Static files must load without a token so the login screen can appear. Only `/api` is protected.

### Commands

- Every `PUT` returns the new `StateModel`. Render from that response immediately rather than waiting for the next stream message.
- Setpoint ± taps: update the number at once, then send the latest value after a short pause (about 300 ms) so ten taps make one request.
- Valve sliders: show the value while dragging, send on release and at most a few times a second while dragging.
- 409 on a valve write means the mode changed underneath: re-read the state and disable the sliders.
- 422 shows the validation message next to the control that caused it.

### Things the state already tells you

- `fault != null` means the failsafe is active: both 3-way valves read 100 and the supply valve holds.
- `valves_pct.supply` can be missing. Show a dash, never 0.
- `temps_f` can be null (before the first reading, or during a read error).
- The mode is never saved: the daemon always starts in auto.

## 6. Required changes to the daemon

Each item names the current behavior, the required behavior and a suggested shape. Regenerate `docs/openapi.json` and update `README.md`, `CLAUDE.md` and `PLAN.md` for every API or behavior change. Add tests beside the existing ones in `tests/`.

### B1. Serve the front end

- **Now:** `create_app()` in `src/still/api.py` only registers the `/api` router.
- **Needed:** mount the static directory at `/` after the router (for example `StaticFiles(directory=..., html=True)`), unauthenticated. Include the directory in the package data in `pyproject.toml`.
- **Test:** `GET /` returns the page without a token; `/api/state` still returns 401 without one.

### B2. Manual mode holds the last valve positions

- **Now:** `Controller.set_mode()` clears `_manual_valves` on leaving manual, and `_apply_manual()` falls back to `FAILSAFE_PERCENT` (100) for the dephlegmator and `_condenser_percent()` for the condenser until a valve is set by hand. Entering manual therefore drives the dephlegmator to 100%.
- **Needed:** on entering manual, seed `_manual_valves` from the positions last commanded (`self._state.valves_pct`: dephlegmator, condenser, and supply if it has been commanded). The valves then stay put until the operator moves one. Entering manual from off holds 100%, which is where off left them.
- **Also update** the module docstring and the README lines that describe manual mode.
- **Worth deciding at the same time:** on returning to auto the PID integral is whatever it was when auto was left, so the valve can jump. `simple_pid` supports a bumpless hand-back with `set_auto_mode(True, last_output=<current position>)`. Not required by the design, but cheap to do here.
- **Tests:** auto → manual keeps all three positions; off → manual keeps 100/100; a valve set in manual still works; leaving manual clears the manual positions.

### B3. Output limits: expose them and allow changes

- **Now:** limits come only from `config/still.yaml` (`profiles.<name>.output_limits`, `supply.output_limits`). `/api/profiles` returns the profile limits; the supply limits are not in the API at all; nothing can change either.
- **Needed:**
  - Add `output_limits: [min, max]` and `supply_output_limits: [min, max]` to `StateModel`.
  - `PUT /api/output-limits` with `{"min": number, "max": number}` for the active profile, and `PUT /api/supply/output-limits` for the supply loop. Validate `0 <= min < max <= 100`, otherwise 422. Return `StateModel`.
  - Persist like the gains: `overrides[profile]["output_limits"]` and `supply_override["output_limits"]`, and have `_build_pid()` / `_build_supply_pid()` read them.
  - Limits clamp the PID output only. Manual moves stay 0 to 100, as now. The Advanced tab says so on screen.
- **Open question for Arthur:** the dephlegmator minimum (30% whiskey, 40% gin) guarantees some cooling flow. Decide whether the API should refuse a minimum below some floor.

### B4. History: windows and downsampling

- **Now:** `GET /api/history` takes `since` and `limit` (max 100000, newest kept) and returns one full `StateModel` per second. `Store.history()` has no upper bound and no thinning. A 24-hour window is about 86,000 full states; a 14-day window cannot be requested at all.
- **Needed:**
  - `until` (unix seconds): only points at or before this time. In `Store.history()` add `AND ts <= ?`.
  - `step` (seconds): return at most one point per `step`-second bucket, for example `GROUP BY CAST(ts / :step AS INTEGER)` keeping the earliest row in each bucket. The `history_ts` index already exists.
  - The front end asks for roughly 500 to 1,000 points per window: `step = max(1, window_seconds / 600)`.
- **Events:** the History screen lists mode, profile and setpoint changes, faults and logging gaps. The design derives them by comparing consecutive states, which stops working once the data is thinned (a 40-second fault disappears from a 14-day window). Add `GET /api/events?since=&until=` that scans the full-resolution rows and returns `[{timestamp, kind, text}]` with kinds `mode`, `profile`, `setpoint`, `fault`, `log`, plus the mode and fault intervals for the mode strip. Rules, as in the design's logic class:
  - a gap of more than 60 s between rows is "Logging stopped" at the earlier row and "Logging started" at the later one;
  - `fault` going from null to a string is a fault event carrying the string; back to null is "Fault cleared after N s".
- **CSV export:** add `GET /api/history.csv?since=&until=` returning full-resolution rows, since the page only holds thinned data. The page must fetch it with the `Authorization` header and save the blob; a plain link cannot send the token.
- **Tests:** `until` bounds; `step` thinning returns the expected count and keeps order; events for a scripted sequence of mode change, setpoint change, fault and gap.

### B5. Profiles: saved values as well as defaults

- **Now:** `GET /api/profiles` builds every profile from `config.profiles`, so it returns the `still.yaml` defaults, not what is saved. The saved setpoint and gains are visible only for the active profile, through the state. The supply loop's defaults are not exposed.
- **Needed:** keep the existing fields as the defaults (no breaking change) and add, per profile, `saved: {setpoint_f, pid, output_limits}` (defaults merged with `_overrides[name]`). Add a top-level `supply: {setpoint_f, pid, output_limits, saved: {...}}`.

### B6. Edit the setpoint of a profile that is not active

- **Now:** `PUT /api/setpoint` always writes to the active profile.
- **Needed:** `PUT /api/profiles/{name}/setpoint` with the existing `SetpointRequest`. It writes `overrides[name]["setpoint_f"]`; if `name` is the active profile it behaves exactly like `PUT /api/setpoint`. 404 for an unknown profile.

### B7. Reset to defaults

- **Needed:** `DELETE /api/profiles/{name}/overrides` and `DELETE /api/supply/overrides`. Each removes the saved setpoint, gains and limits. If the target is in use (active profile, or the supply loop) rebuild its PID so the change applies on the next loop. Return `StateModel`.

### B8. Controller information

- **Needed:** `GET /api/info` returning the version, hardware kind (`simulated` or `widgetlords`), loop interval, history retention in days, default profile, the `channels` map and the `thermistor` constants from the config.
- **Also:** `FastAPI(title=..., version="0.1.0")` is hard-coded in `api.py`. Read it from the package metadata so it matches `pyproject.toml`.

## 7. What works today and what waits on the daemon

| Feature | Works against API 0.1.0 | Needs |
|---|---|---|
| Live temperatures, valves, mode, profile, fault banner | Yes | B1 to be served |
| Mode switch with confirmation, profile switch | Yes | |
| Setpoint ± for the active profile and the supply loop | Yes | |
| Manual valve sliders | Yes | |
| Manual holds last positions | No | B2 |
| PID gains in the Advanced tab | Yes | |
| Output limits (display on supply card, edit in Advanced tab) | Dephlegmator display only, from `/api/profiles` | B3 |
| Dashboard trend (up to 1 hr) | Yes: 3,600 points | |
| History screen beyond a few hours, earlier windows, 14-day view | No | B4 |
| Events list and mode strip at long ranges | No | B4 |
| Export CSV | No | B4 |
| Settings: Make active, token, sign out | Yes | |
| Settings: saved vs default table, inactive setpoint, reset | No | B5, B6, B7 |
| Settings: controller information | No (version only, via `/openapi.json`) | B8 |

Suggested order: B1 and the dashboard first so there is something to run on the Pi; then B2 and B3 (they change how the still behaves); then B4 with the History screen; then B5 to B8 with Settings.

## 8. Acceptance checklist

- [ ] `STILL_TOKEN=x .venv/bin/still --simulate` serves the GUI at `/` and the three screens work with live simulated data.
- [ ] No request leaves the LAN: no CDN, fonts served locally.
- [ ] Laptop (1366×860) and 11-inch tablet landscape show the dashboard without scrolling; a phone shows one column with nothing clipped.
- [ ] Switching to Manual or Off asks first; switching to Auto does not.
- [ ] Sliders move only in manual mode; in manual the valves stay where they were (B2).
- [ ] A fault shows the red banner on every screen with the fault text, and both 3-way valves read 100%.
- [ ] Stopping the daemon shows the stream-disconnected banner within about 5 seconds, greys the values and disables the controls; restarting it clears the banner without a reload.
- [ ] A wrong token shows the login overlay with a rejected message; the right one connects.
- [ ] `valves_pct.supply` absent shows a dash and "Not commanded yet".
- [ ] History can show any window in the last 14 days in under about 1,000 points per request, with gaps left blank.
- [ ] `docs/openapi.json`, `README.md`, `CLAUDE.md` and `PLAN.md` match the code, and `pytest` passes.

## 9. Assumptions to confirm with Arthur

1. **Piping on the mimic.** Each 3-way valve is drawn on the supply riser with the bypass teeing into that stage's return just above the bath. The bath is drawn with two separate takeoffs. No pump is shown.
2. **Supply valve** is treated as a plain 2-way valve.
3. **Setpoint step** is 1 °F.
4. **Controls lock when the stream drops**, even though plain requests might still succeed, because the operator would be acting on stale values.
5. **Profile switching has no confirmation**, as in the brief.
6. **History gaps:** the design assumes logging stops when the daemon is off, leaving blank stretches.
7. **Dephlegmator minimum output:** whether the API should enforce a floor (B3).

## 10. Prompt to start Claude Code with

```
Read docs/frontend/HANDOFF.md, then the three files in docs/frontend/design/ and
the images in docs/frontend/screenshots/. Create a branch from development.
Start with B1 and the dashboard so it runs against `still --simulate`, then show
me before moving on to B2 and B3. Follow CLAUDE.md for branches, tests and docs.
```
