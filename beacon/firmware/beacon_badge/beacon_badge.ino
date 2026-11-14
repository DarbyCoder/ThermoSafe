// Beacon badge: ESP32 + MAX30102 (heart) + MPU6050 (movement) + BMP280 (air pressure)
//
// Arduino Library Manager:
//   SparkFun MAX3010x Pulse and Proximity Sensor Library
//   Adafruit BMP280 Library
//   Adafruit MPU6050           (pulls in Adafruit Unified Sensor + BusIO)
//   NimBLE-Arduino             (version 2.x)
// Board: "ESP32 Dev Module". If it doesn't fit: Tools > Partition Scheme > Huge APP.
//
// Wiring: all three sensors share I2C. SDA -> GPIO 21, SCL -> GPIO 22, VIN -> 3V3, GND -> GND.
// The MAX30102 has to touch skin (fingertip clip, earlobe, or strapped to the wrist).
// Clipped to clothing it reads nothing, and the app will say "Check sensor".
//
// Sun: a photoresistor (LDR) facing out. 3V3 -> LDR -> GPIO 34, and GPIO 34 -> 10k -> GND.
// Brighter light reads higher. The badge sends it as "light" from 0 (dark) to 100 (full sun),
// and the app reads that as a rough UV index (light / 100 * 11). A real UV sensor can replace it later.
//
// Every second the badge POSTs this to the server:
//   {"id":"B1","hr":72,"q":0.9,"act":0.12,"p":1003.421,"light":64,
//    "wifi":[["aa:bb:cc:dd:ee:ff",-61],...],"ble":[["B2",-70],...]}
// The reference badge (IS_REFERENCE 1) sits still on the ground floor and only sends
//   {"id":"REF","ref":true,"p":1003.9}

#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <math.h>
#include "MAX30105.h"
#include "heartRate.h"
#include <Adafruit_BMP280.h>
#include <Adafruit_MPU6050.h>
#include <NimBLEDevice.h>

// ---------------- change these ----------------
const char* WIFI_SSID = "your-hotspot";   // a phone hotspot is easiest; eduroam login is painful on ESP32
const char* WIFI_PASS = "your-password";
const char* SERVER    = "http://192.168.1.20:8000/api/ingest";  // printed when server.py starts
const char* BADGE_ID  = "B1";             // must match config/people.json
#define IS_REFERENCE 0                    // 1 for the one badge that stays on the ground floor
#define USE_BLE 1                         // set 0 if Bluetooth + Wi-Fi together is unstable
const char* SCAN_ONLY = "eduroam";        // only map the building's own Wi-Fi ("" = all networks)
#define LIGHT_PIN 34                      // photoresistor divider (input-only ADC pin); -1 if not fitted
// ----------------------------------------------

MAX30105 heart;
Adafruit_BMP280 bmp;
Adafruit_MPU6050 mpu;
bool haveHeart = false, haveBaro = false, haveMotion = false;

volatile float hrNow = 0, quality = 0, actNow = 0, pressureNow = 0;
volatile float lightNow = -1;             // 0..100, -1 until the first reading
SemaphoreHandle_t lock;

// heart: average the last 4 beats
const int RATE_SIZE = 4;
float rates[RATE_SIZE];
int rateSpot = 0, rateCount = 0;
unsigned long lastBeat = 0;

// movement: how much acceleration varies over ~1 second
const int ACC_N = 50;
float accBuf[ACC_N];
int accI = 0, accCount = 0;

// latest Wi-Fi scan
struct Ap { char mac[18]; int rssi; };
Ap aps[15];
int apCount = 0;
bool scanFresh = false;

// other badges heard over Bluetooth
struct Peer { char id[12]; float rssi; unsigned long t; };
Peer peers[12];
int peerCount = 0;

#if USE_BLE
class ScanCB : public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice* dev) override {
    std::string n = dev->getName();
    if (n.rfind("BCN-", 0) != 0) return;
    std::string id = n.substr(4);
    int rssi = dev->getRSSI();
    xSemaphoreTake(lock, portMAX_DELAY);
    int k = -1;
    for (int i = 0; i < peerCount; i++) if (id == peers[i].id) { k = i; break; }
    if (k < 0 && peerCount < 12) {
      k = peerCount++;
      strncpy(peers[k].id, id.c_str(), 11);
      peers[k].id[11] = 0;
      peers[k].rssi = rssi;
    }
    if (k >= 0) {
      peers[k].rssi += 0.3f * (rssi - peers[k].rssi);
      peers[k].t = millis();
    }
    xSemaphoreGive(lock);
  }
} scanCB;
#endif

void netTask(void*);

void setup() {
  Serial.begin(115200);
  lock = xSemaphoreCreateMutex();
  Wire.begin(21, 22);
  Wire.setClock(400000);
#if LIGHT_PIN >= 0
  analogReadResolution(12);                       // 0..4095
  analogSetPinAttenuation(LIGHT_PIN, ADC_11db);   // full 0..3.3 V range
#endif

  haveBaro = bmp.begin(0x76) || bmp.begin(0x77);
  if (haveBaro) {
    bmp.setSampling(Adafruit_BMP280::MODE_NORMAL, Adafruit_BMP280::SAMPLING_X2,
                    Adafruit_BMP280::SAMPLING_X16, Adafruit_BMP280::FILTER_X16,
                    Adafruit_BMP280::STANDBY_MS_63);
  }
  if (!IS_REFERENCE) {
    haveHeart = heart.begin(Wire, I2C_SPEED_FAST);
    if (haveHeart) {
      heart.setup();
      heart.setPulseAmplitudeRed(0x0A);
      heart.setPulseAmplitudeGreen(0);
    }
    haveMotion = mpu.begin();
    if (haveMotion) mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
  }
  Serial.printf("pressure %d  heart %d  motion %d\n", haveBaro, haveHeart, haveMotion);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

#if USE_BLE
  if (!IS_REFERENCE) {
    String name = String("BCN-") + BADGE_ID;
    NimBLEDevice::init(name.c_str());
    NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
    adv->setName(name.c_str());
    adv->start();
    NimBLEScan* scan = NimBLEDevice::getScan();
    scan->setScanCallbacks(&scanCB, true);   // true: report every advert, not just the first
    scan->setActiveScan(false);
    scan->setInterval(160);
    scan->setWindow(60);
    scan->start(0, false, true);             // 0 = scan forever
  }
#endif

  xTaskCreatePinnedToCore(netTask, "net", 8192, nullptr, 1, nullptr, 0);
}

// Sensors run here on core 1 so Wi-Fi never interrupts beat detection.
unsigned long lastAcc = 0, lastBaro = 0, lastLight = 0;

void loop() {
  unsigned long now = millis();

  if (haveHeart) {
    long ir = heart.getIR();
    if (ir < 50000) {                 // not touching skin
      quality = 0;
      rateCount = 0;
    } else if (checkForBeat(ir)) {
      unsigned long d = now - lastBeat;
      lastBeat = now;
      float bpm = 60000.0f / d;
      if (bpm > 30 && bpm < 220) {
        rates[rateSpot] = bpm;
        rateSpot = (rateSpot + 1) % RATE_SIZE;
        if (rateCount < RATE_SIZE) rateCount++;
        float s = 0;
        for (int i = 0; i < rateCount; i++) s += rates[i];
        hrNow = s / rateCount;
        quality = rateCount >= 3 ? 0.9f : 0.6f;
      }
    }
    if (ir >= 50000 && now - lastBeat > 4000) quality = 0.3f;   // touching, but no clean beats
  } else {
    delay(5);
  }

  if (haveMotion && now - lastAcc >= 20) {
    lastAcc = now;
    sensors_event_t a, g, t;
    mpu.getEvent(&a, &g, &t);
    float m = sqrtf(a.acceleration.x * a.acceleration.x + a.acceleration.y * a.acceleration.y +
                    a.acceleration.z * a.acceleration.z) / 9.80665f;
    accBuf[accI] = m;
    accI = (accI + 1) % ACC_N;
    if (accCount < ACC_N) accCount++;
    float mean = 0, var = 0;
    for (int i = 0; i < accCount; i++) mean += accBuf[i];
    mean /= accCount;
    for (int i = 0; i < accCount; i++) var += (accBuf[i] - mean) * (accBuf[i] - mean);
    // ~0 sitting, ~1 walking, ~1.5+ stairs. Tune 0.20 if walking reads too high or low.
    float act = sqrtf(var / accCount) / 0.20f;
    if (act > 3) act = 3;
    actNow += 0.2f * (act - actNow);
  }

  if (haveBaro && now - lastBaro >= 250) {
    lastBaro = now;
    pressureNow = bmp.readPressure() / 100.0f;   // Pa -> hPa
  }

#if LIGHT_PIN >= 0
  if (now - lastLight >= 200) {
    lastLight = now;
    float pct = analogRead(LIGHT_PIN) * 100.0f / 4095.0f;
    lightNow = lightNow < 0 ? pct : lightNow + 0.2f * (pct - lightNow);   // smooth out flicker and shadows
  }
#endif
}

void keepScan(int n) {
  String mine = WiFi.BSSIDstr();
  int idx[64], c = 0;
  for (int i = 0; i < n && c < 64; i++) {
    String ssid = WiFi.SSID(i);
    if (ssid == WIFI_SSID || WiFi.BSSIDstr(i) == mine) continue;   // never map the hotspot itself
    if (strlen(SCAN_ONLY) && !ssid.startsWith(SCAN_ONLY)) continue;
    idx[c++] = i;
  }
  for (int i = 0; i < c; i++)                // strongest first
    for (int j = i + 1; j < c; j++)
      if (WiFi.RSSI(idx[j]) > WiFi.RSSI(idx[i])) { int t = idx[i]; idx[i] = idx[j]; idx[j] = t; }
  xSemaphoreTake(lock, portMAX_DELAY);
  apCount = c < 15 ? c : 15;
  for (int i = 0; i < apCount; i++) {
    strncpy(aps[i].mac, WiFi.BSSIDstr(idx[i]).c_str(), 17);
    aps[i].mac[17] = 0;
    aps[i].rssi = WiFi.RSSI(idx[i]);
  }
  scanFresh = true;
  xSemaphoreGive(lock);
}

void sendPacket() {
  String b = "{\"id\":\"";
  b += IS_REFERENCE ? "REF" : BADGE_ID;
  b += "\"";
  if (IS_REFERENCE) b += ",\"ref\":true";
  if (haveBaro) { b += ",\"p\":"; b += String(pressureNow, 3); }
  if (!IS_REFERENCE) {
    if (haveHeart && quality >= 0.5f) { b += ",\"hr\":"; b += String(hrNow, 0); }
    b += ",\"q\":"; b += String(haveHeart ? (float)quality : 0.0f, 2);
    b += ",\"act\":"; b += String((float)actNow, 2);
    if (lightNow >= 0) { b += ",\"light\":"; b += String((float)lightNow, 0); }
    xSemaphoreTake(lock, portMAX_DELAY);
    if (scanFresh) {
      b += ",\"wifi\":[";
      for (int i = 0; i < apCount; i++) {
        if (i) b += ",";
        b += "[\""; b += aps[i].mac; b += "\","; b += aps[i].rssi; b += "]";
      }
      b += "]";
      scanFresh = false;
    }
    b += ",\"ble\":[";
    bool first = true;
    for (int i = 0; i < peerCount; i++) {
      if (millis() - peers[i].t > 3000) continue;
      if (!first) b += ",";
      first = false;
      b += "[\""; b += peers[i].id; b += "\","; b += (int)roundf(peers[i].rssi); b += "]";
    }
    b += "]";
    xSemaphoreGive(lock);
  }
  b += "}";

  HTTPClient http;
  http.setTimeout(1500);
  http.begin(SERVER);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(b);
  http.end();
  if (code != 200) Serial.printf("send failed (%d). Is SERVER right and the laptop on the same Wi-Fi?\n", code);
}

// Wi-Fi, scanning and sending run on core 0.
void netTask(void*) {
  unsigned long lastSend = 0, lastScan = 0;
  bool scanning = false;
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      vTaskDelay(500 / portTICK_PERIOD_MS);
      continue;
    }
    unsigned long now = millis();
    if (!IS_REFERENCE) {
      if (!scanning && now - lastScan > 5000) {
        WiFi.scanNetworks(true, false, false, 120);   // async, ~1.5 s
        scanning = true;
        lastScan = now;
      }
      if (scanning) {
        int n = WiFi.scanComplete();
        if (n >= 0) { keepScan(n); WiFi.scanDelete(); scanning = false; }
        else if (n == WIFI_SCAN_FAILED) scanning = false;
      }
    }
    if (now - lastSend >= 1000) {
      lastSend = now;
      sendPacket();
    }
    vTaskDelay(20 / portTICK_PERIOD_MS);
  }
}
