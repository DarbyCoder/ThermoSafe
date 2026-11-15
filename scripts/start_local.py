"""Run the ThermoSafe telemetry API and its map/call dashboard together."""

from __future__ import annotations

import signal
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    api = None
    try:
        with urlopen("http://127.0.0.1:8000/health", timeout=1):
            print("Using the already running ThermoSafe API on port 8000.", flush=True)
    except Exception:
        api = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:create_app", "--factory",
             "--host", "0.0.0.0", "--port", "8000"], cwd=ROOT
        )
    dashboard = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if api is not None and api.poll() is not None:
                raise RuntimeError("ThermoSafe API stopped during startup; check its configuration above.")
            try:
                with urlopen("http://127.0.0.1:8000/health", timeout=1):
                    break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("ThermoSafe API did not become ready within 30 seconds.")
        dashboard_env = os.environ.copy()
        dashboard_env.setdefault("TUNNEL", "off")
        dashboard = subprocess.Popen(
            [sys.executable, "thermo_server.py", "--host", "0.0.0.0", "--port", "8001"],
            cwd=ROOT, env=dashboard_env,
        )
        print("Open the live map at http://localhost:8001 (API: http://localhost:8000)", flush=True)
        while (api is None or api.poll() is None) and dashboard.poll() is None:
            time.sleep(0.5)
        return dashboard.poll() or (api.poll() if api is not None else 0) or 0
    except KeyboardInterrupt:
        return 0
    finally:
        for child in (dashboard, api):
            if child is not None and child.poll() is None:
                child.send_signal(signal.SIGTERM)
        for child in (dashboard, api):
            if child is not None:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
