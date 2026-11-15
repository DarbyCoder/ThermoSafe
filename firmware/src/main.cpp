#include <Arduino.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>
#include <painlessMesh.h>
#include <esp_timer.h>
#include <esp_system.h>
#include <esp_arduino_version.h>
#include <atomic>
#include <algorithm>
#include <cmath>
#include <cctype>

#if __has_include("config.h")
#include "config.h"
#else
#include "config.example.h"
#endif
#include "heat_risk.h"
#include "exposure_tracker.h"
#include "pulse_estimator.h"

#ifndef WORKER_ACTIVITY_LEVEL
#define WORKER_ACTIVITY_LEVEL "unknown"
#endif
#ifndef WORKER_ACCLIMATIZATION_STATUS
#define WORKER_ACCLIMATIZATION_STATUS "unknown"
#endif

static_assert(SAMPLE_INTERVAL_MS >= 2000, "DHT sample interval must be at least 2 seconds");
static_assert(TELEMETRY_INTERVAL_MS >= 1000, "Telemetry interval must be at least one second");
static_assert(!ENABLE_EXPERIMENTAL_BPM || ENABLE_PULSE_SENSOR, "Enable raw pulse sensing before BPM");
static_assert(LIGHT_BRIGHT_ORIENTED_RAW > LIGHT_DARK_ORIENTED_RAW,
              "Bright-light ADC calibration point must be greater than dark point");

constexpr char FIRMWARE_VERSION[] = "0.3.0";
constexpr bool HAS_LOCAL_WORKER_SENSORS = !ENABLE_MESH || !MESH_IS_GATEWAY;
constexpr bool LOCAL_PULSE_SENSOR = HAS_LOCAL_WORKER_SENSORS && ENABLE_PULSE_SENSOR;
constexpr bool LOCAL_LIGHT_SENSOR = HAS_LOCAL_WORKER_SENSORS && ENABLE_LIGHT_SENSOR;
constexpr uint8_t MAX_MESH_NODE_IDS = 32;
constexpr uint8_t MAX_MESH_PACKETS_QUEUED = 32;
DHT environmentSensor(DHT_PIN, DHT11);
PulseEstimator pulse;
Preferences exposurePreferences;
ExposureTracker exposure;
String deviceId, bootId;
QueueHandle_t telemetryQueue;
QueueHandle_t meshIncomingQueue;
Scheduler meshScheduler;
painlessMesh mesh;
std::atomic<uint32_t> lastBackendSuccessMs{0};
std::atomic<int> remoteAlertTestState{-1};
std::atomic<uint32_t> remoteAlertTestExpiresAt{0};
std::atomic<int> remoteAssessmentState{-1};
std::atomic<int> remoteSiteUvTenths{-1};
std::atomic<int> remoteAssessmentScoreTenths{-1};
std::atomic<uint32_t> remoteAssessmentExpiresAt{0};
std::atomic<uint32_t> remoteSiteUvExpiresAt{0};
// 0=automatic, 1=green, 2=yellow, 3=red; start in the green demo case.
std::atomic<int> meshDemoMode{1};
std::atomic<bool> meshAutoCommandPending{false};
std::atomic<uint32_t> meshDemoStartedAt{0};
std::atomic<uint32_t> meshDemoSequence{0};
std::atomic<float> meshDemoTemperature{27.2f};
std::atomic<float> meshDemoHumidity{35.0f};
std::atomic<float> meshDemoBpm{70.0f};

struct Sample {
    uint64_t uptimeMs;
    uint32_t sequence;
    float temperature, humidity, bpm, lightPercent;
    double latitude, longitude;
    int pulseRaw, lightRaw;
    uint64_t brightExposureMs, heatExposureMs;
    uint32_t brightContinuousMs, heatContinuousMs;
    float demoUvIndex;
    bool bpmSimulated, locationSimulated, environmentOk, lightSensorOk, sunlightActive;
    uint8_t activityLevel, acclimatizationStatus;
    bool meshConnected;
    uint32_t meshNodeId;
    uint32_t meshNodeIds[MAX_MESH_NODE_IDS];
    uint8_t meshNodeCount;
    int wifiRssiDbm;
    bool demoScenario;
};

void applyDemoScenario(Sample& sample) {
    const int mode = meshDemoMode.load();
    if (mode < 1 || mode > 3) return;

    const uint32_t elapsed = millis() - meshDemoStartedAt.load();
    float brightness = 18.0f;
    uint64_t brightBaseMs = 0;
    uint64_t heatBaseMs = 0;
    int uv = 2;

    if (mode == 1) {
        sample.temperature = meshDemoTemperature.load();
        sample.humidity = meshDemoHumidity.load();
        sample.bpm = meshDemoBpm.load();
        sample.activityLevel = 1;
        sample.acclimatizationStatus = 1;
    } else if (mode == 2) {
        sample.temperature = meshDemoTemperature.load();
        sample.humidity = meshDemoHumidity.load();
        sample.bpm = meshDemoBpm.load();
        sample.activityLevel = 2;
        sample.acclimatizationStatus = 1;
        brightness = 65.0f;
        brightBaseMs = 15ULL * 60ULL * 1000ULL;
        heatBaseMs = 10ULL * 60ULL * 1000ULL;
        uv = 5;
    } else {
        sample.temperature = meshDemoTemperature.load();
        sample.humidity = meshDemoHumidity.load();
        sample.bpm = meshDemoBpm.load();
        sample.activityLevel = 3;
        sample.acclimatizationStatus = 2;
        brightness = 95.0f;
        brightBaseMs = 45ULL * 60ULL * 1000ULL;
        heatBaseMs = 30ULL * 60ULL * 1000ULL;
        uv = 10;
    }

    const uint64_t elapsedMs = mode == 1 ? 0 : elapsed;
    sample.environmentOk = true;
    sample.lightSensorOk = true;
    sample.lightPercent = brightness;
    const float oriented = LIGHT_DARK_ORIENTED_RAW
        + brightness * (LIGHT_BRIGHT_ORIENTED_RAW - LIGHT_DARK_ORIENTED_RAW) / 100.0f;
    sample.lightRaw = LIGHT_BRIGHT_IS_HIGHER ? int(lroundf(oriented))
                                               : 4095 - int(lroundf(oriented));
    sample.sunlightActive = brightness >= SUNLIGHT_THRESHOLD_PERCENT;
    sample.brightExposureMs = brightBaseMs + elapsedMs;
    sample.heatExposureMs = heatBaseMs + elapsedMs;
    sample.brightContinuousMs = uint32_t(sample.brightExposureMs);
    sample.heatContinuousMs = uint32_t(sample.heatExposureMs);
    // Give the presentation UI a clearly simulated waveform value as well as BPM.
    const float phase = float(millis()) * (70.0f / 60.0f) * (2.0f * PI / 1000.0f);
    sample.pulseRaw = 2048 + int(lroundf(80.0f * sinf(phase)));
    sample.bpmSimulated = true;
    sample.demoScenario = true;
    sample.demoUvIndex = float(uv);
}

struct MeshPacket { char json[1500]; };

void meshReceiveCallback(uint32_t from, String& message) {
    if (!ENABLE_MESH) return;
    if (MESH_IS_GATEWAY) {
        if (!meshIncomingQueue) return;
        MeshPacket packet{};
        message.toCharArray(packet.json, sizeof(packet.json));
        if (xQueueSend(meshIncomingQueue, &packet, 0) == pdTRUE) {
            Serial.printf("[MESH RX] sender=%u queued for backend\n", from);
            Serial.printf("[MESH DATA] sender=%u payload=%s\n", from, message.c_str());
        } else {
            Serial.printf("[MESH RX] queue full; dropped sender=%u\n", from);
            Serial.printf("[MESH DATA] sender=%u payload=%s\n", from, message.c_str());
        }
        return;
    }

    JsonDocument command;
    if (deserializeJson(command, message) || strcmp(command["type"] | "", "demo_control") != 0) return;
    const char* target = command["target"] | "";
    char nodeIdHex[12];
    snprintf(nodeIdHex, sizeof(nodeIdHex), "%X", unsigned(mesh.getNodeId()));
    const bool targetMatches = strcmp(target, "all") == 0 || deviceId == target
        || String(unsigned(mesh.getNodeId())) == target || String(nodeIdHex) == target;
    if (!targetMatches) return;

    // Missing or malformed control mode should stay in the Green demo; Auto is explicit.
    const char* mode = command["mode"] | "green";
    int nextMode = 1;
    if (strcmp(mode, "green") == 0) nextMode = 1;
    else if (strcmp(mode, "yellow") == 0) nextMode = 2;
    else if (strcmp(mode, "red") == 0) nextMode = 3;
    else if (strcmp(mode, "auto") == 0 || strcmp(mode, "a") == 0) nextMode = 0;
    if (nextMode == 1) {
        meshDemoTemperature.store(27.2f);
        meshDemoHumidity.store(35.0f);
        meshDemoBpm.store(70.0f);
    } else if (nextMode == 2 || nextMode == 3) {
        const bool hot = (esp_random() & 1U) != 0;
        const float fraction = float(esp_random() % 1001U) / 1000.0f;
        const float humidityFraction = float(esp_random() % 1001U) / 1000.0f;
        const float bpmFraction = float(esp_random() % 1001U) / 1000.0f;
        if (nextMode == 2) {
            meshDemoTemperature.store(hot ? 32.0f + fraction * 4.0f : 2.0f + fraction * 8.0f);
            meshDemoHumidity.store(40.0f + humidityFraction * 45.0f);
            meshDemoBpm.store(88.0f + bpmFraction * 20.0f);
        } else {
            meshDemoTemperature.store(hot ? 39.0f + fraction * 5.0f : -12.0f + fraction * 12.0f);
            meshDemoHumidity.store(40.0f + humidityFraction * 45.0f);
            meshDemoBpm.store(120.0f + bpmFraction * 25.0f);
        }
    }
    meshDemoMode.store(nextMode);
    if (nextMode == 0) meshAutoCommandPending.store(true);
    meshDemoStartedAt.store(millis());
    Serial.printf("[MESH CONTROL] from=%u target=%s mode=%s\n", from, target, mode);
}

void sendMeshDemoCommand(const char* mode, const char* target) {
    JsonDocument command;
    command["type"] = "demo_control";
    command["target"] = target;
    command["mode"] = mode;
    command["sequence"] = ++meshDemoSequence;
    String message;
    serializeJson(command, message);
    bool sent = mesh.sendBroadcast(message);
    Serial.printf("[MESH CONTROL TX] mode=%s target=%s sent=%s\n",
                  mode, target, sent ? "yes" : "no");
}

void handleMasterSerialLine(char* line) {
    char* save = nullptr;
    char* mode = strtok_r(line, " \t", &save);
    if (!mode) return;
    for (char* cursor = mode; *cursor; ++cursor) *cursor = char(tolower(static_cast<unsigned char>(*cursor)));
    const char* target = strtok_r(nullptr, " \t", &save);
    if (!target || !target[0]) target = "all";
    const char* normalizedMode = nullptr;
    if (strcmp(mode, "g") == 0 || strcmp(mode, "green") == 0) normalizedMode = "green";
    else if (strcmp(mode, "y") == 0 || strcmp(mode, "yellow") == 0) normalizedMode = "yellow";
    else if (strcmp(mode, "r") == 0 || strcmp(mode, "red") == 0) normalizedMode = "red";
    else if (strcmp(mode, "a") == 0 || strcmp(mode, "auto") == 0) normalizedMode = "auto";
    else {
        Serial.println("Commands: g|y|r|a [worker-id|all] (default target: all)");
        return;
    }
    sendMeshDemoCommand(normalizedMode, target);
}

struct DemoLocation {
    double latitude;
    double longitude;
    bool simulated;
};

DemoLocation demoLocation(uint32_t now) {
    if (!ENABLE_SIMULATED_LOCATION) return {NAN, NAN, false};
    static bool initialized = false;
    static uint32_t lastStep = 0;
    static double eastMeters = 0;
    static double northMeters = 0;
    if (!initialized) {
        uint64_t mac = ESP.getEfuseMac();
        uint32_t seed = uint32_t(mac) ^ uint32_t(mac >> 32);
        float angle = float(seed % 6283) / 1000.0f;
        float radius = 5.0f + float((seed >> 8) % 1000) / 100.0f;
        eastMeters = cosf(angle) * radius;
        northMeters = sinf(angle) * radius;
        initialized = true;
        lastStep = now;
    } else if (now - lastStep >= 12000) {
        float angle = float(esp_random() % 6283) / 1000.0f;
        float distance = 0.5f + float(esp_random() % 100) / 100.0f;
        eastMeters += cosf(angle) * distance;
        northMeters += sinf(angle) * distance;
        if (hypot(eastMeters, northMeters) > 35.0) {
            eastMeters *= 0.6;
            northMeters *= 0.6;
        }
        lastStep = now;
    }
    const double metersPerDegreeLon = 111320.0 * cos(DEMO_LOCATION_CENTER_LATITUDE * PI / 180.0);
    return {DEMO_LOCATION_CENTER_LATITUDE + northMeters / 111320.0,
            DEMO_LOCATION_CENTER_LONGITUDE + eastMeters / metersPerDegreeLon, true};
}

void attachMeshStatus(Sample& sample) {
    if (!ENABLE_MESH) {
        sample.wifiRssiDbm = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : -128;
        return;
    }
    sample.meshNodeId = mesh.getNodeId();
    auto nodes = mesh.getNodeList();
    bool includesSelf = false;
    for (uint32_t nodeId : nodes) {
        if (nodeId == sample.meshNodeId) includesSelf = true;
        if (sample.meshNodeCount < MAX_MESH_NODE_IDS) sample.meshNodeIds[sample.meshNodeCount++] = nodeId;
    }
    if (!includesSelf && sample.meshNodeCount < MAX_MESH_NODE_IDS) {
        sample.meshNodeIds[sample.meshNodeCount++] = sample.meshNodeId;
    }
    sample.meshConnected = sample.meshNodeCount > 1;
    sample.wifiRssiDbm = MESH_IS_GATEWAY && WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : -128;
}

uint8_t activityCode(const char* value) {
    if (strcmp(value, "light") == 0) return 1;
    if (strcmp(value, "moderate") == 0) return 2;
    if (strcmp(value, "heavy") == 0) return 3;
    return 0;
}

const char* activityName(uint8_t value) {
    switch (value) {
        case 1: return "light";
        case 2: return "moderate";
        case 3: return "heavy";
        default: return "unknown";
    }
}

uint8_t acclimatizationCode(const char* value) {
    if (strcmp(value, "acclimatized") == 0) return 1;
    if (strcmp(value, "not_acclimatized") == 0) return 2;
    return 0;
}

const char* acclimatizationName(uint8_t value) {
    switch (value) {
        case 1: return "acclimatized";
        case 2: return "not_acclimatized";
        default: return "unknown";
    }
}

struct HeartRatePresentation {
    float update(bool fingerDetected, float sensorEstimate, uint32_t now,
                 bool simulationMode, float& reportedRate, bool& simulated) {
        reportedRate = NAN;
        simulated = simulationMode;
        if (simulationMode) {
            candidate = NAN;
            candidateCount = 0;
            sensorConfirmed = false;
            if (!demoStarted) {
                displayed = DEMO_CENTER_BPM;
                demoTarget = DEMO_CENTER_BPM;
                lastDemoChange = now;
                demoStarted = true;
            } else if (now - lastDemoChange >= DEMO_CHANGE_INTERVAL_MS) {
                float step = (int(esp_random() % 25) - 12) / 10.0f;
                demoTarget += step;
                if (demoTarget < DEMO_MIN_BPM) demoTarget = DEMO_MIN_BPM;
                if (demoTarget > DEMO_MAX_BPM) demoTarget = DEMO_MAX_BPM;
                lastDemoChange = now;
            }
            if (!std::isfinite(displayed)) displayed = DEMO_CENTER_BPM;
            displayed += 0.35f * (demoTarget - displayed);
            reportedRate = displayed;
            return displayed;
        }

        simulated = false;
        if (!fingerDetected) {
            candidate = NAN;
            candidateCount = 0;
            sensorConfirmed = false;
            displayed = NAN;
        }

        bool plausible = fingerDetected && std::isfinite(sensorEstimate)
            && sensorEstimate >= 45.0f && sensorEstimate <= 150.0f;
        if (plausible) {
            if (!std::isfinite(candidate) || std::fabs(sensorEstimate - candidate) > 8.0f) {
                candidate = sensorEstimate;
                candidateCount = 1;
            } else {
                candidate = 0.5f * (candidate + sensorEstimate);
                if (candidateCount < 3) ++candidateCount;
            }
            if (candidateCount >= 3) {
                sensorConfirmed = true;
                lastSensorUpdate = now;
            }
        } else {
            candidate = NAN;
            candidateCount = 0;
        }

        if (sensorConfirmed && now - lastSensorUpdate <= SENSOR_HOLD_MS) {
            if (candidateCount >= 3 && std::isfinite(candidate)) {
                if (!std::isfinite(displayed)) displayed = candidate;
                else displayed += 0.35f * (candidate - displayed);
            }
            reportedRate = displayed;
            return displayed;
        }
        sensorConfirmed = false;
        return NAN;
    }

    static constexpr float DEMO_CENTER_BPM = 70.0f;
    static constexpr float DEMO_MIN_BPM = 65.0f;
    static constexpr float DEMO_MAX_BPM = 75.0f;
    static constexpr uint32_t DEMO_CHANGE_INTERVAL_MS = 7000;
    static constexpr uint32_t SENSOR_HOLD_MS = 15000;
    float displayed = NAN;
    float candidate = NAN;
    float demoTarget = DEMO_CENTER_BPM;
    uint8_t candidateCount = 0;
    uint32_t lastDemoChange = 0;
    uint32_t lastSensorUpdate = 0;
    bool demoStarted = false;
    bool sensorConfirmed = false;
};

// LEDC compatibility for Arduino ESP32 2.x and 3.x.
void attachPwm(int pin, int channel, int frequency) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcAttachChannel(pin, frequency, 8, channel);
#else
    ledcSetup(channel, frequency, 8);
    ledcAttachPin(pin, channel);
#endif
}

void writePwm(int channel, uint32_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWriteChannel(channel, duty);
#else
    ledcWrite(channel, duty);
#endif
}

void setColor(uint8_t red, uint8_t green, uint8_t blue) {
    if (!ENABLE_RGB_LED) return;
    // The Keyes RGB module is easier to verify with direct channel switching
    // than PWM. Each channel has an onboard resistor; + modules are active-low.
    digitalWrite(LED_R_PIN, RGB_COMMON_ANODE ? (red ? LOW : HIGH) : (red ? HIGH : LOW));
    digitalWrite(LED_G_PIN, RGB_COMMON_ANODE ? (green ? LOW : HIGH) : (green ? HIGH : LOW));
    digitalWrite(LED_B_PIN, RGB_COMMON_ANODE ? (blue ? LOW : HIGH) : (blue ? HIGH : LOW));
}

void setBuzzer(bool on) {
    if (!ENABLE_BUZZER) return;
    if (BUZZER_ACTIVE_MODULE) digitalWrite(BUZZER_PIN, on == BUZZER_ACTIVE_HIGH ? HIGH : LOW);
    else writePwm(3, on ? 128 : (BUZZER_ACTIVE_HIGH ? 0 : 255));
}

bool networkConfigured();

void updateAlerts(thermosafe::HeatState state, uint32_t now, uint32_t stateStartedAt) {
    uint32_t phase = now-stateStartedAt;
    bool sound = false;
    switch (state) {
        case thermosafe::HeatState::Critical:
            setColor(255, 0, 0);
            sound = phase%5000 < 1400 && (phase%5000)%300 < 150;
            break;
        case thermosafe::HeatState::Warning:
            setColor(255, 80, 0);
            // Caution and warning are both yellow-family states on the clip.
            // Keep a single, clearly separated reminder every five seconds.
            sound = phase % 5000 < 180;
            break;
        case thermosafe::HeatState::Caution:
            setColor(255, 180, 0);
            sound = phase % 5000 < 140;
            break;
        case thermosafe::HeatState::Normal: setColor(0, 255, 0); break;
        default:
            setColor(phase%1000 < 500 ? 100 : 0, 0, phase%1000 < 500 ? 100 : 0);
            sound = phase >= 5000 && (phase-5000)%30000 < 600 && ((phase-5000)%30000)%300 < 100;
    }
    // A short blue flash indicates backend loss without concealing heat alarms.
    uint32_t backendSuccess = lastBackendSuccessMs.load();
    bool connected = ENABLE_MESH && !MESH_IS_GATEWAY
        ? mesh.getNodeList().size() > 1
        : backendSuccess && now-backendSuccess < 20000;
    // Show network loss locally; a mesh worker watches its gateway link.
    if ((ENABLE_MESH || networkConfigured()) && !connected
            && state != thermosafe::HeatState::Unknown && now%2000 < 100) {
        setColor(0, 0, 150);
    }
    setBuzzer(sound);
}

void readSerialCommands(uint32_t now, bool& testMode, thermosafe::HeatState& testState,
                        uint32_t& testStartedAt, bool& pulseTrace, bool& resetExposure,
                        const char*& activityLevel, const char*& acclimatizationStatus) {
    if (ENABLE_MESH && MESH_IS_GATEWAY) {
        static char line[64];
        static size_t length = 0;
        while (Serial.available()) {
            const char value = char(Serial.read());
            if (value == '\r' || value == '\n') {
                if (length) {
                    line[length] = '\0';
                    handleMasterSerialLine(line);
                    length = 0;
                }
            } else if (length + 1 < sizeof(line)) {
                line[length++] = value;
            } else {
                length = 0;
                Serial.println("[MESH CONTROL] command too long; try g|y|r|a [worker-id|all]");
            }
        }
        return;
    }
    while (Serial.available()) {
        char command = char(Serial.read());
        if (command == 'g' || command == 'G') {
            testMode = true;
            testState = thermosafe::HeatState::Normal;
            Serial.println("[LED TEST] Green: simulated Normal state; sensor readings remain real.");
        } else if (command == 'y' || command == 'Y') {
            testMode = true;
            testState = thermosafe::HeatState::Caution;
            Serial.println("[LED TEST] Yellow: simulated Caution state; sensor readings remain real.");
        } else if (command == 'r' || command == 'R') {
            testMode = true;
            testState = thermosafe::HeatState::Critical;
            Serial.println("[LED TEST] Red + buzzer: simulated Critical state; sensor readings remain real.");
        } else if (command == 'a' || command == 'A') {
            testMode = false;
            Serial.println("[LED TEST] Automatic mode: LED and buzzer follow real sensor status.");
        } else if (command == 'p' || command == 'P') {
            pulseTrace = true;
            Serial.println("[PULSE TRACE] Streaming WAVE,millis,adc at 100 samples/second.");
        } else if (command == 'x' || command == 'X') {
            pulseTrace = false;
            Serial.println("[PULSE TRACE] Stopped.");
        } else if (command == 's' || command == 'S') {
            resetExposure = true;
            Serial.println("[EXPOSURE] Shift timers reset requested.");
        } else if (command == 'i' || command == 'I') {
            activityLevel = "light";
            Serial.println("[DEMO INPUT] Activity level set to light.");
        } else if (command == 'm' || command == 'M') {
            activityLevel = "moderate";
            Serial.println("[DEMO INPUT] Activity level set to moderate.");
        } else if (command == 'h' || command == 'H') {
            activityLevel = "heavy";
            Serial.println("[DEMO INPUT] Activity level set to heavy.");
        } else if (command == 'c' || command == 'C') {
            acclimatizationStatus = "acclimatized";
            Serial.println("[DEMO INPUT] Acclimatization set to acclimatized.");
        } else if (command == 'n' || command == 'N') {
            acclimatizationStatus = "not_acclimatized";
            Serial.println("[DEMO INPUT] Acclimatization set to not_acclimatized.");
        } else if (command == 'u' || command == 'U') {
            activityLevel = "unknown";
            acclimatizationStatus = "unknown";
            Serial.println("[DEMO INPUT] Activity and acclimatization set to unknown.");
        } else if (command != '\r' && command != '\n' && command != ' ') {
            Serial.println("Commands: g/y/r/a=LED tests/auto, s=reset timers, i/m/h=light/moderate/heavy, c/n/u=acclimatization context.");
        }
        testStartedAt = now;
    }
}

const char* ledName(thermosafe::HeatState state) {
    switch (state) {
        case thermosafe::HeatState::Normal: return "GREEN";
        case thermosafe::HeatState::Caution: return "YELLOW";
        case thermosafe::HeatState::Warning: return "ORANGE";
        case thermosafe::HeatState::Critical: return "RED";
        default: return "PURPLE BLINK (unknown)";
    }
}

void printDuration(uint64_t milliseconds) {
    const uint64_t seconds = milliseconds / 1000;
    Serial.printf("%02llu:%02llu:%02llu",
                  static_cast<unsigned long long>(seconds / 3600),
                  static_cast<unsigned long long>((seconds / 60) % 60),
                  static_cast<unsigned long long>(seconds % 60));
}

void renderDashboard(bool environmentOk, float temperature, float humidity,
                     thermosafe::HeatState heatState, thermosafe::HeatState overallState,
                     thermosafe::HeatState displayedState, bool testMode, bool remoteTestMode,
                     int meshDemo,
                     thermosafe::HeatState testState, bool pulseEnabled,
                     int pulseRaw, int pulseRange, float bpm, bool bpmSimulated,
                     double latitude, double longitude, bool locationSimulated,
                     bool lightEnabled, int lightRaw, float lightPercent, bool sunlightActive,
                     const char* activityLevel, const char* acclimatizationStatus) {
    Serial.println();
    Serial.println("[THERMOSAFE STATUS]");
    Serial.println("--------------------");
    Serial.printf("AMBIENT HEAT: %-9s OVERALL RISK: %-9s LED: %s (%s)\n",
                  thermosafe::stateName(heatState), thermosafe::stateName(overallState),
                  ledName(displayedState), testMode ? "SERIAL TEST" : remoteTestMode ? "REMOTE TEST"
                      : meshDemo == 1 ? "MESH GREEN" : meshDemo == 2 ? "MESH YELLOW"
                      : meshDemo == 3 ? "MESH RED" : "AUTO");
    if (std::isfinite(temperature)) Serial.printf("TEMP: %.1f C  ", temperature);
    else Serial.print("TEMP: --  ");
    if (std::isfinite(humidity)) Serial.printf("HUMID: %.1f %%\n", humidity);
    else Serial.println("HUMID: --");
    const double hiC = environmentOk ? thermosafe::heatIndexC(temperature, humidity) : NAN;
    const auto hiCategory = environmentOk
        ? thermosafe::heatIndexCategory(hiC, temperature)
        : thermosafe::HeatIndexCategory::Unavailable;
    Serial.print("HEAT INDEX: ");
    if (std::isfinite(hiC)) Serial.printf("%.1f C | ", hiC);
    else Serial.print("not applicable | ");
    Serial.printf("category %s\n", thermosafe::heatIndexCategoryName(hiCategory));
    const double hiScore = environmentOk ? thermosafe::heatIndexDemoScore(hiC, temperature) : NAN;
    const double temperatureScore = environmentOk ? thermosafe::temperatureDemoScore(temperature) : NAN;
    const double humidityScore = environmentOk ? thermosafe::humidityDemoScore(humidity) : NAN;
    const double brightScore = lightEnabled
        ? thermosafe::brightLightExposureScore(exposure.brightTotalMs) : NAN;
    const double heatDurationScore = thermosafe::heatDurationRiskScore(exposure.heatTotalMs);
    const double activityAdjustment = thermosafe::activityDemoAdjustment(activityLevel);
    const double acclimatizationAdjustment = thermosafe::acclimatizationDemoAdjustment(acclimatizationStatus);
    const uint32_t now = millis();
    const bool backendFresh = remoteAssessmentState.load() >= 0
        && int32_t(remoteAssessmentExpiresAt.load() - now) > 0;
    const bool uvFresh = remoteSiteUvExpiresAt.load() != 0
        && int32_t(remoteSiteUvExpiresAt.load() - now) > 0;
    const int uvTenths = uvFresh ? remoteSiteUvTenths.load() : -1;
    const double siteUv = uvTenths >= 0 ? uvTenths / 10.0 : NAN;
    const double totalScore = thermosafe::combinedRiskScore(hiScore, temperatureScore, humidityScore,
        brightScore, heatDurationScore, siteUv, activityAdjustment + acclimatizationAdjustment);
    Serial.printf("DEMO RISK SCORE: %.1f/100 | HI %.1f T %.1f RH %.1f BRIGHT-TIME %.1f HEAT-TIME %.1f",
                  totalScore, hiScore, temperatureScore, humidityScore, brightScore, heatDurationScore);
    if (std::isfinite(siteUv)) Serial.printf(" UV %.1f", siteUv);
    else Serial.print(" UV --");
    Serial.println();
    if (std::isfinite(siteUv)) {
        const int backendScore = backendFresh ? remoteAssessmentScoreTenths.load() : -1;
        Serial.printf("SITE UV INDEX: %.1f | %s | backend score ", siteUv,
                      backendFresh ? "Open-Meteo worksite forecast" : "cached; backend unavailable");
        if (backendScore >= 0) Serial.printf("%.1f/100\n", backendScore / 10.0);
        else Serial.println("--");
    } else {
        Serial.println("SITE UV INDEX: unavailable | waiting for a fresh backend weather assessment");
    }
    Serial.printf("ACTIVITY: %s | ACCLIMATIZATION: %s | context adjustment +%.1f\n",
                  activityLevel, acclimatizationStatus,
                  activityAdjustment + acclimatizationAdjustment);
    if (!pulseEnabled) {
        Serial.println("PULSE INPUT: disabled");
    } else {
        if (pulseRaw >= 4087) Serial.printf("PULSE INPUT: saturated high (%d ADC)\n", pulseRaw);
        else if (pulseRaw <= 8) Serial.printf("PULSE INPUT: saturated low (%d ADC)\n", pulseRaw);
        else Serial.printf("PULSE INPUT: %d ADC (swing %d)\n", pulseRaw, pulseRange);
    }
    if (bpmSimulated) Serial.printf("BPM: %.1f (simulated)\n", bpm);
    else Serial.printf("BPM: %.1f (real)\n", bpm);
    if (std::isfinite(latitude) && std::isfinite(longitude)) {
        Serial.printf("LOCATION: %.6f, %.6f (%s)\n", latitude, longitude,
                      locationSimulated ? "simulated" : "GPS");
    } else {
        Serial.println("LOCATION: unavailable");
    }
    if (!lightEnabled) {
        Serial.println("LIGHT: disabled");
    } else {
        Serial.printf("PHOTORESISTOR: %d ADC | relative brightness %.1f%% | bright threshold %.1f%% | %s\n",
                      lightRaw, lightPercent, SUNLIGHT_THRESHOLD_PERCENT,
                      sunlightActive ? "BRIGHT" : "below threshold");
        Serial.print("ESTIMATED BRIGHT-LIGHT EXPOSURE total: ");
        printDuration(exposure.brightTotalMs);
        Serial.print(" | continuous: ");
        printDuration(exposure.brightContinuousMs);
        Serial.println();
    }
    Serial.print("HEAT EXPOSURE total: ");
    printDuration(exposure.heatTotalMs);
    Serial.print(" | continuous: ");
    printDuration(exposure.heatContinuousMs);
    Serial.println();
    Serial.printf("SENSOR: %s\n", environmentOk ? "OK" : "CHECK SENSOR");
    if (ENABLE_MESH && MESH_IS_GATEWAY)
        Serial.println("MESH TEST: g|y|r|a [worker-id|all] (default target: all)");
    else
        Serial.println("TEST: G green | Y yellow | R red+buzzer | A auto | I/M/H activity | C/N/U acclimatization");
    Serial.println("--------------------");
}

bool networkConfigured() {
    return String(WIFI_SSID) != "YOUR_WIFI_NAME" && String(DEVICE_API_KEY).indexOf("replace-") != 0;
}

String serializeTelemetry(const Sample& sample) {
    const int demoMode = meshDemoMode.load();
    JsonDocument doc;
    doc["schema_version"] = 1;
    doc["device_id"] = deviceId;
    doc["boot_id"] = bootId;
    doc["sequence"] = sample.sequence;
    doc["uptime_ms"] = sample.uptimeMs;
    doc["source"] = "hardware";
    doc["measurement_mode"] = sample.demoScenario ? "simulated_demo" : "sensor";
    doc["demo_scenario_simulated"] = sample.demoScenario;
    doc["demo_scenario"] = sample.demoScenario
        ? (demoMode == 1 ? "green" : demoMode == 2 ? "yellow" : "red") : nullptr;
    doc["firmware_version"] = FIRMWARE_VERSION;
    doc["environment_sensor_ok"] = sample.environmentOk;
    if (sample.environmentOk) {
        doc["temperature_c"] = sample.temperature;
        doc["humidity_pct"] = sample.humidity;
    } else {
        doc["temperature_c"] = nullptr;
        doc["humidity_pct"] = nullptr;
    }
    if ((sample.demoScenario || LOCAL_PULSE_SENSOR) && sample.pulseRaw >= 0) doc["pulse_raw"] = sample.pulseRaw;
    else doc["pulse_raw"] = nullptr;
    if ((sample.bpmSimulated || (LOCAL_PULSE_SENSOR && ENABLE_EXPERIMENTAL_BPM)) && std::isfinite(sample.bpm)) doc["pulse_bpm"] = sample.bpm;
    else doc["pulse_bpm"] = nullptr;
    doc["pulse_quality"] = sample.bpmSimulated ? "simulated" :
        !LOCAL_PULSE_SENSOR ? "disabled" :
        !ENABLE_EXPERIMENTAL_BPM ? "unvalidated" :
        std::isfinite(sample.bpm) ? "experimental" : "insufficient_signal";
    if (sample.locationSimulated && std::isfinite(sample.latitude)
            && std::isfinite(sample.longitude)) {
        doc["latitude_deg"] = sample.latitude;
        doc["longitude_deg"] = sample.longitude;
        doc["location_source"] = "simulated";
    } else {
        doc["latitude_deg"] = nullptr;
        doc["longitude_deg"] = nullptr;
        doc["location_source"] = "unavailable";
    }
    doc["mesh_role"] = !ENABLE_MESH ? "disabled" : MESH_IS_GATEWAY ? "gateway" : "worker";
    doc["mesh_connected"] = sample.meshConnected;
    if (ENABLE_MESH) doc["mesh_node_id"] = sample.meshNodeId;
    else doc["mesh_node_id"] = nullptr;
    JsonArray nodeIds = doc["mesh_node_ids"].to<JsonArray>();
    for (uint8_t i = 0; i < sample.meshNodeCount; ++i) nodeIds.add(sample.meshNodeIds[i]);
    if (ENABLE_MESH && MESH_IS_GATEWAY) {
        JsonDocument topology;
        DeserializationError error = deserializeJson(topology, mesh.subConnectionJson());
        if (!error) doc["mesh_topology"] = topology.as<JsonObject>();
    }
    if (sample.wifiRssiDbm >= -127 && sample.wifiRssiDbm <= 0) doc["wifi_rssi_dbm"] = sample.wifiRssiDbm;
    else doc["wifi_rssi_dbm"] = nullptr;
    doc["light_sensor_ok"] = sample.lightSensorOk;
    if (sample.lightSensorOk) {
        doc["light_raw"] = sample.lightRaw;
        doc["light_intensity_pct"] = sample.lightPercent;
        doc["bright_light_threshold_pct"] = SUNLIGHT_THRESHOLD_PERCENT;
    } else {
        doc["light_raw"] = nullptr;
        doc["light_intensity_pct"] = nullptr;
        doc["bright_light_threshold_pct"] = nullptr;
    }
    doc["bright_light_active"] = sample.sunlightActive;
    doc["bright_exposure_seconds"] = sample.brightExposureMs / 1000;
    doc["bright_continuous_seconds"] = sample.brightContinuousMs / 1000;
    doc["heat_exposure_seconds"] = sample.heatExposureMs / 1000;
    doc["heat_continuous_seconds"] = sample.heatContinuousMs / 1000;
    doc["activity_level"] = activityName(sample.activityLevel);
    doc["acclimatization_status"] = acclimatizationName(sample.acclimatizationStatus);
    const double heatIndex = sample.environmentOk
        ? thermosafe::heatIndexC(sample.temperature, sample.humidity) : NAN;
    const bool uvFresh = remoteSiteUvExpiresAt.load() != 0
        && int32_t(remoteSiteUvExpiresAt.load() - millis()) > 0;
    const int uvTenths = uvFresh ? remoteSiteUvTenths.load() : -1;
    const double siteUv = sample.demoScenario ? sample.demoUvIndex
        : uvTenths >= 0 ? uvTenths / 10.0 : NAN;
    double score = thermosafe::combinedRiskScore(
        sample.environmentOk ? thermosafe::heatIndexDemoScore(heatIndex, sample.temperature) : NAN,
        sample.environmentOk ? thermosafe::temperatureDemoScore(sample.temperature) : NAN,
        sample.environmentOk ? thermosafe::humidityDemoScore(sample.humidity) : NAN,
        LOCAL_LIGHT_SENSOR ? thermosafe::brightLightExposureScore(sample.brightExposureMs) : NAN,
        thermosafe::heatDurationRiskScore(sample.heatExposureMs), siteUv,
        thermosafe::activityDemoAdjustment(activityName(sample.activityLevel))
            + thermosafe::acclimatizationDemoAdjustment(acclimatizationName(sample.acclimatizationStatus)));
    const auto ambient = sample.environmentOk
        ? thermosafe::assess(sample.temperature, sample.humidity) : thermosafe::HeatState::Unknown;
    auto alert = thermosafe::combine(ambient, score);
    if (sample.demoScenario) {
        // Test commands are an explicit presentation state. Keep the transmitted
        // score and alert state in the same band as the requested LED/buzzer mode.
        if (demoMode == 1) {
            score = std::min(score, 29.0);
            alert = thermosafe::HeatState::Normal;
        } else if (demoMode == 2) {
            score = std::max(30.0, std::min(score, 54.0));
            alert = thermosafe::HeatState::Caution;
        } else {
            score = std::max(75.0, score);
            alert = thermosafe::HeatState::Critical;
        }
    }
    if (std::isfinite(heatIndex)) doc["heat_index_c"] = heatIndex;
    else doc["heat_index_c"] = nullptr;
    if (std::isfinite(siteUv)) doc["site_uv_index"] = siteUv;
    else doc["site_uv_index"] = nullptr;
    doc["site_uv_index_source"] = sample.demoScenario ? "simulated_demo"
        : std::isfinite(siteUv) ? "weather" : "unavailable";
    doc["heat_index_category"] = thermosafe::heatIndexCategoryName(
        thermosafe::heatIndexCategory(heatIndex, sample.environmentOk ? sample.temperature : NAN));
    if (std::isfinite(score)) doc["risk_score_demo"] = score;
    else doc["risk_score_demo"] = nullptr;
    doc["alert_level"] = thermosafe::stateName(alert);
    doc["alert_mode"] = demoMode == 1 ? "green" : demoMode == 2 ? "yellow"
        : demoMode == 3 ? "red" : "auto";
    if (sample.demoScenario) {
        const char* scenario = demoMode == 1 ? "GREEN" : demoMode == 2 ? "YELLOW" : "RED";
        char explanation[300];
        snprintf(explanation, sizeof(explanation),
                 "%s DEMO (simulated): %.1f °C, %.0f%% RH, heat index %.1f °C (%s), %.0f%% brightness, %.1f UV, %.0f min bright-light and %.0f min elevated-heat exposure.",
                 scenario, sample.temperature, sample.humidity,
                 std::isfinite(heatIndex) ? heatIndex : sample.temperature,
                 thermosafe::heatIndexCategoryName(thermosafe::heatIndexCategory(heatIndex, sample.temperature)),
                 sample.lightPercent, siteUv, sample.brightExposureMs / 60000.0,
                 sample.heatExposureMs / 60000.0);
        doc["alert_explanation"] = explanation;
        if (demoMode == 1) {
            doc["recommended_action"] = "Routine monitoring; keep water and shade available. Demo values are simulated, not a worker safety determination.";
        } else if (demoMode == 2) {
            doc["recommended_action"] = "Increase monitoring, pause strenuous work as needed, and make water and a shaded recovery break available. Demo values are simulated.";
        } else {
            doc["recommended_action"] = "Stop work for this demo, move to a cooler area, and alert the site manager. Demo values are simulated, not a medical prediction.";
        }
    } else if (!sample.environmentOk) {
        doc["alert_explanation"] = "Temperature/humidity unavailable; Auto cannot confirm a lower heat risk.";
        doc["recommended_action"] = "Check the environment sensor; keep normal water and shade precautions available.";
    } else if (alert == thermosafe::HeatState::Critical) {
        doc["alert_explanation"] = "The combined demonstration score or heat-index category is critical.";
        doc["recommended_action"] = "Stop work for this demo, move to a cooler area, and alert the site manager.";
    } else if (alert == thermosafe::HeatState::Warning) {
        doc["alert_explanation"] = "Combined heat-index, humidity, brightness/exposure, UV, and context factors are elevated.";
        doc["recommended_action"] = "Pause strenuous work, move to shade, hydrate, and have the site manager review.";
    } else if (alert == thermosafe::HeatState::Caution) {
        doc["alert_explanation"] = "One or more combined environmental or exposure factors reached the caution band.";
        doc["recommended_action"] = "Increase monitoring and make water and a shaded recovery break available.";
    } else {
        doc["alert_explanation"] = "Available measurements are below the configured demonstration warning bands.";
        doc["recommended_action"] = "Continue monitoring; this screening is not a guarantee of safety.";
    }
    String body;
    serializeJson(doc, body);
    return body;
}

bool postTelemetryBody(const String& body) {
    HTTPClient http;
    http.setConnectTimeout(1500);
    http.setTimeout(2000);
    String url = String(API_BASE_URL);
    while (url.endsWith("/")) url.remove(url.length()-1);
    if (!http.begin(url+"/v1/telemetry")) return false;
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Device-Key", DEVICE_API_KEY);
    int code = http.POST(body);
    http.end();
    if (code >= 200 && code < 300) {
        lastBackendSuccessMs.store(millis());
        return true;
    }
    Serial.printf("Telemetry HTTP %d (check API URL/key/network)\n", code);
    return false;
}

void networkTask(void*) {
    if (ENABLE_MESH && !MESH_IS_GATEWAY) vTaskDelete(nullptr);
    if (!ENABLE_MESH) {
        WiFi.mode(WIFI_STA);
        WiFi.setAutoReconnect(true);
    }
    uint32_t lastAttempt = 0;
    uint32_t lastAlertTestPoll = 0;
    bool attempted = false;
    Sample sample{};
    for (;;) {
        if (!networkConfigured()) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        uint32_t now = millis();
        if (WiFi.status() != WL_CONNECTED) {
            if (!ENABLE_MESH && (!attempted || now-lastAttempt >= WIFI_RETRY_MS)) {
                WiFi.disconnect();
                WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
                lastAttempt = now;
                attempted = true;
            }
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }
        now = millis();
        if (now-lastAlertTestPoll >= 1000) {
            lastAlertTestPoll = now;
            String commandUrl = String(API_BASE_URL);
            while (commandUrl.endsWith("/")) commandUrl.remove(commandUrl.length()-1);
            commandUrl += "/v1/device/" + deviceId + "/alert-test";
            HTTPClient commandHttp;
            commandHttp.setConnectTimeout(700);
            commandHttp.setTimeout(700);
            if (commandHttp.begin(commandUrl)) {
                commandHttp.addHeader("X-Device-Key", DEVICE_API_KEY);
                int commandCode = commandHttp.GET();
                if (commandCode == 200) {
                    JsonDocument commandDoc;
                    DeserializationError error = deserializeJson(commandDoc, commandHttp.getString());
                    if (!error) {
                        const char* requested = commandDoc["state"] | "";
                        int value = -1;
                        if (strcmp(requested, "unknown") == 0) value = int(thermosafe::HeatState::Unknown);
                        else if (strcmp(requested, "normal") == 0) value = int(thermosafe::HeatState::Normal);
                        else if (strcmp(requested, "caution") == 0) value = int(thermosafe::HeatState::Caution);
                        else if (strcmp(requested, "warning") == 0) value = int(thermosafe::HeatState::Warning);
                        else if (strcmp(requested, "critical") == 0) value = int(thermosafe::HeatState::Critical);
                        remoteAlertTestState.store(value);
                        remoteAlertTestExpiresAt.store(value < 0 ? 0 : millis() + 2500);

                        const char* reportedRisk = commandDoc["risk_state"] | "";
                        int riskValue = -1;
                        if (strcmp(reportedRisk, "unknown") == 0) riskValue = int(thermosafe::HeatState::Unknown);
                        else if (strcmp(reportedRisk, "normal") == 0) riskValue = int(thermosafe::HeatState::Normal);
                        else if (strcmp(reportedRisk, "caution") == 0) riskValue = int(thermosafe::HeatState::Caution);
                        else if (strcmp(reportedRisk, "warning") == 0) riskValue = int(thermosafe::HeatState::Warning);
                        else if (strcmp(reportedRisk, "critical") == 0) riskValue = int(thermosafe::HeatState::Critical);
                        remoteAssessmentState.store(riskValue);
                        remoteAssessmentExpiresAt.store(riskValue < 0 ? 0 : millis() + 2500);
                        JsonVariantConst reportedUv = commandDoc["site_uv_index"];
                        int uvTenths = -1;
                        if (reportedUv.is<float>() || reportedUv.is<int>()) {
                            float uv = reportedUv.as<float>();
                            if (std::isfinite(uv) && uv >= 0) uvTenths = lroundf(uv * 10.0f);
                        }
                        remoteSiteUvTenths.store(uvTenths);
                        remoteSiteUvExpiresAt.store(uvTenths >= 0 ? millis() + 600000 : 0);
                        JsonVariantConst reportedScore = commandDoc["combined_risk_score"];
                        int scoreTenths = -1;
                        if (reportedScore.is<float>() || reportedScore.is<int>()) {
                            float score = reportedScore.as<float>();
                            if (std::isfinite(score) && score >= 0) scoreTenths = lroundf(score * 10.0f);
                        }
                        remoteAssessmentScoreTenths.store(scoreTenths);
                        lastBackendSuccessMs.store(millis());
                    }
                }
                commandHttp.end();
            }
        }
        MeshPacket incoming{};
        if (ENABLE_MESH && MESH_IS_GATEWAY
                && xQueueReceive(meshIncomingQueue, &incoming, 0) == pdTRUE) {
            postTelemetryBody(String(incoming.json));
        }
        if (xQueueReceive(telemetryQueue, &sample, pdMS_TO_TICKS(100)) != pdTRUE) continue;
        if (uint64_t(esp_timer_get_time()/1000)-sample.uptimeMs > uint64_t(TELEMETRY_INTERVAL_MS) * 5) continue;
        postTelemetryBody(serializeTelemetry(sample));
    }
}

void setup() {
    Serial.begin(115200);
    meshDemoStartedAt.store(millis());
    if (HAS_LOCAL_WORKER_SENSORS) environmentSensor.begin();
    if (LOCAL_LIGHT_SENSOR) {
        analogReadResolution(12);
        pinMode(LIGHT_PIN, INPUT);
    }
    if (LOCAL_PULSE_SENSOR) {
        analogReadResolution(12);
        pinMode(PULSE_PIN, INPUT);
    }
    if (ENABLE_RGB_LED) {
        pinMode(LED_R_PIN, OUTPUT);
        pinMode(LED_G_PIN, OUTPUT);
        pinMode(LED_B_PIN, OUTPUT);
        setColor(0, 0, 0);
    }
    if (ENABLE_BUZZER) {
        pinMode(BUZZER_PIN, OUTPUT);
        if (!BUZZER_ACTIVE_MODULE) attachPwm(BUZZER_PIN, 3, BUZZER_FREQUENCY_HZ);
        setBuzzer(false);
    }
    char id[32], boot[24];
    snprintf(id, sizeof(id), "clip-%012llx", (unsigned long long)ESP.getEfuseMac());
    snprintf(boot, sizeof(boot), "%08lx%08lx", (unsigned long)esp_random(), (unsigned long)esp_random());
    deviceId = strlen(DEVICE_ID) ? DEVICE_ID : id;
    bootId = boot;
    if (ENABLE_MESH) {
        mesh.setDebugMsgTypes(ERROR | CONNECTION);
        if (MESH_IS_GATEWAY) {
            meshIncomingQueue = xQueueCreate(MAX_MESH_PACKETS_QUEUED, sizeof(MeshPacket));
            mesh.initAsBridge(MESH_PREFIX, MESH_PASSWORD, WIFI_SSID, WIFI_PASSWORD,
                              &meshScheduler, MESH_PORT);
        } else {
            mesh.init(MESH_PREFIX, MESH_PASSWORD, &meshScheduler, MESH_PORT,
                      WIFI_AP_STA, 0);
            mesh.setContainsRoot(true);
        }
        mesh.onReceive(&meshReceiveCallback);
        mesh.onNewConnection([](uint32_t nodeId) {
            Serial.printf("[MESH] joined node=%u\n", nodeId);
        });
        mesh.onChangedConnections([]() {
            Serial.printf("[MESH] topology changed; nodes=%u\n",
                          unsigned(mesh.getNodeList().size()));
        });
        Serial.printf("[MESH] role=%s node=%u gateway uplink=%s\n",
                      MESH_IS_GATEWAY ? "gateway" : "worker",
                      unsigned(mesh.getNodeId()),
                      MESH_IS_GATEWAY ? "Wi-Fi" : "mesh relay");
    }
    exposurePreferences.begin("thermosafe", false);
    exposure.begin(millis(), exposurePreferences.getULong64("bright_ms", 0),
                   exposurePreferences.getULong64("heat_ms", 0));
    Serial.printf("ThermoSafe %s | Device %s\n", FIRMWARE_VERSION, deviceId.c_str());
    if (ENABLE_MESH && MESH_IS_GATEWAY)
        Serial.println("Site Manager commands: g|y|r|a [worker-id|all]; target defaults to all workers.");
    else
        Serial.println("LED demo commands: g=green, y=yellow, r=red+buzzer, a=automatic.");
    Serial.println("Exposure command: s resets cumulative bright-light and heat timers for a new shift.");
    Serial.println("Demo context: i/m/h sets activity level; c/n/u sets acclimatization context.");
    if (LOCAL_LIGHT_SENSOR) Serial.printf("Photoresistor GPIO%d measures relative brightness, not UV or solar radiation.\n", LIGHT_PIN);
    if (!HAS_LOCAL_WORKER_SENSORS) Serial.println("[GATEWAY] Sensorless Site Manager role; worker readings arrive over the mesh.");
    if (!networkConfigured()) Serial.println("Configure Wi-Fi/API in include/config.h; local sensing remains active.");
    if (!ENABLE_MESH && networkConfigured()) {
        WiFi.mode(WIFI_STA);
        const int networkCount = WiFi.scanNetworks();
        bool targetFound = false;
        for (int i = 0; i < networkCount; ++i) {
            if (WiFi.SSID(i) == WIFI_SSID) {
                Serial.printf("[WIFI SCAN] target visible, RSSI=%d dBm, channel=%d, auth=%d\n",
                              WiFi.RSSI(i), WiFi.channel(i), int(WiFi.encryptionType(i)));
                targetFound = true;
            }
        }
        if (!targetFound) Serial.println("[WIFI SCAN] configured SSID not visible to ESP32 on 2.4 GHz.");
        WiFi.scanDelete();
    }
    if (!ENABLE_MESH || MESH_IS_GATEWAY) {
        telemetryQueue = xQueueCreate(1, sizeof(Sample));
        if (ENABLE_MESH && !meshIncomingQueue) {
            meshIncomingQueue = xQueueCreate(MAX_MESH_PACKETS_QUEUED, sizeof(MeshPacket));
        }
        if (!telemetryQueue || (ENABLE_MESH && !meshIncomingQueue)
                || xTaskCreatePinnedToCore(networkTask, "telemetry", 8192, nullptr, 1, nullptr, 0) != pdPASS) {
            Serial.println("Network task unavailable; local sensing remains active.");
            telemetryQueue = nullptr;
        }
    } else {
        Serial.println("[MESH] Worker mode: sensor telemetry routes through the gateway.");
    }
}

void loop() {
    static uint32_t lastEnvironmentRead = 0, lastGoodReading = 0, lastPost = 0, lastPulse = 0;
    static uint32_t lastDiagnostic = 0, lastLightRead = 0, lastExposureSave = 0, lastWifiDiagnostic = 0;
    static uint32_t stateStartedAt = 0, sequence = 0;
    static int previousMeshDemoMode = -1;
    static float temperature = NAN, humidity = NAN;
    static float displayedBpm = NAN, bpmForTelemetry = NAN;
    static float lightPercent = NAN;
    static bool bpmSimulated = true;
    static bool lightInitialized = false, sunlightActive = false;
    static HeartRatePresentation heartRatePresentation;
    static bool environmentOk = false;
    static int pulseRaw = 0, lightRaw = 0;
    static int pulseMinimum = 4095, pulseMaximum = 0;
    static bool testMode = false, pulseTrace = false, resetExposure = false;
    static const char* activityLevel = WORKER_ACTIVITY_LEVEL;
    static const char* acclimatizationStatus = WORKER_ACCLIMATIZATION_STATUS;
    static auto testState = thermosafe::HeatState::Unknown;
    static uint32_t testStartedAt = 0;
    static int previousRemoteValue = -2;
    static uint32_t remoteTestStartedAt = 0;
    static auto state = thermosafe::HeatState::Unknown;
    uint32_t now = millis();
    if (ENABLE_MESH) mesh.update();
    readSerialCommands(now, testMode, testState, testStartedAt, pulseTrace, resetExposure,
                       activityLevel, acclimatizationStatus);
    if (meshAutoCommandPending.exchange(false) && testMode) {
        testMode = false;
        Serial.println("[LED TEST] Site Manager selected Auto; cleared stale serial test state.");
    }
    if (now-lastWifiDiagnostic >= 5000 && (ENABLE_MESH || networkConfigured())) {
        lastWifiDiagnostic = now;
        if (ENABLE_MESH) {
            auto nodes = mesh.getNodeList();
            Serial.printf("[MESH] role=%s node=%u known_nodes=%u uplink=%s\n",
                          MESH_IS_GATEWAY ? "gateway" : "worker",
                          unsigned(mesh.getNodeId()), unsigned(nodes.size()),
                          MESH_IS_GATEWAY
                              ? (WiFi.status() == WL_CONNECTED ? "Wi-Fi connected" : "Wi-Fi waiting")
                              : "via master");
        } else if (WiFi.status() == WL_CONNECTED) {
            Serial.printf("[WIFI] connected, IP=%s, RSSI=%d dBm\n",
                          WiFi.localIP().toString().c_str(), WiFi.RSSI());
        } else {
            Serial.printf("[WIFI] not connected, status=%d\n", int(WiFi.status()));
        }
    }
    if (resetExposure) {
        exposure.reset(now);
        exposurePreferences.putULong64("bright_ms", 0);
        exposurePreferences.putULong64("heat_ms", 0);
        resetExposure = false;
        Serial.println("[EXPOSURE] Cumulative shift timers cleared; continuous timers restarted.");
    }
    if (LOCAL_LIGHT_SENSOR && now-lastLightRead >= 100) {
        lastLightRead = now;
        lightRaw = analogRead(LIGHT_PIN);
        const int orientedRaw = LIGHT_BRIGHT_IS_HIGHER ? lightRaw : 4095-lightRaw;
        const int calibratedSpan = std::max(1, LIGHT_BRIGHT_ORIENTED_RAW-LIGHT_DARK_ORIENTED_RAW);
        const float normalized = float(orientedRaw-LIGHT_DARK_ORIENTED_RAW)/calibratedSpan;
        const float instantPercent = 100.0f * std::max(0.0f, std::min(1.0f, normalized));
        if (!lightInitialized) {
            lightPercent = instantPercent;
            lightInitialized = true;
        } else {
            lightPercent += 0.2f * (instantPercent-lightPercent);
        }
        if (!sunlightActive && lightPercent >= SUNLIGHT_THRESHOLD_PERCENT) sunlightActive = true;
        else if (sunlightActive && lightPercent < SUNLIGHT_THRESHOLD_PERCENT-SUNLIGHT_THRESHOLD_HYSTERESIS_PERCENT) {
            sunlightActive = false;
        }
    }
    if (LOCAL_PULSE_SENSOR && now-lastPulse >= 10) {
        lastPulse = now;
        pulseRaw = analogRead(PULSE_PIN);
        pulseMinimum = std::min(pulseMinimum, pulseRaw);
        pulseMaximum = std::max(pulseMaximum, pulseRaw);
        if (ENABLE_EXPERIMENTAL_BPM) {
            pulse.sample(pulseRaw, now, PULSE_INVERT, PULSE_MIN_AMPLITUDE, PULSE_CONTACT_MIN_RAW);
        }
        if (pulseTrace) Serial.printf("WAVE,%lu,%d\n", (unsigned long)now, pulseRaw);
    }
    if (HAS_LOCAL_WORKER_SENSORS && now-lastEnvironmentRead >= SAMPLE_INTERVAL_MS) {
        lastEnvironmentRead = now;
        temperature = environmentSensor.readTemperature();
        humidity = environmentSensor.readHumidity();
        environmentOk = std::isfinite(temperature) && std::isfinite(humidity)
            && temperature >= 0 && temperature <= 50 && humidity >= 20 && humidity <= 90;
        if (environmentOk) lastGoodReading = now;
    }
    if (now-lastGoodReading > SENSOR_STALE_MS) environmentOk = false;
    auto ambientState = environmentOk ? thermosafe::assess(temperature, humidity)
                                      : thermosafe::HeatState::Unknown;
    const bool elevatedHeat = ambientState == thermosafe::HeatState::Caution
        || ambientState == thermosafe::HeatState::Warning
        || ambientState == thermosafe::HeatState::Critical;
    exposure.update(now, LOCAL_LIGHT_SENSOR && sunlightActive, elevatedHeat);
    const double hiC = environmentOk ? thermosafe::heatIndexC(temperature, humidity) : NAN;
    const double hiScore = environmentOk ? thermosafe::heatIndexDemoScore(hiC, temperature) : NAN;
    const double temperatureScore = environmentOk ? thermosafe::temperatureDemoScore(temperature) : NAN;
    const double humidityScore = environmentOk ? thermosafe::humidityDemoScore(humidity) : NAN;
    const double brightScore = LOCAL_LIGHT_SENSOR
        ? thermosafe::brightLightExposureScore(exposure.brightTotalMs) : NAN;
    const double heatDurationScore = thermosafe::heatDurationRiskScore(exposure.heatTotalMs);
    const bool backendAssessmentFresh = remoteAssessmentState.load() >= 0
        && int32_t(remoteAssessmentExpiresAt.load() - now) > 0;
    const bool remoteUvFresh = remoteSiteUvExpiresAt.load() != 0
        && int32_t(remoteSiteUvExpiresAt.load() - now) > 0;
    const int remoteUvTenths = remoteUvFresh ? remoteSiteUvTenths.load() : -1;
    const double siteUvIndex = remoteUvTenths >= 0 ? remoteUvTenths / 10.0 : NAN;
    const double totalScore = thermosafe::combinedRiskScore(hiScore, temperatureScore, humidityScore,
        brightScore, heatDurationScore, siteUvIndex,
        thermosafe::activityDemoAdjustment(activityLevel)
            + thermosafe::acclimatizationDemoAdjustment(acclimatizationStatus));
    auto next = thermosafe::combine(ambientState, totalScore);
    auto riskRank = [](thermosafe::HeatState value) {
        switch (value) {
            case thermosafe::HeatState::Critical: return 4;
            case thermosafe::HeatState::Warning: return 3;
            case thermosafe::HeatState::Caution: return 2;
            case thermosafe::HeatState::Normal: return 1;
            default: return 0;
        }
    };
    if (backendAssessmentFresh) {
        const auto backendState = static_cast<thermosafe::HeatState>(remoteAssessmentState.load());
        if (next == thermosafe::HeatState::Unknown || backendState == thermosafe::HeatState::Unknown) {
            next = thermosafe::HeatState::Unknown;
        } else if (riskRank(backendState) > riskRank(next)) {
            next = backendState;
        }
    }
    if (next != state) {
        state = next;
        stateStartedAt = now;
    }
    if (now-lastExposureSave >= 60000) {
        lastExposureSave = now;
        exposurePreferences.putULong64("bright_ms", exposure.brightTotalMs);
        exposurePreferences.putULong64("heat_ms", exposure.heatTotalMs);
    }
    int remoteValue = remoteAlertTestState.load();
    const uint32_t remoteExpiry = remoteAlertTestExpiresAt.load();
    bool remoteTestMode = remoteValue >= 0 && int32_t(remoteExpiry - now) > 0;
    auto remoteState = remoteTestMode
        ? static_cast<thermosafe::HeatState>(remoteValue) : state;
    if (remoteTestMode && remoteValue != previousRemoteValue) remoteTestStartedAt = now;
    previousRemoteValue = remoteTestMode ? remoteValue : -1;
    auto displayState = state;
    if (remoteTestMode && state == thermosafe::HeatState::Unknown) {
        if (riskRank(remoteState) >= riskRank(thermosafe::HeatState::Warning)) displayState = remoteState;
    } else if (remoteTestMode && state == thermosafe::HeatState::Normal) {
        displayState = remoteState;
    } else if (remoteTestMode && remoteState == thermosafe::HeatState::Unknown) {
        displayState = thermosafe::HeatState::Unknown;
    } else if (remoteTestMode && riskRank(remoteState) > riskRank(state)) {
        displayState = remoteState;
    }
    if (testMode) displayState = testState;
    uint32_t displayStateStartedAt = remoteTestMode ? remoteTestStartedAt : stateStartedAt;
    if (testMode) displayStateStartedAt = testStartedAt;
    const int meshDemo = meshDemoMode.load();
    if (meshDemo != previousMeshDemoMode) {
        // A Site Manager Auto command must cancel any stale local serial test.
        if (meshDemo == 0 && previousMeshDemoMode > 0 && testMode) {
            testMode = false;
            Serial.println("[LED TEST] Site Manager selected Auto; cleared stale serial test state.");
        }
        previousMeshDemoMode = meshDemo;
    }
    if (HAS_LOCAL_WORKER_SENSORS && meshDemo > 0) {
        if (meshDemo == 1) {
            setColor(0, 255, 0);
            setBuzzer(false);
            displayState = thermosafe::HeatState::Normal;
        } else if (meshDemo == 2) {
            setColor(255, 180, 0);
            setBuzzer((now - meshDemoStartedAt.load()) % 5000 < 140);
            displayState = thermosafe::HeatState::Caution;
        } else {
            setColor(255, 0, 0);
            setBuzzer((now % 600) < 120);
            displayState = thermosafe::HeatState::Critical;
        }
    } else if (HAS_LOCAL_WORKER_SENSORS) updateAlerts(displayState, now, displayStateStartedAt);
    else {
        setColor(0, 0, 0);
        setBuzzer(false);
    }
    if (now-lastDiagnostic >= STATUS_INTERVAL_MS) {
        lastDiagnostic = now;
        float sensorEstimate = pulse.value(now, PULSE_MIN_AMPLITUDE);
        displayedBpm = heartRatePresentation.update(LOCAL_PULSE_SENSOR && pulse.contactDetected(), sensorEstimate,
                                                    now, LOCAL_PULSE_SENSOR && ENABLE_SIMULATED_BPM,
                                                    bpmForTelemetry, bpmSimulated);
        DemoLocation location = demoLocation(now);
        const int pulseRange = pulseMaximum-pulseMinimum;
        if (HAS_LOCAL_WORKER_SENSORS) {
            renderDashboard(environmentOk, temperature, humidity, ambientState, state, displayState,
                            testMode, remoteTestMode, meshDemo, testState, LOCAL_PULSE_SENSOR, pulseRaw, pulseRange, displayedBpm,
                            bpmSimulated, location.latitude, location.longitude, location.simulated,
                            LOCAL_LIGHT_SENSOR, lightRaw, lightPercent, sunlightActive,
                            activityLevel, acclimatizationStatus);
        }
        pulseMinimum = 4095;
        pulseMaximum = 0;
    }
    if (now-lastPost >= TELEMETRY_INTERVAL_MS) {
        lastPost = now;
        DemoLocation location = demoLocation(now);
        Sample sample{};
        sample.uptimeMs = uint64_t(esp_timer_get_time()/1000);
        sample.sequence = sequence++;
        sample.temperature = temperature;
        sample.humidity = humidity;
        sample.bpm = bpmForTelemetry;
        sample.lightPercent = lightPercent;
        sample.latitude = location.latitude;
        sample.longitude = location.longitude;
        sample.pulseRaw = LOCAL_PULSE_SENSOR ? pulseRaw : -1;
        sample.lightRaw = lightRaw;
        sample.brightExposureMs = exposure.brightTotalMs;
        sample.heatExposureMs = exposure.heatTotalMs;
        sample.brightContinuousMs = uint32_t(exposure.brightContinuousMs);
        sample.heatContinuousMs = uint32_t(exposure.heatContinuousMs);
        sample.bpmSimulated = LOCAL_PULSE_SENSOR && bpmSimulated;
        sample.locationSimulated = location.simulated;
        sample.environmentOk = environmentOk;
        sample.lightSensorOk = LOCAL_LIGHT_SENSOR;
        sample.sunlightActive = sunlightActive;
        sample.activityLevel = activityCode(activityLevel);
        sample.acclimatizationStatus = acclimatizationCode(acclimatizationStatus);
        applyDemoScenario(sample);
        attachMeshStatus(sample);
        if (ENABLE_MESH && !MESH_IS_GATEWAY) {
            String body = serializeTelemetry(sample);
            bool sent = mesh.sendBroadcast(body);
            Serial.printf("[MESH TX] device=%s sequence=%lu sent=%s\n",
                          deviceId.c_str(), static_cast<unsigned long>(sample.sequence),
                          sent ? "yes" : "no");
        } else if (telemetryQueue) {
            xQueueOverwrite(telemetryQueue, &sample);
        }
    }
    if (!ENABLE_MESH) delay(2);
}
