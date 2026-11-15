"""Generate fresh, clearly marked randomized readings for risk-state demos."""

import random
import uuid
from typing import Literal

from .models import Telemetry
from .risk import assess

DemoScenario = Literal["normal", "caution", "warning", "critical", "unknown"]
SCENARIOS: tuple[DemoScenario, ...] = ("normal", "caution", "warning", "critical", "unknown")
_RNG = random.SystemRandom()


def make_demo_reading(scenario: DemoScenario, device_id: str,
                      site_uv_index: float | None = None) -> Telemetry:
    """Make new random criteria that resolve to the requested demo state."""
    for _ in range(5000):
        if scenario == "unknown":
            temperature = humidity = None
            bright_seconds = _RNG.randint(0, 300)
            heat_seconds = _RNG.randint(0, 300)
            activity = _RNG.choice(("unknown", "light"))
            acclimatization = _RNG.choice(("unknown", "acclimatized"))
        elif scenario == "normal":
            temperature = round(_RNG.uniform(18.0, 26.5), 1)
            humidity = round(_RNG.uniform(25.0, 90.0), 1)
            bright_seconds = _RNG.randint(0, 600)
            heat_seconds = _RNG.randint(0, 600)
            activity = _RNG.choice(("unknown", "light"))
            acclimatization = _RNG.choice(("unknown", "acclimatized"))
        elif scenario == "caution":
            temperature = round(_RNG.uniform(27.0, 31.0), 1)
            humidity = round(_RNG.uniform(25.0, 90.0), 1)
            bright_seconds = _RNG.randint(0, 900)
            heat_seconds = _RNG.randint(0, 900)
            activity = _RNG.choice(("light", "moderate"))
            acclimatization = _RNG.choice(("unknown", "acclimatized"))
        elif scenario == "warning":
            temperature = round(_RNG.uniform(30.0, 38.0), 1)
            humidity = round(_RNG.uniform(25.0, 90.0), 1)
            bright_seconds = _RNG.randint(0, 900)
            heat_seconds = _RNG.randint(0, 900)
            activity = _RNG.choice(("light", "moderate"))
            acclimatization = _RNG.choice(("unknown", "acclimatized"))
        else:
            temperature = round(_RNG.uniform(33.0, 44.0), 1)
            humidity = round(_RNG.uniform(30.0, 90.0), 1)
            bright_seconds = _RNG.randint(0, 1800)
            heat_seconds = _RNG.randint(0, 1800)
            activity = _RNG.choice(("light", "moderate", "heavy"))
            acclimatization = _RNG.choice(("unknown", "acclimatized", "not_acclimatized"))

        intensity = round(_RNG.uniform(10.0, 99.0), 1)
        threshold = 60.0
        bright_continuous = _RNG.randint(0, bright_seconds)
        heat_continuous = _RNG.randint(0, heat_seconds)
        reading = Telemetry(
            schema_version=1,
            device_id=device_id,
            boot_id=uuid.uuid4().hex,
            sequence=0,
            uptime_ms=0,
            source="simulator",
            firmware_version="demo-scenario-1",
            temperature_c=temperature,
            humidity_pct=humidity,
            environment_sensor_ok=temperature is not None,
            pulse_raw=None,
            pulse_bpm=None,
            pulse_quality="disabled",
            light_sensor_ok=True,
            light_raw=round(intensity * 4095 / 100),
            light_intensity_pct=intensity,
            bright_light_threshold_pct=threshold,
            bright_light_active=intensity >= threshold,
            bright_exposure_seconds=bright_seconds,
            bright_continuous_seconds=bright_continuous,
            heat_exposure_seconds=heat_seconds,
            heat_continuous_seconds=heat_continuous,
            activity_level=activity,
            acclimatization_status=acclimatization,
            wifi_rssi_dbm=_RNG.randint(-78, -45),
        )
        if assess(temperature, humidity, bright_seconds, heat_seconds,
                  activity, acclimatization, site_uv_index)["state"] == scenario:
            return reading

    raise RuntimeError(f"Could not create a randomized {scenario} test reading")
