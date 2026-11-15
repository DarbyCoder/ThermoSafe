"""Small cached Open-Meteo client for site UV and hourly forecast context."""

from datetime import datetime, timezone
import json
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class WeatherClient:
    def __init__(self, latitude: float, longitude: float, location_name: str,
                 cache_seconds: float = 600):
        self.latitude = latitude
        self.longitude = longitude
        self.location_name = location_name
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached = None

    def get_cached(self):
        """Return cached site conditions without making a network request."""
        with self._lock:
            if self._cached is None:
                return {"available": False, "current": None, "hourly": [], "stale": False}
            result = dict(self._cached)
            result["stale"] = time.monotonic() - self._cached_at >= self.cache_seconds
            return result

    def get(self):
        now = time.monotonic()
        with self._lock:
            if self._cached is not None and now - self._cached_at < self.cache_seconds:
                return self._cached
            params = urlencode({
                "latitude": self.latitude,
                "longitude": self.longitude,
                "current": "temperature_2m,relative_humidity_2m,uv_index",
                "hourly": "temperature_2m,relative_humidity_2m,uv_index",
                "forecast_days": 2,
                "timezone": "UTC",
            })
            request = Request(
                f"https://api.open-meteo.com/v1/forecast?{params}",
                headers={"User-Agent": "ThermoSafe/1.0"},
            )
            try:
                with urlopen(request, timeout=5) as response:
                    data = json.load(response)
                current = data.get("current") or {}
                hourly = data.get("hourly") or {}
                times = hourly.get("time") or []
                temperature = hourly.get("temperature_2m") or []
                humidity = hourly.get("relative_humidity_2m") or []
                uv_values = hourly.get("uv_index") or []
                points = []
                for index, stamp in enumerate(times):
                    if index >= min(len(temperature), len(humidity), len(uv_values)):
                        break
                    if any(value[index] is None for value in (temperature, humidity, uv_values)):
                        continue
                    points.append({
                        "time": stamp,
                        "temperature_c": temperature[index],
                        "humidity_pct": humidity[index],
                        "uv_index": uv_values[index],
                    })
                result = {
                    "available": True,
                    "provider": "Open-Meteo",
                    "location": self.location_name,
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "current": {
                        "time": current.get("time"),
                        "temperature_c": current.get("temperature_2m"),
                        "humidity_pct": current.get("relative_humidity_2m"),
                        "uv_index": current.get("uv_index"),
                    },
                    "hourly": points,
                    "uv_metric": "forecast UV index; site-level weather estimate, not a wearable measurement",
                    "attribution": "Weather data by Open-Meteo (https://open-meteo.com/)",
                }
                self._cached = result
                self._cached_at = now
                return result
            except Exception:
                # Keep a prior response available during a temporary provider outage.
                if self._cached is not None:
                    stale = dict(self._cached)
                    stale["stale"] = True
                    return stale
                return {
                    "available": False,
                    "provider": "Open-Meteo",
                    "location": self.location_name,
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "fetched_at": None,
                    "current": None,
                    "hourly": [],
                    "uv_metric": "forecast UV index; site-level weather estimate, not a wearable measurement",
                    "unavailable_reason": "Weather service could not be reached or returned invalid data.",
                }
