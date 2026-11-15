"""Explainable, short-horizon heat-index trend estimates for the prototype."""

from datetime import datetime, timezone
import math

from .risk import heat_index_f

HEAT_INDEX_THRESHOLDS_C = (27.0, 32.0, 41.0, 54.0)
FORECAST_CATEGORY = {27.0: "caution", 32.0: "extreme_caution", 41.0: "danger", 54.0: "extreme_danger"}
TREND_LOOKBACK_SECONDS = 600
MIN_TREND_POINTS = 5
MIN_TREND_SPAN_SECONDS = 30
MAX_TREND_EXTRAPOLATION_MINUTES = 60


def _effective_heat_index(temperature_c, humidity_pct):
    if temperature_c is None or humidity_pct is None:
        return None
    fahrenheit = heat_index_f(temperature_c * 1.8 + 32, humidity_pct)
    hi_c = (fahrenheit - 32) / 1.8 if fahrenheit is not None else None
    # Below the equation's operating range, ambient temperature determines the
    # lower screening band; it is not presented as a calculated heat index.
    return max(temperature_c, hi_c if hi_c is not None else temperature_c)


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return None


def _linear_slope(points):
    if len(points) < MIN_TREND_POINTS:
        return None, "Need at least five recent valid sensor samples."
    start = points[0][0]
    span = points[-1][0] - start
    if span < MIN_TREND_SPAN_SECONDS:
        return None, "Recent valid samples do not yet span 30 seconds."
    xs = [(stamp - start) / 60.0 for stamp, _ in points]
    ys = [value for _, value in points]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator <= 0:
        return None, "Recent samples do not provide a usable time trend."
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
    return slope, None


def build_prediction(device, readings, weather, now_epoch, hours=12):
    latest = device["latest"]
    age = max(0.0, now_epoch - _parse_time(latest["received_at"])) if _parse_time(latest["received_at"]) else None
    current_hi = _effective_heat_index(latest.get("temperature_c"), latest.get("humidity_pct"))
    base = {
        "device_id": device["device_id"],
        "worker_name": device.get("worker_name"),
        "source": device["source"],
        "current_heat_index_estimate_c": round(current_hi, 1) if current_hi is not None else None,
        "current_reading_age_seconds": round(age, 1) if age is not None else None,
        "current_conditions": {
            "temperature_c": latest.get("temperature_c"),
            "humidity_pct": latest.get("humidity_pct"),
            "risk_state": device.get("risk_state"),
            "estimated_bright_light_exposure_seconds": latest.get("bright_exposure_seconds"),
            "elevated_heat_exposure_seconds": latest.get("heat_exposure_seconds"),
            "activity_level": latest.get("activity_level"),
            "acclimatization_status": latest.get("acclimatization_status"),
        },
        "next_threshold": None,
        "sensor_trend": {"available": False, "slope_c_per_minute": None,
                          "crossing_in_minutes": None, "reason": None},
        "weather_forecast": {"available": bool(weather.get("available")),
                             "crossing_at": None, "crossing_in_minutes": None,
                             "provider": weather.get("provider")},
        "earliest_crossing_basis": None,
        "method": "Observed heat-index trend and site forecast are shown separately. This is a demonstration estimate, not a heat-illness forecast.",
        "recommended_action": "Continue the site's heat-safety plan and reassess when new readings arrive.",
        "horizon_hours": hours,
    }
    if current_hi is None or latest.get("environment_sensor_ok") is not True:
        base["sensor_trend"]["reason"] = "Required temperature/humidity reading is unavailable."
        return base
    if device["connection_state"] != "online" or age is None or age >= 30:
        base["sensor_trend"]["reason"] = "Latest device reading is stale or the device is offline."
        return base

    threshold = next((value for value in HEAT_INDEX_THRESHOLDS_C if value > current_hi), None)
    if threshold is not None:
        base["next_threshold"] = {"heat_index_c": threshold,
                                   "category": FORECAST_CATEGORY[threshold]}
    points = []
    lower_bound = now_epoch - TREND_LOOKBACK_SECONDS
    for item in readings:
        stamp = _parse_time(item.get("received_at"))
        if stamp is None or stamp < lower_bound or item.get("environment_sensor_ok") is not True:
            continue
        value = _effective_heat_index(item.get("temperature_c"), item.get("humidity_pct"))
        if value is not None and math.isfinite(value):
            points.append((stamp, value))
    slope, reason = _linear_slope(points)
    trend = base["sensor_trend"]
    if slope is None:
        trend["reason"] = reason
    else:
        trend.update({"available": True, "slope_c_per_minute": round(slope, 3), "reason": None,
                      "samples_used": len(points),
                      "lookback_seconds": round(points[-1][0] - points[0][0])})
        if threshold is None:
            trend["reason"] = "Current reading is already above the highest configured heat-index screening band."
        elif slope > 0:
            minutes = (threshold - current_hi) / slope
            if 0 < minutes <= MAX_TREND_EXTRAPOLATION_MINUTES:
                trend["crossing_in_minutes"] = round(minutes, 1)
                trend["reason"] = "Linear extrapolation from recent ESP32 readings; uncertainty increases with time."
            else:
                trend["reason"] = "Recent trend does not estimate a threshold crossing within the 60-minute sensor-trend horizon."
        else:
            trend["reason"] = "Recent measured trend is flat or decreasing; no sensor-trend crossing is estimated."

    forecast_points = []
    for item in weather.get("hourly", []):
        stamp = _parse_time(item.get("time"))
        if stamp is None or stamp < now_epoch:
            continue
        value = _effective_heat_index(item.get("temperature_c"), item.get("humidity_pct"))
        if value is not None:
            forecast_points.append((stamp, value))
    weather_result = base["weather_forecast"]
    if weather.get("available") and threshold is not None:
        horizon_end = now_epoch + hours * 3600
        previous = (now_epoch, current_hi)
        for stamp, value in forecast_points:
            if stamp > horizon_end:
                break
            if previous[1] < threshold <= value:
                fraction = ((threshold - previous[1]) / (value - previous[1])) if value != previous[1] else 0
                crossing = previous[0] + (stamp - previous[0]) * fraction
                weather_result["crossing_at"] = datetime.fromtimestamp(crossing, timezone.utc).isoformat().replace("+00:00", "Z")
                weather_result["crossing_in_minutes"] = round((crossing - now_epoch) / 60, 1)
                weather_result["reason"] = "Interpolated between hourly site-weather forecast points."
                break
            previous = (stamp, value)
    elif not weather.get("available"):
        weather_result["reason"] = weather.get("unavailable_reason", "Site forecast is unavailable.")
    elif threshold is None:
        weather_result["reason"] = "Current reading is already above the highest configured heat-index screening band."
    else:
        weather_result["reason"] = f"No crossing of the next screening threshold is shown in the next {hours} hours of site forecast."

    crossings = [("sensor_trend", trend.get("crossing_in_minutes")),
                 ("weather_forecast", weather_result.get("crossing_in_minutes"))]
    crossings = [(basis, eta) for basis, eta in crossings if eta is not None]
    if crossings:
        basis, eta = min(crossings, key=lambda item: item[1])
        base["earliest_crossing_basis"] = basis
        source_text = "Recent sensor trend estimates" if basis == "sensor_trend" else "The site weather forecast estimates"
        base["recommended_action"] = (
            f"{source_text} that conditions may reach the next heat-index screening band in about {eta:g} minutes. "
            "Arrange a precautionary shaded cooling break and supervisor check before conditions increase; "
            "follow the site's heat-safety plan."
        )
    return base
