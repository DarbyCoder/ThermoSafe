# ThermoSafe dashboard, devices, and backend

ESP32 firmware, a telemetry API, and the integrated ThermoSafe map dashboard.
The pictured hardware is supported: ESP32, Keyes DHT11, RGB LED, buzzer, and an
optional basic optical pulse module.

## Start the backend

Run from this project directory with Python 3.10 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.setup
python -m uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8000
```

Setup creates separate random device/dashboard API keys in `.env` and copies the
device key into `firmware/include/config.h`. It preserves existing files on reruns.
Do not commit or share either file. If a key changes in `.env`, update the matching
firmware or dashboard setting too. The server fails to start with blank or example keys.

## Run the integrated map and phone alerts

The GitHub front end is included at `index.html`. Start the telemetry API and the
dashboard/call bridge together from the project root:

```bash
.venv/bin/python scripts/start_local.py
```

Open <http://localhost:8001>. The API listens on port 8000. The bridge keeps the
dashboard key on the server and polls `/v1/snapshot` for real ESP32 telemetry.
Both services bind to the LAN so a gateway on the same network can reach the API.
The map shows eight nodes: the blue Site Manager gateway, two hardware worker
slots, and five moving simulated workers. A worker slot is labeled `SIM` until a
real worker appears, then it shows live temperature, humidity, brightness, pulse
(when available), risk state, mesh state, and last-seen time. Map coordinates are
labeled simulated unless the device reports GPS or a Wi-Fi estimate; this firmware
does not provide physical GPS. All simulated workers are placed on outdoor grass
areas, and another simulator patrols beside Mae near Campus Pond.

Use **Contacts** to replace the sample numbers with real worker recipients.
Automatic calls are sent when a hardware worker or the gateway transitions offline
and has a saved number. Existing stale records at dashboard startup do not trigger
new calls. The call server uses Gemini when `GEMINI_API_KEY` is set and falls back to
its fixed script if Gemini is unavailable. Real calls also require
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, and a public HTTPS
`PUBLIC_URL` for call key presses. The dashboard shows practice calls until Twilio
is configured. Keep credentials in the ignored `.env` file.

API documentation: <http://localhost:8000/docs>. Health check:
<http://localhost:8000/health>. Local SQLite persists in `data/thermosafe.sqlite3`
and provides the durable retry queue for TigerData uploads.
Run one backend process for this prototype. Install the project separately on each
computer; the virtual environment is not portable.

## Upload telemetry to TigerData

The ESP32 continues posting authenticated telemetry to this backend every five
seconds. The backend saves each reading locally first, then uploads it to Tiger
Cloud (TimescaleDB) in the background. If the cloud database is not configured or
temporarily unreachable, readings remain queued locally and retry automatically.
Existing local readings are queued for backfill on startup. The ESP32 never receives
or stores the TigerData credential.

Create or select a Tiger Cloud service, open its **Connect** details, and copy its
PostgreSQL connection URI into the ignored local `.env` file:

```dotenv
THERMOSAFE_TIGERDATA_DSN=postgresql://USER:PASSWORD@HOST:PORT/tsdb?sslmode=require
```

Keep the real URI in `.env`; do not add it to firmware, the frontend, or source
control. Then install the updated backend requirements and restart the backend:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8000
```

On its first successful connection, the backend creates `thermosafe_devices` and
the `thermosafe_telemetry` time-series hypertable. The telemetry table stores the
MAC-derived device ID, saved worker name/site, source label, every sensor value and
exposure timer, and the full risk assessment (including UV, explanation, and
recommended action). A uniqueness key makes outbox retries safe. `GET
/v1/tigerdata/status` reports connection state, last successful upload, and queued
row counts; the terminal tracker displays the same status. Simulator rows are also
uploaded with `source=simulator` so they remain distinguishable from measured data.

## Give your teammate these files

- `docs/API.md`: endpoints, sample data, display rules, and setup.
- `teammate/thermosafe-client.ts`: typed client without dependencies.
- `docs/openapi.json`: exported request and response schemas.

They need the backend URL, their dashboard key, and their browser origin listed in
`THERMOSAFE_CORS_ORIGINS`. If they use another computer, `localhost` points to their
computer; use this laptop's LAN IP instead. Both computers and the ESP32 must be
able to reach the backend on the same network. Some guest Wi-Fi blocks connections
between devices; a private router or suitable hotspot avoids that problem.

## Develop without the hardware

With the server running, open another terminal in this directory:

```bash
source .venv/bin/activate
python -m scripts.simulate
```

Four devices named `demo-01` through `demo-04` provide Normal, Caution, Warning,
and Critical examples. Every sample is explicitly tagged `source=simulator`.
Stop with Ctrl+C; after 20 seconds those devices appear offline.

```bash
python -m scripts.simulate --scenario cycle
python -m scripts.simulate --scenario sensor-failure
python -m scripts.simulate --scenario disconnect
```

Run one simulator at a time to avoid two sessions claiming the same demo device IDs.
Disable all simulator input with `THERMOSAFE_ALLOW_SIMULATOR=false` when desired.
Changes to `.env` take effect when the backend restarts.

Site weather and UV use Open-Meteo's forecast API. The `.env.example` coordinates
default to UMass Amherst for this demonstration; set
`THERMOSAFE_WEATHER_LOCATION_NAME`, `THERMOSAFE_WEATHER_LATITUDE`, and
`THERMOSAFE_WEATHER_LONGITUDE` in `.env` to your actual worksite, then restart the
backend. No GPS is present on the ESP32, and the API does not infer a worker's
location. The forecast is cached for ten minutes. A provider/network outage returns
UV as unavailable (or marks a cached result stale) instead of substituting a value.

The readable terminal monitor also has randomized risk-state tests. Start it from the
project root with `python scripts/serial_dashboard.py`, then press `1` for Normal,
`2` for Caution, `3` for Warning, `4` for Critical, or `5` for Unknown. Every press
creates a fresh set of randomized temperature, humidity, brightness, exposure-time,
activity, and acclimatization values. These are stored as `source=simulator` under
`demo-test-*` device IDs, so the teammate dashboard can render them too. When an
online ESP32 is available, each key also previews that alert on its physical LED and
buzzer for up to 20 seconds. The ESP32 polls the backend over its authenticated
device connection; stronger live local alerts take priority over a less severe
preview. Press `0` to clear the preview and return to the live ESP32 state. Sensor
readings and exposure timers are never replaced by demo values. Simulator input must
be enabled in `.env` for these tests.

At tracker startup, each online ESP32 prompts for its worker name. Press Enter to
keep a saved name or type a new one; the name is stored against that board's
MAC-derived device ID in the local SQLite database and is available to the teammate
dashboard API. Use `[` and `]` in the terminal view to switch between connected
hardware devices.

To open the live Graphviz-style mesh topology view in a browser, run this in another
project terminal after the backend is running:

```bash
.venv/bin/python scripts/mesh_topology.py
```

Open <http://127.0.0.1:8765>. The viewer polls the backend every second, shows the
gateway's reported parent/child tree, highlights newly detected nodes, and displays
online/offline status. The gateway reports its mesh tree with the next telemetry
packet, so a new join should appear within about one second. When the gateway tree
is not available yet, dashed links mean “mesh reachable, parent not yet reported”
rather than a measured direct radio link.

The live terminal view includes `UV INDEX` and `UV SCORE` rows. The former shows the
configured site's Open-Meteo forecast; the latter shows its contribution in the most
recent backend assessment. It also lists every known hardware ESP32 with its worker
name, online state, and last-reported mesh role, plus the selected board's mesh node
and simulated map coordinates. It refreshes from the backend every second; the
backend caches provider weather data for ten minutes.

## Configure and upload the firmware

Edit `firmware/include/config.h` for the actual Wi-Fi credentials, this laptop's
LAN API URL, and the GPIOs you choose. The default pins are examples. Set LED
common-anode/cathode and buzzer active/passive options to match your modules.
An empty `DEVICE_ID` generates a unique `clip-...` identifier from the ESP32 MAC.

Install PlatformIO's VS Code extension and open the `firmware` directory, or use:

```bash
python -m pip install 'platformio>=6.1,<7'
cd firmware
PLATFORMIO_CORE_DIR="$PWD/.platformio" pio run -e esp32dev-gateway
PLATFORMIO_CORE_DIR="$PWD/.platformio" pio run -e esp32dev-worker
PLATFORMIO_CORE_DIR="$PWD/.platformio" pio device monitor --baud 115200
```

The project pins the ESP32 platform and sensor libraries. USB upload/monitor uses
PlatformIO port discovery; set `upload_port` in `platformio.ini` if several boards
are connected. The serial log prints the device ID and status, never credentials.

The photoresistor uses a calibrated ADC window in `firmware/include/config.h`.
The current oriented ADC range 3200–4000 maps to 0–100%, so an upward-facing room
reading near ADC 3386 stays below the 35% bright threshold; a close flashlight should
drive brightness toward 100%. Recalibrate if another board or divider gives a
different ADC range. This is relative brightness only, not UV or solar radiation.

Firmware behavior:

- Reads the DHT11 every 2 seconds and sends a current mesh/backend sample every 1 second. Brightness and cumulative exposure are refreshed continuously on-device and reported each second.
- Runs local sensing, LEDs, and buzzer independently from HTTP in another task. It
  applies a fresh backend risk assessment that includes site UV when connected.
- Retries Wi-Fi and resumes current telemetry after a connection failure.
- Uses a one-sample queue; it does not claim historical offline samples are live.
- Turns failed or stale environment readings into Unknown rather than retaining
  a reassuring green state. A short blue overlay indicates backend disconnection.
- Uses green for Normal, yellow for Caution, orange for Warning, red for Critical,
  and flashing purple for Unknown. Warning/critical/fault states have distinct
  intermittent buzzer patterns; Caution has a short chirp. The more severe local or
  fresh backend risk alert takes priority over a less severe dashboard preview. Acknowledging a dashboard
  alert does not silence the worker's local alarm.
- Prints actual temperature, humidity, sensor validity, measured heat state, pulse
  ADC value, and experimental BPM status to Serial Monitor every second.
- Serial commands preview the RGB and buzzer outputs: `g`=green, `y`=yellow,
  `r`=red with buzzer, and `a`=return to automatic sensor mode. Preview commands
  affect only local outputs; they do not replace actual sensor readings.
- Serial demo inputs select activity (`i` light, `m` moderate, `h` heavy) and
  acclimatization (`c` acclimatized, `n` not acclimatized, `u` unknown). These are
  explicit demonstration adjustments, not physiological safety factors.

## Heat and exposure model

The environment category is calculated from the NOAA/National Weather Service heat
index, which combines air temperature and relative humidity. Firmware converts the
DHT11's Celsius temperature to Fahrenheit for the equation, then converts the
apparent temperature back to Celsius. Initial approximate screening bands are:

| Heat-index band | Category |
| --- | --- |
| Below 27°C | Lower |
| 27–32°C | Caution |
| 32–41°C | Extreme Caution |
| 41–54°C | Danger |
| Above 54°C | Extreme Danger |

These are approximate category labels, not medically validated worker-safety or
work/rest thresholds. The heat index does not account for direct solar radiation,
physical exertion, protective clothing, or acclimatization, and it is not a complete
occupational heat-stress assessment. If the NOAA equation is outside its applicable
range, the interface shows the heat-index category as unavailable rather than
assigning a category from air temperature alone.

The photoresistor timer is labeled **estimated bright-light exposure**. It starts
when relative brightness exceeds the configured threshold, pauses in shade, and
uses `millis()` to maintain cumulative shift exposure and a separate continuous
timer. The photoresistor measures brightness, not UV exposure or direct solar
radiation.

The hackathon **demonstration score** combines heat index (35%), air temperature
(15%), relative humidity (10%), estimated bright-light duration (20%), total
elevated-heat duration (10%), and site UV index (10%). The UV component is
`min(UV index / 11, 1) × 100`; it saturates at UV index 11. It uses a current
Open-Meteo site forecast, not a wearable UV reading. If weather UV is unavailable or
stale, that component is omitted and the available score weights are normalized.
Each duration component saturates at 30 minutes. If known, activity level and
acclimatization status add small explicitly displayed demo adjustments; unavailable
context is marked unknown and contributes zero. Score
bands are demo display thresholds only. The score is not a medical probability, does
not predict heatstroke, and is not safety guidance. The device does not measure core
body temperature, WBGT, or hydration.

Every backend assessment includes a plain-language `explanation`, structured
`triggered_by` labels, `factor_details`, `score_contributions`, and a
`recommended_action`. The explanation distinguishes a heat-index category trigger
from a combined-score trigger and lists the readings and demo-score components that
contributed. The action text is a general reminder to follow the site's heat plan;
it is not an individualized medical instruction. The API's score thresholds and
weights are in `backend/risk.py`; keep the matching firmware values in
`firmware/include/heat_risk.h` aligned when changing them.

The dashboard API also provides `POST /v1/demo/scenarios/{scenario}` for
`normal`, `caution`, `warning`, `critical`, and `unknown`. Each request returns and
stores a new randomized simulator sample with its assessment and explanation. It
requires the dashboard key and simulator input enabled. Demo records are separate
from hardware devices and clearly carry `source=simulator`.

The teammate's Gemini feature can use `/v1/snapshot`, device history, `risk_state`,
`age_seconds`, and the structured assessment fields to summarize who has the
strongest current screening indicators. Keep the API's deterministic assessment as
the displayed status; Gemini should explain the returned readings and their age/source
rather than inventing thresholds, medical probabilities, or a diagnosis. Clearly
label simulated readings and exclude or flag offline/stale devices in rankings.

## Predictive trend and UV

`GET /v1/devices/{device_id}/prediction?hours=12` compares recent valid ESP32
temperature/humidity readings with the next heat-index screening threshold. It fits
a straight line to up to ten minutes of recent readings and only shows a near-term
crossing estimate when at least five fresh samples span 30 seconds and the trend is
rising. Sensor extrapolation is capped at 60 minutes. Flat/decreasing readings,
stale devices, or insufficient history produce no countdown. This is a transparent
trend estimate, not a forecast of illness or a medical certainty.

That endpoint also reports hourly heat-index crossings from the site weather forecast
as a separate field; it does not blend the weather forecast into the device's current
risk state. The dashboard can render the sensor trend and forecast crossing
independently, with the returned explanation and recommended precaution.

## ESP-NOW mesh-only demo

For a radio-only mesh check that does not depend on sensors, Wi-Fi, the backend, or
TigerData, build and upload the same `esp32dev-espnow` firmware to every ESP32. The
first board powered on by itself becomes `Site Manager`; later boards hear its
beacon and join as workers. Keep all boards on the configured ESP-NOW channel 6.

```bash
cd firmware
PLATFORMIO_CORE_DIR="$PWD/.platformio" pio run -e esp32dev-espnow
PLATFORMIO_CORE_DIR="$PWD/.platformio" pio run -e esp32dev-espnow --target upload
PLATFORMIO_CORE_DIR="$PWD/.platformio" pio device monitor --baud 115200
```

The monitor should print `[STATUS] ... role=MASTER` on the first board. A joined
worker is listed as `[MESH] worker joined ...` and appears in the master's changing
`[TOPOLOGY]` Graphviz-style output. Type `name=Site Manager` or `name=Adam` in a
board's serial monitor to save its display name. The proof-of-concept topology is
observed at the USB-connected master's serial monitor; this isolated ESP-NOW build
does not upload mesh data to the backend or TigerData. At least two powered boards
are needed to verify a join/relay.

For a live browser graph directly from a USB-connected Site Manager, close its
serial monitor and run:

```bash
.venv/bin/python scripts/mesh_topology.py --serial-port /dev/cu.usbserial-0001
```

Open <http://127.0.0.1:8765>. The viewer reads master status and received worker
telemetry directly over USB; it does not need the API or Wi-Fi. Click a worker
node to inspect its latest temperature, humidity, pulse, brightness, and exposure
readings. The graph also has per-worker and all-worker LED controls.

The `esp32dev-gateway` accepts the same controls from its serial terminal as
`g|y|r|a [worker-id|all]`. For example, `r all` starts the repeating red alert on
every worker, and `a all` returns all workers to sensor-based automatic alerts.
With the graph reader holding the USB port, run its companion terminal controller
in another terminal:

```bash
.venv/bin/python scripts/mesh_control.py --target all
```

Enter `g`, `y`, `r`, or `a`; use `target <mesh-node-id|device-id|all>` to choose
one worker or broadcast to all. Workers boot into the GREEN demo case. G/Y/R
publish a complete, labeled simulated scenario (temperature, humidity, heat
index/category, UV, brightness, cumulative exposure, simulated BPM, score,
explanation, and action) as well as driving the LED/buzzer. Yellow gives one
short beep; red repeats until another mode is selected. A explicitly switches
to real sensor readings; missing sensors remain unavailable in that mode. The
simulation flag is included in telemetry and stored payloads. Keep the graph
reader and Site Manager powered while using these controls.

## Sensor workers with a Wi-Fi-connected Site Manager

For the dashboard/TigerData system, use two different firmware environments:

- `esp32dev-gateway` is the sensorless Site Manager. It bridges the local painlessMesh
  network to the configured Wi-Fi and forwards worker telemetry to the backend.
- `esp32dev-worker` samples the worker's sensors, runs local LED/buzzer alerts, and
  sends each worker's telemetry through the mesh to the Site Manager.

The map's eight-node layout reserves two real worker slots. To fill both beside
the blue master requires three boards total: one `esp32dev-gateway` Site Manager
and two `esp32dev-worker` sensor boards. USB connects each board to the computer
for upload and serial monitoring; live readings travel over the worker mesh and
the gateway's Wi-Fi connection to the API.

The worker firmware includes the current presentation-mode simulated BPM setting;
it marks those values as `simulated`, and the risk score ignores them. Real BPM is
only sent when the experimental signal checks accept a sensor reading. The gateway
does not initialize worker sensors, emit simulated BPM, or run local worker alerts.

Build/upload the gateway and worker images separately, changing the serial port to
the board currently plugged into USB:

```bash
PLATFORMIO_CORE_DIR="$PWD/firmware/.platformio" .venv/bin/pio run -d firmware -e esp32dev-gateway --target upload --upload-port /dev/cu.usbserial-PORT
PLATFORMIO_CORE_DIR="$PWD/firmware/.platformio" .venv/bin/pio run -d firmware -e esp32dev-worker --target upload --upload-port /dev/cu.usbserial-PORT
```

Only the gateway needs Wi-Fi access to the backend host. The backend stores and
syncs received worker readings to TigerData; the ESP32s do not connect directly to
TigerData. Keep the sensorless Site Manager powered and in radio range of at least
one worker. The laptop is needed for the browser graph/backend view, not for
worker-to-gateway radio communication.

The earlier `esp32dev-espnow` environment is a separate, radio-only topology test.
It has no sensor sampling or backend/TigerData upload path; do not use that image
for the sensor workers or Wi-Fi-connected Site Manager demo.

The demo location fields are generated simulated coordinates around the configured
site center. The ESP32 boards do not have GPS, and connecting to Wi-Fi does not give
them worker coordinates. The dashboard must show `location_source="simulated"`
beside those markers; do not describe them as GPS measurements. Update
`DEMO_LOCATION_CENTER_LATITUDE` and `DEMO_LOCATION_CENTER_LONGITUDE` in
`firmware/include/config.h` to center the presentation map.

`GET /v1/weather` returns the configured site's current forecast UV index and hourly
UV, temperature, and humidity. UV comes from Open-Meteo's weather model, not the
photoresistor, and is labeled a site-level forecast estimate. The latest non-stale UV
index is included in each backend demo risk assessment. The photoresistor continues
to report relative brightness and estimated bright-light exposure; it does not
measure UV. Show UV as unavailable when the API has no current value, and show a
cached value as stale when the provider is temporarily unreachable. The ESP32 uses a
fresh authenticated backend assessment for its physical alert while retaining local
heat alerts if the backend connection drops.

The weather service uses the [Open-Meteo Forecast API](https://open-meteo.com/en/docs) with a ten-minute server
cache and no API key. Forecast coverage and accuracy depend on the provider and the
configured coordinates; indoor or on-person conditions may differ from the site
forecast. Keep the Open-Meteo attribution visible wherever the weather data is shown.

Equation source: [NOAA/WPC heat-index equation](https://www.wpc.ncep.noaa.gov/html/heatindex_equation.shtml).

The presentation firmware currently enables `ENABLE_SIMULATED_BPM`. It sends a
slowly varying demo value around 70 BPM with `pulse_quality="simulated"`; the
dashboard/Gemini must display that label. This mode ignores the optical sensor's
estimate for the shown BPM, and heart rate never changes the heat-risk state. Set
`ENABLE_SIMULATED_BPM=false` to show the unreliable sensor estimate (experimental)
or unavailable instead.

## Check the implementation

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m scripts.export_contract
```

The C++/Python parity test needs `clang++` or `g++`; without one it is reported as
skipped. The full ESP32 cross-build uses PlatformIO as shown above. Automated tests
cover sensor validation, retries, out-of-order packets, reboot handling, concurrent
ingestion, persistence, alert transitions, offline detection, CORS, and role keys.
See `docs/VALIDATION.md` for the checks performed during implementation.

## Scope and known limits

The API uses two static role keys and plain HTTP for a trusted local demo network.
All clips share the device key; it is not a per-device identity system. Supervisor
names on acknowledgments are caller-supplied labels. A browser interface exposes
its demo key to its operator. Internet hosting requires HTTPS, user authentication,
per-device credentials, rate limits, and an appropriate worker-data policy.

The original map simulator still runs its scenario model, but the integrated map
shows five simulated workers beside the two live hardware slots. The older
`beacon/` server and firmware remain as a separate prototype; the current
`firmware/` and `backend/` are the live heat/exposure path used by this dashboard.

Local SQLite retains telemetry and alert history until you remove/archive the
database; successfully synced telemetry is mirrored in Tiger Cloud. Back up the
stopped server's SQLite database if you need to preserve unsynced rows and local
alert history. This demo has no physical GPS or indoor positioning. Its generated
coordinates are simulation values and must be labeled that way. Real phone calling
requires valid Twilio and Gemini credentials plus a publicly reachable HTTPS
callback URL; source code alone cannot enable an account's calls.
