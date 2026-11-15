# Dashboard integration

Copy `teammate/thermosafe-client.ts` into your interface project. The backend is
implemented independently; your interface only renders it and calls its API.

## Connection

For the same laptop, the base URL is `http://localhost:8000`. For another computer,
use the backend laptop's LAN address, for example `http://192.168.1.100:8000`.
Get the dashboard key privately from `.env`, under `THERMOSAFE_DASHBOARD_KEY`.
The device key is for firmware/simulator input and does not authorize dashboard reads.

Include this header on all dashboard requests:

```text
X-Dashboard-Key: <dashboard key>
```

Add the interface's exact origin (scheme, hostname, port) to
`THERMOSAFE_CORS_ORIGINS` and restart the backend. `localhost` and `127.0.0.1` are
different origins. HTTPS pages cannot fetch an HTTP API directly due to mixed-content
rules; run both locally for the demo, or put both behind HTTPS when deploying.

## Client use

```ts
import { ThermoSafeClient } from "./thermosafe-client";

const api = new ThermoSafeClient(API_BASE_URL, DASHBOARD_KEY);
const snapshot = await api.getSnapshot();
// Render snapshot.devices and snapshot.alerts.
await api.updateDevice("clip-example", { worker_name: "Worker A", site: "Roof" });
await api.acknowledgeAlert(alertId, "Supervisor A");
```

Poll `getSnapshot()` every second. Wait for the previous call to finish before
starting another call; stop the polling loop on unmount. If a call fails, show
"Backend unavailable" and mark previously rendered values stale. Device offline
status cannot be refreshed while the backend itself is unreachable. The client
times out after eight seconds and throws on HTTP/network errors.

Use device IDs returned by the API. Devices automatically appear after their
first telemetry packet; worker name and site can be assigned afterward.

## Dashboard endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Public health, API version, UTC server time |
| GET | `/v1/config` | Timeout, model, units, thresholds, poll interval |
| GET | `/v1/tigerdata/status` | Tiger Cloud connection and durable upload-queue status |
| GET | `/v1/weather` | Cached site weather and forecast UV index |
| GET | `/v1/snapshot` | Devices plus active alerts, recommended polling endpoint |
| GET | `/v1/devices` | Devices without alert list |
| POST | `/v1/demo/scenarios/{scenario}` | Create a randomized simulator sample for a risk state |
| PATCH | `/v1/devices/{device_id}` | Set or clear `worker_name` / `site` |
| POST | `/v1/devices/{device_id}/alert-test/{state}` | Preview a risk state on connected hardware indicators for 20 seconds |
| DELETE | `/v1/devices/{device_id}/alert-test` | Clear the physical alert preview |
| GET | `/v1/device/{device_id}/alert-test` | Device-authenticated preview poll plus latest risk state, site UV, score, and reading time for firmware |
| GET | `/v1/devices/{device_id}/history?after_id=0&limit=200` | Ascending historical samples |
| GET | `/v1/devices/{device_id}/prediction?hours=12` | Recent sensor trend estimate and separate site forecast crossing |
| GET | `/v1/alerts?active_only=true&after_id=0&limit=200` | Active or historical alerts |
| POST | `/v1/alerts/{alert_id}/ack` | Record `{"acknowledged_by":"Supervisor A"}` |

`limit` must be 1–1000. History and alert list pages contain `items` and
`next_after_id`; pass that cursor to fetch the next page. A shorter page means
you have reached the current end. Start at zero to backfill a chart, then retain
the last cursor to fetch only later samples. Chart timestamps are `received_at`.
Alert cursors paginate IDs, not updates: re-fetch the snapshot/active list to see
acknowledgment, escalation, or resolution changes to existing alerts.

`PATCH` preserves omitted fields. Send null to clear a field. The endpoint returns
the device ID and fields you supplied. `GET /v1/snapshot` includes all devices and
up to 1000 active alerts; check `alerts_truncated` and page `/v1/alerts` if true.
The prototype is designed for a small demo fleet.

## Device network and TigerData telemetry mirror

The firmware uses one gateway and one or more worker ESP32s. Workers broadcast
telemetry over the mesh every second; the gateway forwards each unchanged
worker payload to the laptop backend and also uploads its own readings. The gateway
is the only ESP32 that needs router access. Keep the gateway within Wi-Fi range and
configure every node with matching mesh name, password, and port. Each worker keeps
its own MAC-derived `device_id`, boot ID, and sequence so the backend attributes its
telemetry correctly. The backend commits each reading to local SQLite and adds it to
a durable outbox; a background worker uploads batches to Tiger Cloud over TLS
every five seconds while keeping the local API feed available for one-second polling.
Failed uploads remain queued and retry, and existing local readings are backfilled
when the backend starts. The database credential stays in the backend's `.env` as
`THERMOSAFE_TIGERDATA_DSN`; it is never sent to the ESP32 or browser.

The backend creates the `thermosafe_devices` registry and the
`thermosafe_telemetry` Timescale hypertable on first successful upload. The latter
contains device and worker identity, source, all validated sensor fields and timers,
flattened query columns for heat-index/UV/risk, location source and coordinates,
mesh role/connectivity/node IDs and the parent/child tree, plus full JSONB copies
of the raw telemetry and assessment. Existing tables receive the location/mesh
columns automatically. The
key `(received_at, device_id, boot_id, sequence)` makes retries idempotent.
`/v1/tigerdata/status` gives `configured`, `state`,
`pending_count`, `failed_pending_count`, `last_success_at`, and a safe error class;
it never returns the DSN.

Each telemetry sample includes `mesh_role`, `mesh_connected`, `mesh_node_id`, and
`mesh_node_ids`. The gateway also includes `mesh_topology`, the current parent/child
tree from the mesh library. Use that tree to draw links; worker peer lists indicate
reachability but do not prove direct radio links. Aggregate the latest values across
`snapshot.devices` to show the known workforce and their mesh status. The gateway
tree refreshes with its normal telemetry packet (about every second).

The demo may include `latitude_deg` and `longitude_deg` with
`location_source="simulated"`. These generated map coordinates are for presentation
only. They are not GPS or Wi-Fi geolocation and must remain visibly labeled as
simulated in the dashboard and Gemini answers. The boards contain no GPS hardware.

## Site UV and predictive intervention

The API reads Open-Meteo forecast data using the site's configured coordinates and
caches it for ten minutes. Set `THERMOSAFE_WEATHER_LOCATION_NAME`,
`THERMOSAFE_WEATHER_LATITUDE`, and `THERMOSAFE_WEATHER_LONGITUDE` in `.env`; the
example defaults are UMass Amherst. The ESP32 has no GPS, so this is a site-level
weather estimate. `GET /v1/weather` returns current and hourly `uv_index` values,
temperature, humidity, provider, coordinates, and fetch time. The latest non-stale
current UV value is included in the backend assessment and demonstration score. It
remains separate from the photoresistor brightness. If the provider is unavailable,
`available` is false; if a cached response is being served, `stale` is true. Keep the
returned Open-Meteo attribution visible in the dashboard.

`GET /v1/devices/{device_id}/prediction?hours=12` returns `sensor_trend` and
`weather_forecast` separately. These future-looking fields do not change the current
`risk_state`; the latest non-stale site UV is included in the next telemetry assessment.
The sensor trend uses up to ten minutes of recent,
valid telemetry and requires at least five fresh samples spanning 30 seconds. It
fits a linear slope and only gives a crossing time for a rising trend, with a
maximum 60-minute extrapolation. Otherwise its `crossing_in_minutes` is null and
`reason` explains why. The weather field looks for a crossing in the next 1–24 hours
using hourly site forecast temperature/humidity. Neither prediction changes
`risk_state`; show the method, timestamp/reading age, and unavailable reason with the
estimate. These are demonstration estimates, not a heat-illness forecast or a
medically validated work/rest limit.

The `scripts/serial_dashboard.py` live terminal view shows the current site UV index
and the score component used for the latest reading. It polls weather through the
backend every minute. The ESP32 polls the authenticated device status response; a
fresh backend assessment that includes site UV can raise its physical alert state.
Local heat-index alerts continue if the backend disconnects.

## Snapshot example

```json
{
  "devices": [{
    "device_id": "clip-example",
    "worker_name": "Worker A",
    "site": "Roof",
    "source": "hardware",
    "connection_state": "online",
    "risk_state": "normal",
    "environment_sensor_ok": true,
    "last_seen_at": "2026-11-14T04:00:00Z",
    "age_seconds": 0.2,
    "latest": {
      "id": 1,
      "received_at": "2026-11-14T04:00:00Z",
      "schema_version": 1,
      "device_id": "clip-example",
      "boot_id": "boot-example",
      "sequence": 0,
      "uptime_ms": 5000,
      "source": "hardware",
      "firmware_version": "0.1.0",
      "temperature_c": 23.0,
      "humidity_pct": 45.0,
      "environment_sensor_ok": true,
      "pulse_raw": null,
      "pulse_bpm": null,
      "pulse_quality": "disabled",
      "light_sensor_ok": true,
      "light_raw": 3000,
      "light_intensity_pct": 73.3,
      "bright_light_threshold_pct": 60.0,
      "bright_light_active": true,
      "bright_exposure_seconds": 420,
      "bright_continuous_seconds": 420,
      "heat_exposure_seconds": 0,
      "heat_continuous_seconds": 0,
      "activity_level": "unknown",
      "acclimatization_status": "unknown",
      "wifi_rssi_dbm": -55,
      "latitude_deg": null,
      "longitude_deg": null,
      "location_source": "unavailable",
      "mesh_role": "disabled",
      "mesh_connected": false,
      "mesh_node_id": null,
      "mesh_node_ids": [],
      "mesh_topology": null,
      "assessment": {
        "state": "normal",
        "heat_index_c": null,
        "site_uv_index": null,
        "uv_index_score": null,
        "heat_index_category": "lower",
        "indicator_f": null,
        "bright_light_exposure_score": 23.33,
        "heat_duration_risk_score": 0.0,
        "exposure_risk_score": 23.33,
        "combined_risk_score": 10.18,
        "demo_score_components": {
          "heat_index": 0.0,
          "air_temperature": 0.0,
          "relative_humidity": 45.0,
          "estimated_bright_light_duration": 23.3,
          "elevated_heat_duration": 0.0,
          "uv_index": null,
          "activity_adjustment": 0.0,
          "acclimatization_adjustment": 0.0,
          "total_context_adjustment": 0.0
        },
        "score_contributions": {
          "heat_index": 0.0,
          "air_temperature": 0.0,
          "relative_humidity": 5.0,
          "estimated_bright_light_duration": 5.18,
          "elevated_heat_duration": 0.0,
          "uv_index": null
        },
        "triggered_by": [],
        "factor_details": [
          "Heat index is not calculated below 27°C; ambient temperature is 23.0°C and the screening band is lower.",
          "Air-temperature score: 0.0/100, 15% weight, 0.0 points in the normalized demo score.",
          "Relative-humidity score: 45.0/100, 10% weight, 5.0 points in the normalized demo score.",
          "Estimated bright-light exposure score: 23.3/100, 20% weight, 5.2 points in the normalized demo score.",
          "Elevated-heat duration score: 0.0/100, 10% weight, 0.0 points in the normalized demo score.",
          "Site UV index unavailable; the 10% UV component is omitted and the remaining score weights are normalized."
        ],
        "explanation": "No heat-index caution band is active and the demonstration score is 10.2/100, below the caution display threshold of 30.",
        "recommended_action": "Continue the site's planned water, rest, and monitoring practices. Recheck if conditions or the worker's condition changes.",
        "activity_level": "unknown",
        "acclimatization_status": "unknown",
        "model_version": "heat-exposure-demo-v4-uv",
        "reason": "heat_index_category"
      }
    }
  }],
  "alerts": [],
  "alerts_truncated": false,
  "server_time": "2026-11-14T04:00:00.200000Z"
}
```

All timestamps are UTC ISO 8601; convert for display if desired. Temperature readings
are °C, humidity is %, pulse estimates are BPM, RSSI is dBm. `heat_index_c` is the
NOAA/NWS heat index in °C when its equation applies; `heat_index_category` carries
the approximate screening band. Below 27°C the band is `lower`; if the equation is
outside its applicable range, the category is `unavailable`. Keep null as unavailable.

`light_intensity_pct` is normalized ADC brightness from the photoresistor, not lux,
UV, or direct solar radiation. Label its timer **estimated bright-light exposure**.
Configure `LIGHT_BRIGHT_IS_HIGHER`, `LIGHT_DARK_ORIENTED_RAW`,
`LIGHT_BRIGHT_ORIENTED_RAW`, and `SUNLIGHT_THRESHOLD_PERCENT` in
`firmware/include/config.h` using observed shade and bright-light readings. The
timer starts above that threshold, pauses in shade, and uses `millis()`; cumulative
shift totals persist across ESP32 restarts while the continuous timer resets when
the condition ends. The heat-exposure timer accumulates while the heat-index band
is Caution or higher.

`/v1/config` returns the initial Celsius heat-index bands: below 27°C Lower, 27–32°C
Caution, 32–41°C Extreme Caution, 41–54°C Danger, and above 54°C Extreme Danger.
These are approximate screening bands, not individualized worker-safety thresholds.
The NOAA heat index combines °F air temperature and relative humidity; firmware
converts the sensor's Celsius value to °F for calculation and back to °C for display.
The index does not account for direct sunlight, physical exertion, clothing, or
acclimatization.

`combined_risk_score` is a hackathon **demonstration score**. Its component values
and weights appear in `assessment.demo_score_components` and `/v1/config`: heat
index 35%, air temperature 15%, humidity 10%, estimated bright-light duration 20%,
elevated-heat duration 10%, and UV index 10%. The UV score is `min(UV index / 11,
1) * 100`; values at 11 or above saturate at 100. Duration components reach 100
after 30 minutes.
Known activity and acclimatization inputs add small, explicit demo adjustments;
when unavailable they are marked `unknown` and add zero. The score thresholds
(30/55/75) are demo display thresholds, not validated safety limits or medical
probabilities. It does not predict heat illness or replace an occupational heat-stress assessment.

For demo context only: activity contributes +0 for light/unknown, +2 for moderate,
or +4 for heavy. Acclimatization contributes +0 for acclimatized/unknown and +4
for not acclimatized. These are arbitrary score points used to make supplied
context visible in the demonstration, not physiological or safety thresholds.

## Alert explanation and action fields

Each `assessment` includes `explanation` (plain-language reason), `triggered_by`
(the heat-index category, combined demo score, and/or unavailable environmental
sensor), `factor_details` (readable values and score details), `score_contributions`
(normalized points contributed by weighted components), and `recommended_action`
(a general site-heat-plan precaution). Render these fields as the basic explanation
without waiting for generative AI. They are informational prototype guidance, not a
diagnosis or individualized medical instruction.

The teammate's Gemini feature can summarize `/v1/snapshot`, device history,
`risk_state`, `age_seconds`, and these structured assessment fields for questions
such as “who has the highest current screening indicators?” Keep the API's returned
assessment as the source of the displayed status; Gemini should summarize readings
and their age/source rather than invent thresholds, medical probabilities, or a
diagnosis. Clearly label simulator readings and flag offline/stale devices.

## Randomized risk-state demos

Use `POST /v1/demo/scenarios/{scenario}` with the dashboard key and no request body.
Supported values are `normal`, `caution`, `warning`, `critical`, and `unknown`. Every
request creates new randomized temperature, humidity, relative brightness, exposure
time, activity, and acclimatization values, then runs the same assessment used for
hardware samples. The response includes the generated sample and assessment. The
sample is stored with `source=simulator` and a `demo-test-{scenario}` device ID, so
it appears in snapshots/history and can be used to test frontend explanations and
Gemini answers. The endpoint returns 403 when `THERMOSAFE_ALLOW_SIMULATOR=false`.
The terminal test keys also call the physical alert preview endpoint for an online
hardware device. These short-lived previews affect only LED/buzzer output; they do
not replace or store simulated sensor readings on the ESP32. The hardware polls the
device-authenticated GET endpoint about once per second and drops the preview if
commands stop arriving. Live local alerts take priority over a less severe preview.
Dashboard clients can call the POST and DELETE routes with the dashboard key when
implementing explicit hardware tests.

## Display behavior

| Field or state | Interface behavior |
| --- | --- |
| `connection_state=offline` | Show Offline; show the last reading with an explicit stale label |
| `risk_state=unknown` | Show Unknown; do not color it green |
| `risk_state=normal/caution/warning/critical` | Use returned state instead of recalculating it |
| `environment_sensor_ok=false` | Show sensor error; latest temperature and humidity are null |
| `environment_sensor_ok=null` | Current sensor condition is unknown because device is offline |
| `source=simulator` | Prominently label the entire device as simulated |
| `pulse_quality=simulated` | Show the value as simulated; never present it as a sensor measurement |
| `pulse_quality=experimental` | Label the estimate experimental; no diagnosis or reliable health status |
| `pulse_quality=disabled/unvalidated/insufficient_signal` | Show unavailable BPM, optionally show raw signal for development |
| `light_sensor_ok=true` | Show relative photoresistor brightness; do not label it lux, UV, or solar radiation |
| `bright_light_active=true` | Threshold is active; display the estimated bright-light exposure timer |
| `assessment.heat_index_category` | Show the screening band and indicate when it is unavailable |
| `assessment.demo_score_components` | Explain the demonstration score; do not present it as medical or occupational guidance |
| `assessment.site_uv_index` / `assessment.uv_index_score` | Show site-forecast UV and its score component; mark unavailable when null |
| `risk_state` / `assessment.state` | Use the returned state; do not recalculate it in the interface |

For a local hardware check, open PlatformIO's serial monitor at 115200 baud.
The firmware prints temperature, humidity, heat-index value/category, componentized
demo score, photoresistor ADC/relative brightness, cumulative and continuous exposure timers,
raw pulse ADC value, and any experimental BPM estimate every second. Press `g`
to preview green/Normal, `y` for yellow/Caution, `r` for red/Critical plus buzzer,
and `a` to return to automatic sensor-driven alerts. Set demo activity with `i`
(light), `m` (moderate), or `h` (heavy); set acclimatization with `c` (acclimatized),
`n` (not acclimatized), or `u` (unknown for both). The context choices appear on
the local display and are included in telemetry. LED test commands change only the
local outputs; logged sensor data remains the real sensor input. BPM remains
experimental, and `unavailable` means the optical signal did not meet the simple
detector's checks. The pulse module needs steady finger contact; room light and
movement may still affect the reading.

When offline, top-level `risk_state` becomes Unknown but `latest.assessment` still
preserves the last historical calculation. Use the top-level state for current
worker cards. Time since the last sample is `age_seconds`.

Alert kinds are `heat`, `environment_sensor`, and `offline`. A heat alert opens
on combined Warning/Critical risk and resolves on a valid Normal/Caution sample. Sensor failure
and disconnection do not establish recovery; an existing heat alert stays active.
Escalation from Warning to Critical clears its acknowledgment. Acknowledgment records
that someone saw the alert; it does not resolve the condition or silence the clip.
Recheck the latest snapshot if acknowledgment returns 409 because the alert resolved.

## Device ingestion contract

Only firmware and the simulator call `POST /v1/telemetry`, using `X-Device-Key`.
The JSON input matches the fields inside `latest`, excluding `id`, `received_at`,
and `assessment`, which the backend calculates. Raw API payload fields may not be
invented by the dashboard. Simulated data must say `source=simulator`.

Device IDs cannot change source. Each reboot generates a new `boot_id`; sequence
and uptime increase within a boot. Duplicate packets are idempotent and do not
refresh last-seen timestamps. Reusing a sequence with different data, regressing
sequence/uptime, and reactivating a known retired boot return 409. Packets from an
unseen boot establish a new session; this is sequencing protection, not hardware
identity authentication. Old offline samples are not backfilled as live data.

## Errors and contract files

- 401: missing/incorrect role key.
- 403: simulator input disabled.
- 404: device/alert not found.
- 409: ordering conflict or already resolved alert.
- 422: invalid fields or inconsistent sensor data.

Error JSON has a `detail` property. CORS rejection appears as a browser/network
error. `docs/openapi.json` contains machine-readable input and response schemas;
the running backend serves the same contract at `/openapi.json` and Swagger `/docs`.
