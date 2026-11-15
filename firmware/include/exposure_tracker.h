#pragma once

#include <cstdint>

struct ExposureTracker {
    uint64_t brightTotalMs = 0;
    uint64_t heatTotalMs = 0;
    uint64_t brightContinuousMs = 0;
    uint64_t heatContinuousMs = 0;
    uint32_t lastUpdateMs = 0;
    bool initialized = false;

    void begin(uint32_t now, uint64_t savedBrightMs, uint64_t savedHeatMs) {
        brightTotalMs = savedBrightMs;
        heatTotalMs = savedHeatMs;
        brightContinuousMs = 0;
        heatContinuousMs = 0;
        lastUpdateMs = now;
        initialized = true;
    }

    void update(uint32_t now, bool bright, bool elevatedHeat) {
        if (!initialized) begin(now, brightTotalMs, heatTotalMs);
        const uint32_t elapsed = now - lastUpdateMs;
        lastUpdateMs = now;
        if (bright) {
            brightTotalMs += elapsed;
            brightContinuousMs += elapsed;
        } else {
            brightContinuousMs = 0;
        }
        if (elevatedHeat) {
            heatTotalMs += elapsed;
            heatContinuousMs += elapsed;
        } else {
            heatContinuousMs = 0;
        }
    }

    void reset(uint32_t now) {
        brightTotalMs = 0;
        heatTotalMs = 0;
        brightContinuousMs = 0;
        heatContinuousMs = 0;
        lastUpdateMs = now;
        initialized = true;
    }
};
