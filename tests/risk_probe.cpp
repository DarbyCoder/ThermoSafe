#include <iomanip>
#include <iostream>
#include "heat_risk.h"
#include "pulse_estimator.h"

int main() {
    // Flat and saturated input stays unavailable; a repeating prototype waveform is estimated.
    PulseEstimator flat, saturated, noise, pulse;
    uint32_t randomState = 17;
    for (uint32_t t = 0; t < 20000; t += 10) {
        flat.sample(2000, t, false, 40);
        saturated.sample(4095, t, false, 40);
        randomState = randomState * 1664525u + 1013904223u;
        noise.sample(2000 + int((randomState >> 24) % 13) - 6, t, false, 8);
    }
    if (std::isfinite(flat.value(20000)) || std::isfinite(saturated.value(20000))
        || std::isfinite(noise.value(20000))) return 1;
    for (uint32_t t = 0; t < 3000; t += 10) {
        pulse.sample(2000, t, false, 8);
    }
    for (uint32_t t = 3000; t < 15000; t += 10) {
        float phase = float(t % 800) / 800.0f;
        float distance = phase-.12f;
        if (distance > .5f) distance -= 1.0f;
        else if (distance < -.5f) distance += 1.0f;
        int raw = 2100 + int(100*std::exp(-distance*distance/.002f));
        pulse.sample(raw, t, false, 8);
    }
    if (!std::isfinite(pulse.value(15000)) || std::fabs(pulse.value(15000)-75.0f) > 8.0f) return 2;
    double temperature, humidity;
    std::cout << std::setprecision(17);
    while (std::cin >> temperature >> humidity) {
        double hi = thermosafe::heatIndexF(temperature*1.8+32, humidity);
        std::cout << thermosafe::stateName(thermosafe::assess(temperature, humidity)) << ' ';
        if (std::isfinite(hi)) std::cout << hi;
        else std::cout << "null";
        std::cout << '\n';
    }
}
