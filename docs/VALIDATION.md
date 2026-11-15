# Implementation checks

The checks below cover the pre-UV baseline unless stated otherwise. The UV update
was compiled, built, uploaded to the ESP32, and checked against the running system
on 2026-11-15. The full automated suite was not rerun for that update.

The implementation was checked with Python 3.14.8, Node.js 24.21.0,
TypeScript 5.9.3, and PlatformIO 6.2.0 on this machine.

## Passed

- `python -m pytest -q`: 34 tests passed. The installed Starlette version emitted
  one deprecation warning about its httpx test adapter; no test failed.
- Native C++ compilation of the actual `firmware/include/heat_risk.h` and
  `pulse_estimator.h`, with compiler warnings treated as errors.
- 460 C++/Python comparisons of the heat equation and state classification,
  including boundary cases. Flat/saturated optical input produced no pulse estimate.
- TypeScript client checked with `tsc --strict --noEmit` against ES2020 and DOM APIs.
- Actual HTTP simulator run against Uvicorn: four explicitly simulated devices
  were accepted with Normal, Caution, Warning, and Critical states.
- Actual HTTP snapshot after the timeout: all four devices became Offline/Unknown,
  and unresolved heat alerts remained visible.
- Compiled TypeScript client exercised against the live HTTP backend: snapshot,
  model config, metadata update, history, alert acknowledgment, and rejection of
  an invalid dashboard key.

The backend used for the baseline HTTP checks was shut down after verification.

## Firmware build

`PLATFORMIO_CORE_DIR="$PWD/.platformio" pio run` in `firmware` passed for
`esp32dev`, using Espressif32 6.12.0 / Arduino ESP32 2.0.17. The complete firmware
compiled and linked successfully. Reported RAM use was 47,024 bytes (14.4%) and
flash use was 933,577 bytes (71.2% of the application partition).

## UV update and live hardware check (2026-11-15)

- Python modules compiled with `.venv/bin/python -m compileall -q backend scripts/serial_dashboard.py`.
- The UV-aware model reports version `heat-exposure-demo-v4-uv`; its six weights sum to 1.00.
- PlatformIO firmware upload to `/dev/cu.usbserial-0001` completed successfully. The ESP32 rebooted and continued sending telemetry and authenticated status polls to the backend, each returning HTTP 200.
- The backend was restarted with the UV-aware code. The live monitor showed hardware device `clip-aceaba470968` online, current temperature/humidity/light readings, heart-rate unavailable due to insufficient signal, a normal calculated risk, and the Open-Meteo UV rows.
- The alert preview path was exercised from the terminal monitor; a randomized caution sample was shown and the physical alert preview was sent. The monitor was returned to the live hardware device.
- The live UV was 0.0 at the time of the check, so no daytime UV contribution was visible. The scoring component is included; confirm a daytime site forecast for a non-zero UV contribution.
- The photoresistor was recalibrated to oriented ADC 2800–3400, flashed successfully, and shown at 40.7% / 3047 ADC in the live monitor, below the 60% bright threshold. The tracker saved the connected device's worker name as Adam in SQLite and displayed it.
- A TigerData durable outbox, retry worker, schema creation, and status endpoint were added. The local `.env` has no Tiger Cloud connection URI, so remote database connectivity and row insertion are pending credentials; queued rows remain local until configured.

## Hardware checks still required

Confirm each GPIO assignment and module polarity on the actual assembly, shine a
lamp on the photoresistor to confirm the transition above the 60% threshold, and
check the buzzer's warning/critical patterns in person. Wi-Fi reconnection, battery
behavior, pulse signal quality, and daytime UV behavior still need hands-on checks.
The heart-rate channel remained unavailable during this check. Automated software
tests do not establish protective or medical performance.

`requirements-dev.lock.txt` records the resolved Python environment used for
verification, including PlatformIO tools. `requirements.txt` pins the three direct
runtime dependencies. `firmware/platformio.ini` pins the ESP32 platform and libraries.
