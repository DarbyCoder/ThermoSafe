# ThermoSafe

A clip-on sensor that watches workers for heat stress and danger, and walks you to anyone who needs help.

- **Heart rate, judged against each person's own normal.** It learns everyone's usual resting rate, allows for walking and stairs, and only flags a clear change that lasts.
- **Air temperature around each worker, judged against the usual for that kind of place.** Inside and outside each have their own average, learned from everyone's clips. Anything past hard limits always counts.
- **The clip buzzes its wearer** to take a break, cool off or warm up as soon as things start heading the wrong way.
- **Find them.** You get the floor from air pressure, the spot inside from the building's Wi-Fi, the position outside from GPS, and the last few metres from Bluetooth between clips.

## Try it

Open `index.html` in a browser, or the GitHub Pages link once Pages is on. It runs on simulated clips, so no hardware is needed. Use the **Demo** button in the top bar to set off a scenario.

1. The map opens on a satellite view of the UMass campus around the ILC, with every worker as a colored circle. The list sorts them by risk.
2. Press **Play story** (or Space) for the 30-second walkthrough. The loop icon repeats it until someone touches the page.
3. Click a worker: the map zooms in, their readings pop up, and a chart compares them with their own normal and with a fixed alarm.
4. Press **Locate**: the route pulls up, as a 3D building if they're inside or a path on the map if they're outside. "Send …" dispatches whoever is nearest.
5. **Worker's view** shows what the wearer's clip shows (I'm OK, Hold for help).
6. Tap an Activity entry to replay that moment.

Keep `campus.jpg` and `campus-hi.jpg` next to `index.html`. The live phone link and the Claude shift summary work only when the page is opened as a claude.ai Artifact. Opened directly, the summary falls back to one built from the numbers.

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
- Photoresistor (LDR) facing out, for sun intensity: 3V3 → LDR → GPIO 34 → 10k → GND. The clip sends `light` from 0 to 100, which the app reads as a rough UV index. A real UV sensor can replace it later.
- One extra ESP32 + BME280 that stays still on the ground floor as the pressure reference

## Ask Beacon (Gemini)

The **Ask** button opens a panel where a supervisor asks questions about the live crew, for example "Who has the most risk right now?" or "I can take someone off for 30 minutes. Who should I take off?"

- The first time, tap the key icon and paste a [Gemini API key](https://aistudio.google.com/apikey). It's kept only in that browser's local storage and is never committed. The model defaults to `gemini-2.5-flash` and can be changed in the same box.
- Each question goes to the Gemini API with a JSON snapshot of the crew: status, heart rate against each person's own normal, air temperature and feels-like, UV index, minutes in strong sun, time outside, location, clip battery, nearest responder and recent events.
- Gemini works from GitHub Pages or a local `index.html`. A claude.ai artifact blocks outside calls, so the panel says to open the Pages version there.

## Sun exposure

Each clip's light reading becomes a UV index (WHO bands: Low, Moderate, High, Very high). Time outside is counted from GPS. Minutes in strong sun (UV 6+) build up while outside and drain twice as fast in shade: 25 minutes buzzes the wearer to find shade and turns them yellow, and 40 minutes turns them red. Try **Demo › Dev in full sun**.

## Still to do

- **Firmware:** the clip code doesn't send air temperature or GPS yet, and doesn't drive the buzzer yet.
- **Server:** the Python server's models don't include air temperature yet. The simulator in `index.html` has the full logic to port over.
- **Thresholds:** the hard limits (95°F / 25°F air, 130 / 40 BPM at rest) are placeholders until the team sets real ones.
- **Not a medical device:** it flags readings that are unusual for that person or place, and diagnoses nothing.

## Credits

- Satellite imagery © Esri, Maxar, Earthstar Geographics (Esri World Imagery)
- Building footprints © OpenStreetMap contributors (ODbL)
- [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) (MIT)
- Fonts: Inter and Space Grotesk (Google Fonts, OFL)
- Stats: BLS, OSHA, CARES 2024 (linked in the app's About panel)
