"""Configuration is loaded once, at application startup."""

from dataclasses import dataclass
import math
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    device_key: str
    dashboard_key: str
    db_path: Path
    tigerdata_dsn: str = ""
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://localhost:3000")
    offline_seconds: float = 20
    allow_simulator: bool = True
    weather_latitude: float = 42.3912
    weather_longitude: float = -72.5262
    weather_location_name: str = "UMass Amherst (demo default)"

    def __post_init__(self):
        if any(not key or key.startswith("replace-") for key in (self.device_key, self.dashboard_key)):
            raise ValueError("Set distinct THERMOSAFE_DEVICE_KEY and THERMOSAFE_DASHBOARD_KEY in .env")
        if self.device_key == self.dashboard_key:
            raise ValueError("Device and dashboard keys must be different")
        if self.offline_seconds < 10:
            raise ValueError("Offline timeout must be at least 10 seconds")
        if "*" in self.cors_origins:
            raise ValueError("Configure exact dashboard origins instead of a wildcard")
        if (not math.isfinite(self.weather_latitude) or not math.isfinite(self.weather_longitude)
                or not -90 <= self.weather_latitude <= 90 or not -180 <= self.weather_longitude <= 180):
            raise ValueError("Weather coordinates are outside valid latitude/longitude ranges")

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        path = Path(os.getenv("THERMOSAFE_DB", "data/thermosafe.sqlite3"))
        return cls(
            device_key=os.getenv("THERMOSAFE_DEVICE_KEY", ""),
            dashboard_key=os.getenv("THERMOSAFE_DASHBOARD_KEY", ""),
            db_path=path if path.is_absolute() else ROOT / path,
            tigerdata_dsn=os.getenv("THERMOSAFE_TIGERDATA_DSN", "").strip(),
            cors_origins=tuple(x.strip() for x in os.getenv(
                "THERMOSAFE_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
            ).split(",") if x.strip()),
            offline_seconds=float(os.getenv("THERMOSAFE_OFFLINE_SECONDS", "20")),
            allow_simulator=os.getenv("THERMOSAFE_ALLOW_SIMULATOR", "true").lower() == "true",
            weather_latitude=float(os.getenv("THERMOSAFE_WEATHER_LATITUDE", "42.3912")),
            weather_longitude=float(os.getenv("THERMOSAFE_WEATHER_LONGITUDE", "-72.5262")),
            weather_location_name=os.getenv("THERMOSAFE_WEATHER_LOCATION_NAME", "UMass Amherst (demo default)"),
        )
