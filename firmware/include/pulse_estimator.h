#pragma once
#include <array>
#include <algorithm>
#include <cmath>
#include <cstdint>

// Estimate pulse rate from a repeating optical waveform. Prototype only.
class PulseEstimator {
public:
    bool contactDetected() const { return contactPresent; }

    void sample(int raw, uint32_t now, bool invert, float minimumAmplitude,
                int contactMinimumRaw = 100) {
        (void)invert; // Correlation is polarity-independent.

        // Learn the sensor's uncovered reading at startup. On this board the
        // no-finger level can be high or low depending on light and orientation.
        if (!bootClockStarted) {
            bootClockStarted = true;
            bootStartedAt = now;
        }
        if (!contactBaselineReady) {
            if (raw >= 0 && raw <= 4095) {
                contactBaselineSum += raw;
                ++contactBaselineSamples;
            }
            resetSignal(raw);
            if (now - bootStartedAt >= 2000 && contactBaselineSamples >= 100) {
                noContactBaseline = float(contactBaselineSum) / contactBaselineSamples;
                contactBaselineReady = true;
            }
            return;
        }

        float contactDelta = std::fabs(raw - noContactBaseline);
        if (!contactPresent) {
            if (contactDelta >= CONTACT_DELTA_ON) {
                if (!contactCandidate) {
                    contactCandidate = true;
                    contactCandidateAt = now;
                } else if (now - contactCandidateAt >= CONTACT_CONFIRM_MS) {
                    contactPresent = true;
                    contactCandidate = false;
                    resetSignal(raw);
                }
            } else {
                contactCandidate = false;
            }
        } else if (contactDelta <= CONTACT_DELTA_OFF) {
            if (!noContactCandidate) {
                noContactCandidate = true;
                noContactCandidateAt = now;
            } else if (now - noContactCandidateAt >= NO_CONTACT_CONFIRM_MS) {
                contactPresent = false;
                noContactCandidate = false;
                resetSignal(raw);
            }
        } else {
            noContactCandidate = false;
        }

        if (!contactPresent || raw <= contactMinimumRaw || raw >= 4087) {
            resetSignal(raw);
            return;
        }

        int step = raw - lastRaw;
        if (initialized && (step > 120 || step < -120)) {
            resetSignal(raw);
            return;
        }
        lastRaw = raw;

        if (!initialized) {
            baseline = raw;
            initialized = true;
        }
        baseline += 0.01f * (raw - baseline);
        float centered = raw - baseline;
        filtered += 0.30f * (centered - filtered);

        samples[writeIndex] = filtered;
        timestamps[writeIndex] = now;
        writeIndex = (writeIndex + 1) % samples.size();
        if (sampleCount < samples.size()) ++sampleCount;
        lastSampleMs = now;
        signalValid = true;
        amplitudeFloor = minimumAmplitude;
    }

    float value(uint32_t now, float minimumAmplitude = 1.0f) const {
        if (!signalValid || sampleCount < 350 || now - lastSampleMs > 400) return NAN;

        const size_t count = sampleCount;
        const size_t oldest = (writeIndex + samples.size() - count) % samples.size();
        const uint32_t firstMs = timestamps[oldest];
        const uint32_t lastMs = timestamps[(writeIndex + samples.size() - 1) % samples.size()];
        const uint32_t spanMs = lastMs - firstMs;
        if (spanMs < 3000) return NAN;
        const float samplePeriodMs = float(spanMs) / float(count - 1);
        if (samplePeriodMs < 5.0f || samplePeriodMs > 30.0f) return NAN;

        float mean = 0;
        for (size_t i = 0; i < count; ++i) mean += samples[(oldest + i) % samples.size()];
        mean /= float(count);

        float totalEnergy = 0;
        for (size_t i = 0; i < count; ++i) {
            float value = samples[(oldest + i) % samples.size()] - mean;
            totalEnergy += value * value;
        }
        float rms = std::sqrt(totalEnergy / float(count));
        float requiredAmplitude = std::max(minimumAmplitude, amplitudeFloor);
        if (rms < requiredAmplitude) return NAN;

        int firstLag = std::max(2, int(std::ceil(333.0f / samplePeriodMs)));
        int lastLag = std::min(int(count / 3), int(1500.0f / samplePeriodMs));
        if (firstLag >= lastLag) return NAN;

        float bestCorrelation = -1;
        int bestLag = 0;
        for (int lag = firstLag; lag <= lastLag; ++lag) {
            float cross = 0, energyA = 0, energyB = 0;
            for (size_t i = 0; i + size_t(lag) < count; ++i) {
                float a = samples[(oldest + i) % samples.size()] - mean;
                float b = samples[(oldest + i + size_t(lag)) % samples.size()] - mean;
                cross += a * b;
                energyA += a * a;
                energyB += b * b;
            }
            float denominator = std::sqrt(energyA * energyB);
            float correlation = denominator > 0 ? cross / denominator : 0;
            if (correlation > bestCorrelation) {
                bestCorrelation = correlation;
                bestLag = lag;
            }
        }

        // Require a clear repeating pattern so random sensor noise stays blank.
        if (bestCorrelation < 0.55f || bestLag == 0) return NAN;
        float rate = 60000.0f / (float(bestLag) * samplePeriodMs);
        return rate >= 40.0f && rate <= 180.0f ? rate : NAN;
    }

private:
    void resetSignal(int raw) {
        initialized = false;
        signalValid = false;
        baseline = raw;
        lastRaw = raw;
        filtered = 0;
        sampleCount = 0;
        writeIndex = 0;
    }

    static constexpr size_t WindowSize = 450;
    static constexpr int CONTACT_DELTA_ON = 35;
    static constexpr int CONTACT_DELTA_OFF = 20;
    static constexpr uint32_t CONTACT_CONFIRM_MS = 250;
    static constexpr uint32_t NO_CONTACT_CONFIRM_MS = 500;
    bool bootClockStarted = false;
    bool contactBaselineReady = false;
    bool contactCandidate = false;
    bool noContactCandidate = false;
    bool contactPresent = false;
    uint32_t bootStartedAt = 0;
    uint32_t contactCandidateAt = 0;
    uint32_t noContactCandidateAt = 0;
    uint32_t contactBaselineSamples = 0;
    uint64_t contactBaselineSum = 0;
    float noContactBaseline = 0;
    bool initialized = false;
    bool signalValid = false;
    float baseline = 0;
    float filtered = 0;
    float amplitudeFloor = 1.0f;
    int lastRaw = 0;
    uint32_t lastSampleMs = 0;
    std::array<float, WindowSize> samples{};
    std::array<uint32_t, WindowSize> timestamps{};
    size_t sampleCount = 0;
    size_t writeIndex = 0;
};
