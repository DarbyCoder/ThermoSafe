"""NOAA heat-index bands plus an explicitly non-medical demo exposure score."""

import math

MODEL_VERSION = "heat-exposure-demo-v4-uv"
HEAT_INDEX_BANDS_C = {
    "lower": {"below_c": 27.0},
    "caution": {"from_c_inclusive": 27.0, "to_c_exclusive": 32.0},
    "extreme_caution": {"from_c_inclusive": 32.0, "to_c_exclusive": 41.0},
    "danger": {"from_c_inclusive": 41.0, "through_c_inclusive": 54.0},
    "extreme_danger": {"above_c": 54.0},
}
RISK_SCORE_THRESHOLDS = {"caution": 30.0, "warning": 55.0, "critical": 75.0}
EXPOSURE_SATURATION_SECONDS = 1800.0
DEMO_SCORE_WEIGHTS = {
    "heat_index": 0.35,
    "air_temperature": 0.15,
    "relative_humidity": 0.10,
    "estimated_bright_light_duration": 0.20,
    "elevated_heat_duration": 0.10,
    "uv_index": 0.10,
}


def heat_index_f(temperature_f: float, humidity_pct: float) -> float | None:
    """NWS simple estimate/Rothfusz equation; regression range is 80–112 F."""
    t, r = temperature_f, humidity_pct
    if not math.isfinite(t) or not math.isfinite(r) or not 80 <= t <= 112 or not 0 <= r <= 100:
        return None
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + r * 0.094)
    simple_average = (simple + t) / 2.0
    if simple_average < 80:
        return simple_average
    result = (-42.379 + 2.04901523*t + 10.14333127*r - 0.22475541*t*r
              - 0.00683783*t*t - 0.05481717*r*r + 0.00122874*t*t*r
              + 0.00085282*t*r*r - 0.00000199*t*t*r*r)
    if r < 13 and 80 <= t <= 112:
        result -= ((13-r)/4) * math.sqrt((17-abs(t-95))/17)
    elif r > 85 and 80 <= t <= 87:
        result += ((r-85)/10) * ((87-t)/5)
    return result


def heat_index_category(heat_index_c: float | None, temperature_c: float | None) -> str:
    if temperature_c is not None and temperature_c < 27.0:
        return "lower"
    if heat_index_c is None:
        return "unavailable"
    if heat_index_c < 27.0:
        return "lower"
    if heat_index_c < 32.0:
        return "caution"
    if heat_index_c < 41.0:
        return "extreme_caution"
    if heat_index_c <= 54.0:
        return "danger"
    return "extreme_danger"


def _category_state(category: str) -> str:
    return {
        "lower": "normal",
        "caution": "caution",
        "extreme_caution": "warning",
        "danger": "critical",
        "extreme_danger": "critical",
        "unavailable": "unknown",
    }[category]


def _severity(state: str) -> int:
    return {"unknown": -1, "normal": 0, "caution": 1, "warning": 2, "critical": 3}[state]


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _duration_score(seconds: int) -> float:
    return _clamp(seconds / EXPOSURE_SATURATION_SECONDS * 100.0)


def _uv_score(uv_index: float | None) -> float | None:
    if uv_index is None or not math.isfinite(uv_index) or uv_index < 0:
        return None
    return _clamp(uv_index / 11.0 * 100.0)


def _activity_adjustment(activity_level: str) -> float:
    return {"unknown": 0.0, "light": 0.0, "moderate": 2.0, "heavy": 4.0}[activity_level]


def _acclimatization_adjustment(status: str) -> float:
    return 4.0 if status == "not_acclimatized" else 0.0


def _score_state(score: float) -> str:
    if score >= RISK_SCORE_THRESHOLDS["critical"]:
        return "critical"
    if score >= RISK_SCORE_THRESHOLDS["warning"]:
        return "warning"
    if score >= RISK_SCORE_THRESHOLDS["caution"]:
        return "caution"
    return "normal"


def _recommended_action(state: str) -> str:
    return {
        "unknown": ("Do not treat this reading as safe. Check the sensor and connection; "
                    "use the site's heat-safety plan and a supervisor's observation until "
                    "valid environmental readings return."),
        "normal": ("Continue the site's planned water, rest, and monitoring practices. "
                   "Recheck if conditions or the worker's condition changes."),
        "caution": ("Increase monitoring, make water and a shaded recovery break available, "
                    "and recheck conditions soon under the site's heat-safety plan."),
        "warning": ("Pause strenuous work and move the worker to a shaded or cooler area. "
                    "Notify the supervisor, follow the site heat-safety plan, and reassess "
                    "before work resumes."),
        "critical": ("Stop work and move the worker to a cooler area. Notify the supervisor "
                     "and follow the site's emergency protocol; if the worker has symptoms "
                     "of heat illness, seek emergency medical help."),
    }[state]


def _duration_text(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def assess(temperature_c: float | None, humidity_pct: float | None,
           bright_exposure_seconds: int = 0,
           heat_exposure_seconds: int = 0,
           activity_level: str = "unknown",
           acclimatization_status: str = "unknown",
           site_uv_index: float | None = None) -> dict:
    if site_uv_index is not None and (not math.isfinite(site_uv_index) or site_uv_index < 0):
        site_uv_index = None
    if temperature_c is None or humidity_pct is None:
        hi_c = None
        category = "unavailable"
        hi_score = None
        temperature_score = None
        humidity_score = None
        ambient_state = "unknown"
    else:
        hi_f = heat_index_f(temperature_c * 1.8 + 32.0, humidity_pct)
        hi_c = round((hi_f - 32.0) / 1.8, 2) if hi_f is not None and temperature_c >= 27 else None
        category = heat_index_category(hi_c, temperature_c)
        ambient_state = _category_state(category)
        hi_score = _clamp((hi_c - 27.0) * (100.0 / 27.0)) if hi_c is not None else 0.0
        temperature_score = _clamp((temperature_c - 27.0) * (100.0 / 27.0))
        humidity_score = _clamp(humidity_pct)

    bright_score = _duration_score(bright_exposure_seconds)
    heat_duration_score = _duration_score(heat_exposure_seconds)
    uv_index_score = _uv_score(site_uv_index)
    activity_adjustment = _activity_adjustment(activity_level)
    acclimatization_adjustment = _acclimatization_adjustment(acclimatization_status)
    available_components = {
        "heat_index": hi_score,
        "air_temperature": temperature_score,
        "relative_humidity": humidity_score,
        "estimated_bright_light_duration": bright_score,
        "elevated_heat_duration": heat_duration_score,
        "uv_index": uv_index_score,
    }
    weighted = sum(DEMO_SCORE_WEIGHTS[key] * value
                   for key, value in available_components.items() if value is not None)
    available_weight = sum(DEMO_SCORE_WEIGHTS[key] for key, value in available_components.items()
                           if value is not None)
    base_score = weighted / available_weight if available_weight else 0.0
    context_adjustment = activity_adjustment + acclimatization_adjustment
    combined_score = _clamp(base_score + context_adjustment)
    combined_state = _score_state(combined_score)
    state = combined_state if _severity(combined_state) > _severity(ambient_state) else ambient_state
    if ambient_state == "unknown" and combined_state == "normal":
        state = "unknown"

    score_exceeds_ambient = _severity(combined_state) > _severity(ambient_state)
    reason = ("combined_demo_estimate" if score_exceeds_ambient
              else "heat_index_category" if ambient_state != "unknown"
              else "environment_sensor_error")
    triggered_by = []
    if ambient_state == "unknown":
        triggered_by.append("environment_sensor_unavailable")
    elif ambient_state == state and state != "normal":
        triggered_by.append("heat_index_category")
    if combined_state == state and state != "normal":
        triggered_by.append("combined_demo_score")
        if uv_index_score is not None and uv_index_score > 0:
            triggered_by.append("site_uv_index")

    components = {key: round(value, 1) if value is not None else None
                  for key, value in available_components.items()}
    components["activity_adjustment"] = activity_adjustment
    components["acclimatization_adjustment"] = acclimatization_adjustment
    components["total_context_adjustment"] = context_adjustment

    score_contributions = {
        key: round(DEMO_SCORE_WEIGHTS[key] * value / available_weight, 2)
        if value is not None and available_weight else None
        for key, value in available_components.items()
    }
    factor_details = []
    if temperature_c is None or humidity_pct is None:
        factor_details.append(
            "Temperature and/or humidity is unavailable, so the heat-index screening "
            "category cannot be assessed. The system marks risk unknown instead of safe."
        )
    elif hi_c is None:
        factor_details.append(
            f"Heat index is not calculated below 27°C; ambient temperature is "
            f"{temperature_c:.1f}°C and the screening band is lower."
        )
    else:
        factor_details.append(
            f"Heat index is {hi_c:.1f}°C ({category.replace('_', ' ')} screening band), "
            f"which maps to {ambient_state} for this demonstration."
        )

    labels = {
        "heat_index": ("Heat-index score", "35%"),
        "air_temperature": ("Air-temperature score", "15%"),
        "relative_humidity": ("Relative-humidity score", "10%"),
        "estimated_bright_light_duration": ("Estimated bright-light exposure score", "20%"),
        "elevated_heat_duration": ("Elevated-heat duration score", "10%"),
        "uv_index": ("Site UV-index score", "10%"),
    }
    for key, (label, weight) in labels.items():
        value = available_components[key]
        if value is None or (key == "heat_index" and hi_c is None):
            continue
        if key == "uv_index":
            factor_details.append(
                f"{label}: {value:.1f}/100 from site-forecast UV index "
                f"{site_uv_index:.1f}, {weight} weight, "
                f"{score_contributions[key]:.1f} points in the normalized demo score."
            )
            continue
        factor_details.append(
            f"{label}: {value:.1f}/100, {weight} weight, "
            f"{score_contributions[key]:.1f} points in the normalized demo score."
        )
    if site_uv_index is None:
        factor_details.append("Site UV index unavailable; the 10% UV component is omitted and the remaining score weights are normalized.")
    factor_details.extend([
        f"Estimated bright-light exposure: {_duration_text(bright_exposure_seconds)} total.",
        f"Elevated-heat exposure: {_duration_text(heat_exposure_seconds)} total.",
        f"Activity: {activity_level} ({activity_adjustment:+.1f} demo points); "
        f"acclimatization: {acclimatization_status} ({acclimatization_adjustment:+.1f} demo points).",
        f"Combined demonstration score: {combined_score:.1f}/100 "
        f"(caution {RISK_SCORE_THRESHOLDS['caution']:.0f}, warning "
        f"{RISK_SCORE_THRESHOLDS['warning']:.0f}, critical "
        f"{RISK_SCORE_THRESHOLDS['critical']:.0f}).",
    ])

    if state == "unknown":
        explanation = ("Risk is UNKNOWN because required environmental readings are "
                       "unavailable; the system is not showing a green/safe assessment.")
    elif state == "normal":
        explanation = (f"No heat-index caution band is active and the demonstration "
                       f"score is {combined_score:.1f}/100, below the caution display "
                       f"threshold of {RISK_SCORE_THRESHOLDS['caution']:.0f}.")
    else:
        triggers = []
        if "heat_index_category" in triggered_by:
            triggers.append(f"the heat-index {category.replace('_', ' ')} band")
        if "combined_demo_score" in triggered_by:
            triggers.append(f"the combined demonstration score of {combined_score:.1f}/100")
        if "site_uv_index" in triggered_by:
            triggers.append(f"the site UV index of {site_uv_index:.1f}")
        explanation = f"Risk is {state.upper()} because " + " and ".join(triggers) + "."

    return {
        "state": state,
        "heat_index_c": hi_c,
        "heat_index_category": category,
        "indicator_f": round(hi_c * 1.8 + 32.0, 2) if hi_c is not None else None,
        "bright_light_exposure_score": round(bright_score, 2),
        "heat_duration_risk_score": round(heat_duration_score, 2),
        "exposure_risk_score": round(max(bright_score, heat_duration_score, uv_index_score or 0), 2),
        "site_uv_index": round(site_uv_index, 2) if site_uv_index is not None else None,
        "uv_index_score": round(uv_index_score, 2) if uv_index_score is not None else None,
        "combined_risk_score": round(combined_score, 2),
        "demo_score_components": components,
        "score_contributions": score_contributions,
        "triggered_by": triggered_by,
        "factor_details": factor_details,
        "explanation": explanation,
        "recommended_action": _recommended_action(state),
        "activity_level": activity_level,
        "acclimatization_status": acclimatization_status,
        "model_version": MODEL_VERSION,
        "reason": reason,
    }
