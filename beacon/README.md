# Beacon

Find someone inside a building, and know when their heart rate is unusual *for them*.

Everyone wears a small ESP32 badge. The app shows who needs a check, which floor they're on, and points you to them.

## Run it (no installs, Python 3.8+)

```
python3 server.py --sim
```

- App: http://localhost:8000
- Setup and demo buttons: http://localhost:8000/setup

`--sim` runs four fake badges that send exactly what the real badges send, so all the real floor, position and heart-rate code runs on them. Without `--sim`, the server waits for real badges.

To check the logic: `python3 tests/run_checks.py`

## Two-minute demo

Open the app on the projector. Keep `/setup` open on a phone or second screen; the demo buttons are at the bottom.

1. **Everyone's fine.** Joe sits around 90 bpm and shows OK. That's his normal.
2. **Press "Rose's heart races while sitting."** About 30–40 seconds later a red card appears: *Rose needs a check. Heart rate 95 while sitting still. Her normal is about 60.* Press the button before you start talking about Rose, so the alert arrives on cue.
3. **Press "Joe at 95."** Joe is now at the same number and stays OK. Same number, different verdict.
4. **Tap Find Rose.** The satellite view zooms into the building, the floors lift apart, and Rose's floor lights up. The arrow says something like *14 m to the east stairs. Then go up 1 floor to floor 2.*
5. **Tap "I'm on my way."** Everyone can see someone is coming.
6. **Press "Walk me to Rose."** Your pin walks over. Within a few metres, Bluetooth between the two badges takes over: *You're right here.*
7. **Press "Mae takes the stairs."** Her heart rate passes 110 with no alert, because climbing stairs is expected to raise it.

## How it works

**Heart rate, personal to each person** (`heart.py`)
- Learns each person's resting normal from moments when they're sitting still. It uses the median and MAD (median absolute deviation), so one-off spikes don't skew it.
- New people start from a population guess that gives way to their own data within minutes: `normal = (n·their_median + k·70) / (n + k)`.
- Adjusts for activity: `expected = normal + β·activity`. β is learned per person, and the activity signal speeds up quickly and slows down gradually, the way a real heart does.
- Alerts with CUSUM, a running sum of how far readings sit outside the expected range. A one-second spike does nothing; a sustained change alerts. Bigger changes alert faster.
- Never learns from the emergency itself. While an alert is active, the person's normal is frozen.
- A loose sensor shows "Check sensor", never a false alarm.

**Floor, from air pressure** (`locate.py`)
- Each badge has a BMP280 pressure sensor. One reference badge sits still on the ground floor.
- Weather shifts both readings by the same amount, so the difference between them is pure height. Height ÷ floor height gives the floor.
- No GPS. GPS height is off by 10 m or more indoors, which is several floors.
- The person's own height doesn't matter. Zeroing all badges together absorbs where each badge is clipped.

**Position on the floor, from the building's own Wi-Fi**
- No beacons to install. Each badge scans the building's Wi-Fi access points.
- During setup you walk around once and tap where you are on a floor plan, recording the Wi-Fi pattern at each spot (a "fingerprint").
- Live, the app finds the 3 recorded spots whose pattern is most similar (k-nearest neighbors).

**The last few metres, badge to badge**
- Badges hear each other over Bluetooth. Signal strength gives a rough distance (`d = 10^((−59 − RSSI) / 22)`), which is more accurate than Wi-Fi once you're close.

**Results in simulation** (`tests/run_checks.py`)

| Check | Result |
|---|---|
| Floor correct | 99.8% |
| Position error | median 3.9 m, 9 in 10 within 7.9 m |
| Rose alerts after her heart starts racing | 31 s |
| Joe at 95 flagged | never |
| Mae on the stairs flagged | never |

These are simulated sensors. Measure on the real badges before quoting numbers to judges.

## Real badges

**Parts per badge:** an ESP32 dev board, MAX30102 (heart), MPU6050 (movement) and BMP280 (pressure). If you have a BME280, swap in the Adafruit BME280 library; it's the same idea.

**Reference badge:** one extra ESP32 + BMP280 that stays still on the ground floor.

**Wiring:** all sensors share I2C. SDA → GPIO 21, SCL → GPIO 22, 3V3, GND.

1. In Arduino, install: SparkFun MAX3010x, Adafruit BMP280, Adafruit MPU6050, NimBLE-Arduino 2.x.
2. Open `firmware/beacon_badge/beacon_badge.ino` and edit the top: hotspot name and password, `SERVER` (server.py prints it), and `BADGE_ID`. For the reference badge, set `IS_REFERENCE 1`.
3. Put the laptop and all badges on the same **phone hotspot**. Logging an ESP32 into eduroam costs hours.
4. Run `python3 server.py` without `--sim`, then open `/setup`. Badges appear within a few seconds.
5. **Zero:** put all badges next to the reference badge and press "Zero all badges."
6. **Check each floor** (optional): carry one badge to each floor and save.
7. **Map the Wi-Fi:** 15–20 spots per floor. The red ring on the floor plan shows where the app currently thinks that badge is, so you can check accuracy as you go.
8. **Learning:** wear the badges sitting still for 5 minutes. People show "Learning" until the app knows their normal.

**The heart sensor must touch skin:** fingertip clip, earlobe, or a strap on the wrist. A badge clipped to clothing gets no heart reading.

**Phone compass:** browsers only allow the compass over https. Run `cloudflared tunnel --url http://localhost:8000` (or ngrok) and open the https link on the phone. Without that, the arrow follows the on-screen map, which works fine on a laptop.

## Edit for your building

- `config/building.json`: centre coordinates, size, floors, floor height, stairs, named spots.
  - The ILC coordinates are approximate. Right-click the building in Google Maps, copy its exact coordinates, and paste them in so the satellite photo lines up.
  - The room layout is made up. Move the zones to roughly where those rooms are.
- `config/people.json`: names, badge IDs, pronouns, phone number (shows a Call button), and `"caregiver": true` for the person using the app.

## Files

| File | What it does |
|---|---|
| `server.py` | Web server, live stream to the app, setup endpoints |
| `hub.py` | Badge state, setup actions, plain-language sentences |
| `heart.py` | Personal heart-rate model |
| `locate.py` | Pressure → floor, Wi-Fi fingerprints → position, Bluetooth → distance |
| `sim.py` | Fake badges and demo scenarios |
| `web/` | The app (`index.html`), setup page (`setup.html`) |
| `firmware/` | ESP32 badge code |

## Be upfront with judges about

- **Not a medical device.** It flags "unusual for this person" and never diagnoses anything.
- **Privacy.** This demo shows everyone's location. In a real product, a person's location stays hidden until an alert fires or they press for help, and they can see who looked.
- **No login yet.** Anyone on the same network can open the app. Don't use it with real patient data.
- **The firmware wasn't compiled here.** Expect small fixes on the first compile, most likely around the NimBLE 2.x calls.
- **Satellite imagery** comes from Esri World Imagery and needs internet. Without it, the building shows on a plain grid.
