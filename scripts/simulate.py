"""Explicitly labeled simulated devices for dashboard integration; no hardware needed."""

import argparse
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def reading(device_id, boot_id, sequence, elapsed, temperature, humidity):
    return {"schema_version": 1, "device_id": device_id, "boot_id": boot_id,
            "sequence": sequence, "uptime_ms": int(elapsed*1000), "source": "simulator",
            "firmware_version": "simulator-0.1.0", "environment_sensor_ok": temperature is not None,
            "temperature_c": temperature, "humidity_pct": humidity,
            "pulse_raw": None, "pulse_bpm": None, "pulse_quality": "disabled", "wifi_rssi_dbm": -55}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--steps", type=int, default=0, help="0 runs until Ctrl+C")
    parser.add_argument("--scenario", choices=["steady", "cycle", "sensor-failure", "disconnect"], default="steady")
    args = parser.parse_args()
    if args.interval < 1 or args.steps < 0:
        parser.error("interval must be at least 1 second; steps must be nonnegative")
    load_dotenv(ROOT / ".env")
    key = os.getenv("THERMOSAFE_DEVICE_KEY")
    if not key:
        raise SystemExit("Run python -m scripts.setup first.")
    boot = uuid.uuid4().hex
    started = time.monotonic()
    sequence = 0
    # Demonstrates all four colors. Every device is marked source=simulator.
    presets = [(23.0, 45.0), (28.0, 50.0), (32.0, 55.0), (37.0, 65.0)]
    try:
        while not args.steps or sequence < args.steps:
            elapsed = time.monotonic()-started
            cycle_phase = int(elapsed//20) % 4
            for index, (temperature, humidity) in enumerate(presets):
                if args.scenario == "disconnect" and index == 3 and elapsed >= 15:
                    continue
                if args.scenario == "cycle" and index == 3:
                    temperature, humidity = presets[cycle_phase]
                elif args.scenario == "sensor-failure" and index == 3 and 15 <= elapsed%60 < 45:
                    temperature = humidity = None
                if temperature is not None:
                    temperature = round(temperature+.08*math.sin(elapsed/10+index), 2)
                payload = reading(f"demo-{index+1:02d}", boot, sequence, elapsed, temperature, humidity)
                request = urllib.request.Request(args.url.rstrip("/")+"/v1/telemetry",
                    data=json.dumps(payload, allow_nan=False).encode(),
                    headers={"Content-Type": "application/json", "X-Device-Key": key}, method="POST")
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        receipt = json.load(response)
                    print(f"{payload['device_id']}: {receipt['assessment']['state']} (simulated)", flush=True)
                except urllib.error.HTTPError as exc:
                    print(f"API HTTP {exc.code}: {exc.read().decode()[:300]}", flush=True)
                    if exc.code in (401, 403, 409, 422):
                        raise SystemExit("Fix the API configuration or simulator input before retrying.")
                except (urllib.error.URLError, TimeoutError) as exc:
                    print(f"Backend unavailable: {type(exc).__name__}; retrying next cycle.", flush=True)
            sequence += 1
            if not args.steps or sequence < args.steps:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Simulator stopped; devices will become offline after the timeout.")


if __name__ == "__main__":
    main()
