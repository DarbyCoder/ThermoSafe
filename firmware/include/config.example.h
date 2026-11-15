#pragma once

// Copy to config.h. That file is ignored by git and excluded from handoff ZIPs.
constexpr char WIFI_SSID[] = "YOUR_WIFI_NAME";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
constexpr char API_BASE_URL[] = "http://192.168.1.100:8000";
constexpr char DEVICE_API_KEY[] = "replace-with-the-device-key-from-env";

// Every board shares mesh credentials. Set MESH_IS_GATEWAY=true on one board only.
constexpr bool ENABLE_MESH = true;
#ifndef THERMOSAFE_MESH_GATEWAY
#define THERMOSAFE_MESH_GATEWAY 0
#endif
constexpr bool MESH_IS_GATEWAY = THERMOSAFE_MESH_GATEWAY;
constexpr char MESH_PREFIX[] = "HeatSentinelMesh";
constexpr char MESH_PASSWORD[] = "replace-with-a-shared-mesh-password";
constexpr uint16_t MESH_PORT = 5555;

// Empty uses a unique ID derived from the ESP32 MAC address.
constexpr char DEVICE_ID[] = "";
constexpr int DHT_PIN = 4;
constexpr int LIGHT_PIN = 32; // ADC1: photoresistor voltage divider output.
constexpr bool ENABLE_LIGHT_SENSOR = true;
constexpr bool LIGHT_BRIGHT_IS_HIGHER = true; // Set false if ADC drops as light gets brighter.
constexpr int LIGHT_DARK_ORIENTED_RAW = 3200; // Upward-facing room baseline for this demo divider.
constexpr int LIGHT_BRIGHT_ORIENTED_RAW = 4000; // Direct flashlight target for near-100% brightness.
constexpr float SUNLIGHT_THRESHOLD_PERCENT = 35.0f; // Sensitive relative-brightness demo threshold, not UV.
constexpr float SUNLIGHT_THRESHOLD_HYSTERESIS_PERCENT = 5.0f;
// Optional demo context; unknown means this factor is not available.
#define WORKER_ACTIVITY_LEVEL "unknown" // light, moderate, heavy, or unknown
#define WORKER_ACCLIMATIZATION_STATUS "unknown" // acclimatized, not_acclimatized, or unknown
constexpr int PULSE_PIN = 34; // ADC1: remains available while Wi-Fi is active.
constexpr int BUZZER_PIN = 2;
constexpr int LED_R_PIN = 27;
constexpr int LED_G_PIN = 26;
constexpr int LED_B_PIN = 25;

constexpr bool ENABLE_RGB_LED = true;
constexpr bool RGB_COMMON_ANODE = false; // Set false for a common-ground RGB module.
constexpr bool ENABLE_BUZZER = true;
constexpr bool BUZZER_ACTIVE_MODULE = true; // Keyes buzzer module is active/self-oscillating.
constexpr bool BUZZER_ACTIVE_HIGH = true;
constexpr int BUZZER_FREQUENCY_HZ = 2200;

// A basic optical module needs a real contact/motion evaluation before enabling BPM.
// Raw ADC capture can be enabled independently. Pulse never changes the heat state.
constexpr bool ENABLE_PULSE_SENSOR = false;
constexpr bool ENABLE_EXPERIMENTAL_BPM = false;
constexpr bool ENABLE_SIMULATED_BPM = true; // Presentation-only BPM; telemetry labels it simulated and risk ignores it.
constexpr bool ENABLE_SIMULATED_LOCATION = true; // Map demo only; coordinates are not GPS measurements.
constexpr double DEMO_LOCATION_CENTER_LATITUDE = 42.3912; // Set to the demonstration site's map center.
constexpr double DEMO_LOCATION_CENTER_LONGITUDE = -72.5262;
constexpr bool PULSE_INVERT = false;
constexpr int PULSE_CONTACT_MIN_RAW = 100;
constexpr float PULSE_MIN_AMPLITUDE = 1.0f;

constexpr uint32_t SAMPLE_INTERVAL_MS = 2000; // DHT11: avoid rapid repeated reads.
constexpr uint32_t STATUS_INTERVAL_MS = 1000; // Refresh local serial status once per second.
constexpr uint32_t TELEMETRY_INTERVAL_MS = 1000; // Send brightness and cumulative exposure every second.
constexpr uint32_t SENSOR_STALE_MS = 8000;
constexpr uint32_t WIFI_RETRY_MS = 10000;
