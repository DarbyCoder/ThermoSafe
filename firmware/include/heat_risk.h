#pragma once
#include <cmath>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>

// Keep identical to backend/risk.py; the C++ probe checks the contract.
namespace thermosafe {
enum class HeatState { Unknown, Normal, Caution, Warning, Critical };
enum class HeatIndexCategory { Unavailable, Lower, Caution, ExtremeCaution, Danger, ExtremeDanger };

inline const char* stateName(HeatState state) {
    switch (state) {
        case HeatState::Normal: return "normal";
        case HeatState::Caution: return "caution";
        case HeatState::Warning: return "warning";
        case HeatState::Critical: return "critical";
        default: return "unknown";
    }
}

inline const char* heatIndexCategoryName(HeatIndexCategory category) {
    switch (category) {
        case HeatIndexCategory::Lower: return "lower";
        case HeatIndexCategory::Caution: return "caution";
        case HeatIndexCategory::ExtremeCaution: return "extreme_caution";
        case HeatIndexCategory::Danger: return "danger";
        case HeatIndexCategory::ExtremeDanger: return "extreme_danger";
        default: return "unavailable";
    }
}

inline double heatIndexF(double t, double r) {
    if (!std::isfinite(t) || !std::isfinite(r) || r < 0 || r > 100 || t < 80 || t > 112) return NAN;
    const double simple = .5 * (t + 61 + (t-68)*1.2 + r*.094);
    const double simpleAverage = (simple + t) / 2;
    // NOAA uses the simple estimate below an apparent temperature of 80 F.
    if (simpleAverage < 80) return simpleAverage;
    double hi = -42.379 + 2.04901523*t + 10.14333127*r - .22475541*t*r
        - .00683783*t*t - .05481717*r*r + .00122874*t*t*r
        + .00085282*t*r*r - .00000199*t*t*r*r;
    if (r < 13 && t >= 80 && t <= 112) hi -= ((13-r)/4)*std::sqrt((17-std::abs(t-95))/17);
    else if (r > 85 && t >= 80 && t <= 87) hi += ((r-85)/10)*((87-t)/5);
    return hi;
}

inline HeatIndexCategory heatIndexCategory(double heatIndexC, double temperatureC) {
    if (std::isfinite(temperatureC) && temperatureC < 27.0) return HeatIndexCategory::Lower;
    if (!std::isfinite(heatIndexC)) return HeatIndexCategory::Unavailable;
    if (heatIndexC < 27.0) return HeatIndexCategory::Lower;
    if (heatIndexC < 32.0) return HeatIndexCategory::Caution;
    if (heatIndexC < 41.0) return HeatIndexCategory::ExtremeCaution;
    if (heatIndexC <= 54.0) return HeatIndexCategory::Danger;
    return HeatIndexCategory::ExtremeDanger;
}

inline HeatState categoryState(HeatIndexCategory category) {
    switch (category) {
        case HeatIndexCategory::Lower: return HeatState::Normal;
        case HeatIndexCategory::Caution: return HeatState::Caution;
        case HeatIndexCategory::ExtremeCaution: return HeatState::Warning;
        case HeatIndexCategory::Danger:
        case HeatIndexCategory::ExtremeDanger: return HeatState::Critical;
        default: return HeatState::Unknown;
    }
}

inline double heatIndexC(double temperatureC, double humidity) {
    if (!std::isfinite(temperatureC) || !std::isfinite(humidity) || temperatureC < 27.0) return NAN;
    const double hi = heatIndexF(temperatureC*1.8+32, humidity);
    return std::isfinite(hi) ? (hi-32.0)/1.8 : NAN;
}

inline HeatState assess(double temperatureC, double humidity) {
    if (!std::isfinite(temperatureC) || !std::isfinite(humidity)) return HeatState::Unknown;
    return categoryState(heatIndexCategory(heatIndexC(temperatureC, humidity), temperatureC));
}

inline double clampScore(double score) {
    return score < 0 ? 0 : score > 100 ? 100 : score;
}

inline double heatIndexDemoScore(double heatIndexCValue, double temperatureC) {
    if (std::isfinite(heatIndexCValue)) return clampScore((heatIndexCValue - 27.0) * (100.0 / 27.0));
    if (std::isfinite(temperatureC) && temperatureC < 27.0) return 0;
    return NAN;
}

inline double temperatureDemoScore(double temperatureC) {
    if (!std::isfinite(temperatureC)) return NAN;
    return clampScore((temperatureC - 27.0) * (100.0 / 27.0));
}

inline double humidityDemoScore(double humidity) {
    return std::isfinite(humidity) ? clampScore(humidity) : NAN;
}

inline double exposureDurationScore(uint64_t exposureMs) {
    return exposureMs >= 1800000ULL ? 100.0 : static_cast<double>(exposureMs) * (100.0 / 1800000.0);
}

inline double brightLightExposureScore(uint64_t brightExposureMs) {
    return exposureDurationScore(brightExposureMs);
}

inline double heatDurationRiskScore(uint64_t heatExposureMs) {
    return exposureDurationScore(heatExposureMs);
}

inline double activityDemoAdjustment(const char* activity) {
    if (!activity) return 0;
    if (strcmp(activity, "moderate") == 0) return 2.0;
    if (strcmp(activity, "heavy") == 0) return 4.0;
    return 0;
}

inline double acclimatizationDemoAdjustment(const char* status) {
    return status && strcmp(status, "not_acclimatized") == 0 ? 4.0 : 0.0;
}

inline double combinedRiskScore(double hiScore, double temperatureScore, double humidityScore,
                                double brightExposureScore, double heatDurationScore,
                                double siteUvIndex, double optionalAdjustment) {
    // Demonstration weights: HI 35%, temperature 15%, humidity 10%, bright time 20%,
    // elevated-heat time 10%, and site UV 10%. UV >= 11 saturates its component.
    const double uvScore = std::isfinite(siteUvIndex) && siteUvIndex >= 0
        ? clampScore(siteUvIndex * (100.0 / 11.0)) : NAN;
    const double values[] = {hiScore, temperatureScore, humidityScore,
                             brightExposureScore, heatDurationScore, uvScore};
    const double weights[] = {.35, .15, .10, .20, .10, .10};
    double weighted = 0, availableWeight = 0;
    for (size_t i = 0; i < 6; ++i) {
        if (std::isfinite(values[i])) {
            weighted += values[i] * weights[i];
            availableWeight += weights[i];
        }
    }
    if (availableWeight == 0) return 0;
    return clampScore(weighted / availableWeight + optionalAdjustment);
}

inline HeatState scoreState(double score) {
    if (score >= 75) return HeatState::Critical;
    if (score >= 55) return HeatState::Warning;
    if (score >= 30) return HeatState::Caution;
    return HeatState::Normal;
}

inline HeatState combine(HeatState ambient, double totalScore) {
    auto rank = [](HeatState state) {
        switch (state) {
            case HeatState::Normal: return 0;
            case HeatState::Caution: return 1;
            case HeatState::Warning: return 2;
            case HeatState::Critical: return 3;
            default: return -1;
        }
    };
    const HeatState scored = scoreState(totalScore);
    if (ambient == HeatState::Unknown && scored == HeatState::Normal) return HeatState::Unknown;
    return rank(scored) > rank(ambient) ? scored : ambient;
}
} // namespace thermosafe
