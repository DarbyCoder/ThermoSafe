"""Readable live terminal display backed by the local ThermoSafe API."""

import argparse
import curses
import json
import sys
import time
import textwrap
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import Settings


def number(value, suffix="", digits=2):
    if value is None:
        return "--"
    if "%" in suffix:
        digits = 0
    rendered = f"{value:.{digits}f}"
    if digits:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"{rendered}{suffix}"


def duration(seconds):
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def fetch_devices(api_url, dashboard_key):
    request = Request(api_url.rstrip("/") + "/v1/devices",
                      headers={"X-Dashboard-Key": dashboard_key})
    with urlopen(request, timeout=0.8) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_weather(api_url, dashboard_key):
    request = Request(api_url.rstrip("/") + "/v1/weather",
                      headers={"X-Dashboard-Key": dashboard_key})
    with urlopen(request, timeout=6.0) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_tigerdata_status(api_url, dashboard_key):
    request = Request(api_url.rstrip("/") + "/v1/tigerdata/status",
                      headers={"X-Dashboard-Key": dashboard_key})
    with urlopen(request, timeout=0.8) as response:
        return json.loads(response.read().decode("utf-8"))


def save_worker_name(api_url, dashboard_key, device_id, name):
    request = Request(api_url.rstrip("/") + f"/v1/devices/{device_id}",
                      data=json.dumps({"worker_name": name}).encode("utf-8"),
                      method="PATCH",
                      headers={"X-Dashboard-Key": dashboard_key,
                               "Content-Type": "application/json"})
    with urlopen(request, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def prompt_worker_names(api_url, dashboard_key):
    try:
        payload = fetch_devices(api_url, dashboard_key)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        print("Backend unavailable; worker names can be assigned when the tracker is run again.")
        return

    workers = [device for device in payload.get("items", [])
               if device.get("source") == "hardware"
               and device.get("connection_state") == "online"]
    if not workers:
        print("No online ESP32 found yet. Start the tracker after the ESP32 connects to assign its worker name.")
        return

    for device in workers:
        device_id = device["device_id"]
        current_name = (device.get("worker_name") or "").strip()
        prompt = f"Worker name for {device_id}"
        if current_name:
            prompt += f" [{current_name}]"
        prompt += " (Enter keeps saved name): "
        try:
            entered = input(prompt).strip()
        except EOFError:
            return
        name = entered or current_name
        if not name or name == current_name:
            continue
        try:
            save_worker_name(api_url, dashboard_key, device_id, name)
            print(f"Saved {name} for {device_id}.")
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            print(f"Could not save name for {device_id}: {type(error).__name__}.")


def uv_status(weather):
    if weather is None:
        return "waiting for site weather"
    current = weather.get("current") or {}
    uv = current.get("uv_index")
    location = weather.get("location") or "configured site"
    if uv is None:
        return f"unavailable | {location}"
    freshness = "STALE" if weather.get("stale") else "Open-Meteo site forecast"
    return f"{uv:.1f} | {freshness} | {location}"


def send_demo_scenario(api_url, dashboard_key, scenario):
    request = Request(api_url.rstrip("/") + f"/v1/demo/scenarios/{scenario}",
                      data=b"", method="POST",
                      headers={"X-Dashboard-Key": dashboard_key})
    with urlopen(request, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def send_physical_alert_test(api_url, dashboard_key, device_id, scenario):
    request = Request(api_url.rstrip("/") + f"/v1/devices/{device_id}/alert-test/{scenario}",
                      data=b"", method="POST",
                      headers={"X-Dashboard-Key": dashboard_key})
    with urlopen(request, timeout=1.5) as response:
        return json.loads(response.read().decode("utf-8"))


def clear_physical_alert_test(api_url, dashboard_key, device_id):
    request = Request(api_url.rstrip("/") + f"/v1/devices/{device_id}/alert-test",
                      data=b"", method="DELETE",
                      headers={"X-Dashboard-Key": dashboard_key})
    with urlopen(request, timeout=1.5) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_device(payload, selected_device_id=None):
    devices = payload.get("items", [])
    if not devices:
        return None
    if selected_device_id:
        selected = next((item for item in devices if item["device_id"] == selected_device_id), None)
        if selected:
            return selected
    online_hardware = [item for item in devices
                       if item["source"] == "hardware" and item["connection_state"] == "online"]
    if online_hardware:
        return min(online_hardware, key=lambda item: item.get("age_seconds", float("inf")))
    return min(devices, key=lambda item: (item["connection_state"] != "online",
                                          item.get("age_seconds", float("inf"))))


def mesh_roster(devices):
    hardware = [item for item in devices if item.get("source") == "hardware"]
    if not hardware:
        return "no hardware ESP32 telemetry yet"
    online = sum(item.get("connection_state") == "online" for item in hardware)
    entries = []
    for item in hardware:
        latest = item.get("latest", {})
        name = item.get("worker_name") or item.get("device_id", "ESP32")
        role = latest.get("mesh_role", "disabled")
        if role == "disabled":
            mesh_state = "mesh off"
        elif latest.get("mesh_connected"):
            mesh_state = "mesh connected"
        else:
            mesh_state = "mesh waiting"
        connection = item.get("connection_state", "unknown")
        entries.append(f"{name}: {connection}, {role}, {mesh_state}")
    return f"{online}/{len(hardware)} online | " + " | ".join(entries)


def make_rows(device, weather=None, tiger_status=None, devices=None):
    devices = devices or []
    roster = mesh_roster(devices)
    if device is None:
        return [
            ("CONNECTION", "No device telemetry yet", "normal"),
            ("OVERALL RISK", "waiting", "normal"),
            ("DEVICE", "Waiting for ESP32", "normal"),
            ("WORKER", "Waiting for ESP32", "normal"),
            ("MESH FLEET", roster, "normal"),
            ("TIGERDATA", tiger_status_text(tiger_status), "normal"),
            ("UV INDEX", uv_status(weather), "normal"),
        ]

    latest = device["latest"]
    assessment = latest["assessment"]
    risk = device["risk_state"].upper()
    age = device.get("age_seconds", 0)
    temp = latest.get("temperature_c")
    humidity = latest.get("humidity_pct")
    light = latest.get("light_intensity_pct")
    pulse_bpm = latest.get("pulse_bpm")
    pulse_quality = latest.get("pulse_quality", "unknown")
    if pulse_bpm is None:
        bpm_text = f"unavailable ({pulse_quality})"
    else:
        bpm_text = f"{pulse_bpm:.2f}".rstrip("0").rstrip(".") + f" ({pulse_quality})"

    led = {"NORMAL": "GREEN", "CAUTION": "YELLOW", "WARNING": "ORANGE",
           "CRITICAL": "RED"}.get(risk, "--")
    heat_index = assessment.get("heat_index_c")
    heat_index_text = (f"{heat_index:.1f} C | {assessment.get('heat_index_category', 'unavailable')}"
                       if heat_index is not None else assessment.get("heat_index_category", "unavailable"))
    brightness_status = "BRIGHT" if latest.get("bright_light_active") else "below threshold"
    if device["connection_state"] == "offline":
        explanation = "Device is offline; its last environmental sample is stale, so current risk is unknown."
        recommended_action = ("Check the device connection and sensor. Follow the site heat-safety plan "
                              "until current readings return.")
    else:
        explanation = assessment.get("explanation", "No explanation provided.")
        recommended_action = assessment.get("recommended_action", "Follow the site heat-safety plan.")

    return [
        ("CONNECTION", f"{device['connection_state'].upper()} | last update {age:.1f}s ago | {device['source'].upper()}", "normal"),
        ("DEVICE", device["device_id"], "normal"),
        ("OVERALL RISK", risk.lower(), "risk"),
        ("DEMO SCORE", f"{assessment.get('combined_risk_score', 0):.1f}/100", "normal"),
        ("WORKER", device.get("worker_name") or "Name not assigned", "normal"),
        ("MESH FLEET", roster, "normal"),
        ("MESH NODE", f"{latest.get('mesh_role', 'disabled')} | node {latest.get('mesh_node_id') or '--'} | peers {', '.join(str(node) for node in latest.get('mesh_node_ids', [])) or '--'}", "normal"),
        ("LOCATION", f"{number(latest.get('latitude_deg'), '°')}, {number(latest.get('longitude_deg'), '°')} ({latest.get('location_source', 'unavailable')})", "normal"),
        ("TIGERDATA", tiger_status_text(tiger_status), "normal"),
        ("ACTIVITY", f"{latest.get('activity_level', 'unknown')} | acclim: {latest.get('acclimatization_status', 'unknown')}", "normal"),
        ("HEAT INDEX", heat_index_text, "normal"),
        ("TEMPERATURE", number(temp, " C"), "normal"),
        ("HUMIDITY", number(humidity, " %"), "normal"),
        ("UV INDEX", uv_status(weather), "normal"),
        ("UV SCORE", f"{number(assessment.get('demo_score_components', {}).get('uv_index'))}/100 | 10% of demo score", "normal"),
        ("BRIGHTNESS", f"{number(light, '%')} raw {latest.get('light_raw', '--')} ADC", "normal"),
        ("BRIGHT STATUS", f"{brightness_status} (threshold {number(latest.get('bright_light_threshold_pct'), '%')})", "normal"),
        ("BRIGHT TOTAL", duration(latest.get("bright_exposure_seconds")), "normal"),
        ("BRIGHT NOW", duration(latest.get("bright_continuous_seconds")), "normal"),
        ("HEAT TOTAL", duration(latest.get("heat_exposure_seconds")), "normal"),
        ("HEAT NOW", duration(latest.get("heat_continuous_seconds")), "normal"),
        ("HEART RATE", bpm_text, "normal"),
        ("PULSE INPUT", f"{latest.get('pulse_raw', '--')} ADC", "normal"),
        ("LED", f"{led} (AUTO risk indicator)", "led"),
        ("SENSOR", "OK" if latest.get("environment_sensor_ok") else "CHECK SENSOR", "normal"),
        ("WHY", explanation, "normal"),
        *[(f"FACTOR {index}", detail, "normal")
          for index, detail in enumerate(assessment.get("factor_details", []), start=1)],
        ("ACTION", recommended_action, "normal"),
        ("UPDATE", "screen refresh 1s | ESP telemetry about every 1s", "normal"),
    ]


def tiger_status_text(status):
    if status is None:
        return "status unavailable"
    pending = status.get("pending_count", 0)
    state = status.get("state", "unknown")
    if state == "not_configured":
        return f"not configured | {pending} rows queued locally"
    if state == "connected":
        return f"connected | {pending} queued | last upload {status.get('last_success_at') or '--'}"
    if state == "retrying":
        return f"retrying ({status.get('last_error') or 'error'}) | {pending} queued"
    return f"connecting | {pending} queued"


def draw(stdscr, api_url, dashboard_key, poll_seconds):
    curses.curs_set(0)
    stdscr.nodelay(True)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)

    device = None
    selected_device_id = None
    test_status = ""
    hardware_test_device_id = None
    api_status = "connecting to local backend"
    latest_payload = {"items": []}
    next_poll = 0.0
    weather = None
    tiger_status = None
    next_weather_poll = 0.0
    next_tiger_poll = 0.0
    while True:
        now = time.monotonic()
        if now >= next_poll:
            try:
                latest_payload = fetch_devices(api_url, dashboard_key)
                device = latest_device(latest_payload, selected_device_id)
                if selected_device_id is None and device and device.get("source") == "hardware":
                    selected_device_id = device["device_id"]
                api_status = "connected" if device else "waiting for device telemetry"
            except HTTPError as error:
                api_status = f"API HTTP {error.code}"
            except (URLError, TimeoutError, OSError, ValueError) as error:
                api_status = f"backend unavailable ({type(error).__name__})"
            next_poll = now + poll_seconds

        if now >= next_weather_poll:
            try:
                weather = fetch_weather(api_url, dashboard_key)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                weather = {"available": False, "current": None,
                           "location": "site forecast unavailable"}
            # The backend caches provider data for ten minutes; refresh this view
            # less often so it does not make a weather request on each screen tick.
            next_weather_poll = now + 60.0

        if now >= next_tiger_poll:
            try:
                tiger_status = fetch_tigerdata_status(api_url, dashboard_key)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                tiger_status = None
            next_tiger_poll = now + 2.0

        stdscr.erase()
        height, width = stdscr.getmaxyx()
        title = "THERMOSAFE | LIVE STATUS"
        stdscr.addstr(0, max(0, (width-len(title))//2), title[:max(0, width-1)],
                      curses.A_BOLD | curses.color_pair(4))
        if height > 1:
            stdscr.addstr(1, 1, "=" * max(0, width-3))

        rows = make_rows(device, weather, tiger_status, latest_payload.get("items", []))
        risk = device["risk_state"].lower() if device else "unknown"
        risk_color = curses.color_pair(1)
        if risk in ("caution", "warning"):
            risk_color = curses.color_pair(2)
        elif risk == "critical":
            risk_color = curses.color_pair(3)

        index = 3
        for label, value, style_name in rows:
            start = min(17, max(0, width-1))
            wrapped = textwrap.wrap(str(value), max(1, width-start-1)) or [""]
            style = (risk_color | curses.A_BOLD if style_name == "risk" else
                     risk_color | curses.A_BOLD if style_name == "led" else curses.A_NORMAL)
            for line_number, line in enumerate(wrapped):
                if index >= height-3:
                    break
                row_label = label if line_number == 0 else ""
                stdscr.addstr(index, 2, f"{row_label:<13}"[:max(0, width-3)], curses.A_DIM)
                stdscr.addstr(index, start, line[:max(0, width-start-1)], style)
                index += 1
            if index >= height-3:
                break

        if height > 2:
            footer = "[ / ] worker  1 Normal  2 Caution  3 Warning  4 Critical  5 Unknown  0 Live  |  Q quit"
            stdscr.addstr(height-2, 1, footer[:max(0, width-2)], curses.A_DIM)
            if height > 1 and test_status:
                stdscr.addstr(height-1, 1, f"API: {api_status} | {test_status}"[:max(0, width-2)], curses.A_DIM)
            elif height > 1:
                stdscr.addstr(height-1, 1, f"API: {api_status} | Demo values are simulator data; they do not change the ESP32."[:max(0, width-2)], curses.A_DIM)
        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            break
        if key in (ord("["), ord("]")):
            workers = [item for item in latest_payload.get("items", [])
                       if item.get("source") == "hardware"]
            if workers:
                current_index = next((i for i, item in enumerate(workers)
                                      if item["device_id"] == selected_device_id), -1)
                step = -1 if key == ord("[") else 1
                selected_device_id = workers[(current_index + step) % len(workers)]["device_id"]
                device = latest_device(latest_payload, selected_device_id)
        if key == ord("0"):
            if hardware_test_device_id:
                try:
                    clear_physical_alert_test(api_url, dashboard_key, hardware_test_device_id)
                    test_status = "Physical test cleared; returning to live ESP32 alerts."
                except (HTTPError, URLError, TimeoutError, OSError):
                    test_status = "Returning to live display; hardware test will expire automatically."
            selected_device_id = None
        elif key in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
            scenario = {ord("1"): "normal", ord("2"): "caution", ord("3"): "warning",
                        ord("4"): "critical", ord("5"): "unknown"}[key]
            try:
                result = send_demo_scenario(api_url, dashboard_key, scenario)
                selected_device_id = result["device_id"]
                test_status = f"Fresh randomized {scenario.upper()} sample generated (SIMULATED)."
                api_status = "connected"
                devices = fetch_devices(api_url, dashboard_key)
                latest_payload = devices
                device = latest_device(devices, selected_device_id)
                hardware = latest_device(devices)
                if hardware and hardware["source"] == "hardware" and hardware["connection_state"] == "online":
                    try:
                        send_physical_alert_test(api_url, dashboard_key, hardware["device_id"], scenario)
                        hardware_test_device_id = hardware["device_id"]
                        test_status += " Physical LED/buzzer preview sent; stronger live alerts take priority."
                    except (HTTPError, URLError, TimeoutError, OSError):
                        test_status += " Physical indicator command failed; backend-only sample shown."
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as error:
                test_status = f"Could not create demo: {type(error).__name__}"
                api_status = "check backend / simulator setting"
        time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description="ThermoSafe live API status display")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000",
                        help="Base URL of the local ThermoSafe backend")
    parser.add_argument("--poll-seconds", type=float, default=1.0,
                        help="How often to refresh the display")
    args = parser.parse_args()
    settings = Settings.from_env()
    prompt_worker_names(args.api_url, settings.dashboard_key)
    curses.wrapper(draw, args.api_url, settings.dashboard_key, max(0.5, args.poll_seconds))


if __name__ == "__main__":
    main()
