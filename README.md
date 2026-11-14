# ThermoSafe

A clip-on sensor that watches workers for heat stress and danger, and walks you to anyone who needs help.

- **Heart rate, judged against each person's own normal.** It learns everyone's usual resting rate, allows for walking and stairs, and only flags a clear change that lasts.
- **Air temperature around each worker, judged against the usual for that kind of place.** Inside and outside each have their own average, learned from everyone's clips. Anything past hard limits always counts.
- **The clip buzzes its wearer** to take a break, cool off or warm up as soon as things start heading the wrong way.
- **Find them.** You get the floor from air pressure, the spot inside from the building's Wi-Fi, the position outside from GPS, and the last few metres from Bluetooth between clips.

## Try it

Open `index.html` in a browser, or the GitHub Pages link once Pages is on. It runs on simulated clips, so no hardware is needed. Use the **Demo** button in the top bar to set off a scenario.

1. The map opens with every worker as a colored circle, and the list sorts them by risk.
2. Click a worker: the map zooms in and their readings pop up.
3. Press **Locate**: the route pulls up, as a 3D building if they're inside or a path on the map if they're outside.

**Turn on GitHub Pages:** go to Settings › Pages, choose "Deploy from a branch," then pick `main` and the root folder.

## What's in here

| Path | What it is |
|---|---|
| `index.html` | The app and its simulator in one file. This is the current design. |
| `beacon/` | Python server for real clips (no installs, `python3 server.py --sim`), heart-rate and location models, setup page, tests. Its web UI is the earlier design. |
| `beacon/firmware/` | ESP32 clip code |

## Hardware per clip

- ESP32 dev board
- MAX30102 (heart rate; has to touch skin)
- MPU6050 (movement)
- BME280 (air pressure for the floor, plus air temperature and humidity; mount it facing out, away from the body)
- GPS module, about $10, for position outside
- Coin vibration motor and piezo buzzer
- One extra ESP32 + BME280 that stays still on the ground floor as the pressure reference

## Still to do

- **Firmware:** the clip code doesn't send air temperature or GPS yet, and doesn't drive the buzzer yet.
- **Server:** the Python server's models don't include air temperature yet. The simulator in `index.html` has the full logic to port over.
- **Thresholds:** the hard limits (95°F / 25°F air, 130 / 40 BPM at rest) are placeholders until the team sets real ones.
- **Not a medical device:** it flags readings that are unusual for that person or place, and diagnoses nothing.
