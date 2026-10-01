# AVATAR simulated-load integration (unpublished)

This branch integrates a virtual 60 W demonstration load with the current AVATAR
Load Forecasting page. No physical relay, building-level prediction, campus meter
measurement, or trained Villanova forecast is claimed. The old separate demo's
source files were not available in this repository; its manual command/status
behavior has been implemented here alongside automatic forecast control.

## Run this review version on Windows

Download this branch (not `main`) and extract it. Open Command Prompt in the folder
containing `server.py`, then run:

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python server.py
```

Python 3.12 is recommended. If it is not installed, install it before the first
command. Open http://127.0.0.1:8000/load-forecasting. Leave Command Prompt open.
Press Ctrl+C there to stop. Later sessions need only environment activation and
`python server.py`. Do not double-click the HTML file: that would not start Python.

`server.py` serves the existing static AVATAR website and APIs on one local origin.
The older `app.py` remains a legacy starter; it does not start this controller.
The shared JS can still display the hosted forecast on the static site. Controls
are disabled on static hosting until a future, explicitly approved deployment.
No main-branch changes, merge, live publishing, or hosting configuration changes
are part of this integration.

## Rules

| Condition | Simulated action |
| --- | --- |
| Startup or restart | Manual mode, illustrative load ON; no automatic switching |
| Automatic, current hourly average >= 250 kW | Request OFF |
| Automatic, current hourly average <= 230 kW | Request ON |
| Between thresholds | Hold previous state (hysteresis) |
| Less than 60 seconds since a switch | Defer automatic switching |
| No interval covering now, missing data, or expired forecast | Hold state and show waiting/unavailable/stale |
| HTTP forecast failure | Use cached intervals only while they cover the current time |
| Manual ON/OFF | Select Manual and apply immediately; this explicit override bypasses automatic waiting |
| Simulated disconnection | Block commands, show unknown simulated state, switch to Manual |
| Reconnection | Remain in Manual; resuming automation requires an explicit action |

250/230 kW and 60 seconds are editable demonstration settings, not approved campus
operating limits. The proxy is approximately 199–270 kW from the assumed 5.5 MWh/day.
The 60 W virtual lamp is separate from those campus kW thresholds; no savings are
subtracted from the campus forecast. Rules can be changed in Manual mode; ON must
be lower than OFF and minimum time must be 1–3600 seconds. Settings reset at restart.

The controller evaluates **the interval that contains now**, with inclusive start
and exclusive end. There is no advance shedding/lead time. Next-day peaks never
turn a load off early. Fresh startup normally has only tomorrow's forecast, so real
time automatic mode waits. Keep the server running across midnight, or use replay.
Unexpired forecasts are cached in ignored `instance/forecast-cache.json`, so a
current-day forecast survives polling for tomorrow and a server restart.

## Demonstration

1. Start the server and open Load Forecasting; wait for the forecast fetch.
2. Try manual ON and OFF, then leave the load ON.
3. Choose **Replay forecast (120×)**. The controller clock starts at tomorrow's
   first forecast interval. One simulated hour passes every 30 real seconds.
4. The default profile reaches 250 kW around midday, switches OFF, then returns ON
   as demand falls to 230 kW or below. A full 24-hour replay takes 12 minutes.
5. The same rules apply in replay, with the minimum interval measured in forecast
   time. At the end, replay stops advancing decisions and holds the final state.
6. Select Manual or a manual ON/OFF button to end replay. Test disconnection and
   reconnect; reconnect does not automatically resume replay or automatic mode.

Python runs the control loop every second, independently of the webpage. A separate
worker performs HTTP GET `/api/forecast` every 60 seconds. The page polls
`/api/status` every two seconds. Closing the browser does not stop the control loop;
Ctrl+C does. If the browser loses the backend, the UI shows Unknown and disables
commands instead of presenting old state as current.

## Forecast contract and teammate's answers

GET `/api/forecast` generates the existing proxy once per next-calendar-day target,
retaining its original generation timestamp for repeated requests. HTTP 500 is
`{"status":"error","message":"..."}`. The successful response contains:

- `status`: `success`; separate `forecast_type`: `temporary_proxy` (future: `trained_model`).
- `location_id`: `villanova_main_campus`; `unit`: `kW`.
- `generated_at` and equal `generated_at_utc`: offset-aware UTC ISO 8601 timestamps.
- `timezone`: `America/New_York`; `valid_until`: exclusive end of final interval.
- `predictions`: `interval_start`, `interval_end`, `value` (hourly average demand).
- Existing `hourly`: `timestamp`, `hour`, `label`, `forecast_kw`, retained for the chart.
- Existing daily-energy summaries in kWh/MWh, average/peak kW, method, warning and weather.

The calendar day has 24 intervals normally, 23 in spring and 25 in autumn. Absolute
one-hour intervals and UTC offsets disambiguate the repeated autumn hour. This
corrects the former fixed-24-hour proxy at DST transitions. Energy remains the
assumed 5.5 MWh daily total after normalization.

Weather failures do not invalidate a load forecast. The existing weather service
cache remains available; the new day-ahead payload captures context when generated.
Set `AVATAR_WEATHER_ENABLED=0` to skip weather locally. Weather does not adjust proxy
demand. Trained forecasting still requires Facilities history (30 days minimum,
6–12 months preferred) and the correct aggregate meter, as the teammate explained.

The controller rejects missing generation metadata, wrong location/unit, invalid
values, gaps/overlaps, inconsistent offsets and expired responses. It never
re-labels a stale static JSON file as fresh. The old static JSON fallback is for
chart display only and cannot drive the controller. `hourly` alone is not accepted
for automatic control without the new contract; upgrade the upstream response first.

## API commands

POST `/api/command`, with `Content-Type: application/json`:

```json
{"action":"mode","mode":"automatic"}
```

Other modes: `manual`, `replay`.

```json
{"action":"set_load","state":"OFF"}
{"action":"configure","off_kw":250,"on_kw":230,"minimum_seconds":60}
{"action":"connection","connected":false}
```

GET `/api/status` returns mode, connection, commanded/simulated state, effective
clock, active interval, forecast validity/source, rules, last successful fetch,
fetch failures and event log. No field represents verified physical feedback.

## Deployment boundary

This is a single-process, loopback-only simulation. Keep one owner of controller
state. It is not a multi-worker Gunicorn service or a public authenticated control
API. Before any later publication, decide the hosting topology, process ownership,
authentication and allowed origins. Future hardware needs a separate compatible
adapter, local operating limits and actual feedback. These steps are intentionally
outside this unpublished simulation change.

## Verification

```bat
python -m unittest discover -s tests -v
```

Tests cover threshold boundaries, hysteresis, minimum dwell, manual override,
disconnection, stale/missing/invalid forecasts, caching across dates/restarts,
replay clock changes, weather failure, DST and HTTP endpoints. Browser interaction
checks additionally cover command requests, displayed state and mobile layout.
