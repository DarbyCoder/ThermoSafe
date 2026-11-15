# ThermoSafe

HackUMass XIV (Fall 2026) entry. The idea is a clip-on sensor that keeps custodial and grounds workers safe from heat, cold, falls and heart strain, and sends the nearest coworker when someone is in trouble. The hardware is simulated. This repo is the supervisor dashboard plus a simulator that plays the clips' readings on a map of UMass Amherst.

It is a demo for judges. What matters most is that "Play story" and every Demo button run cleanly every time. Treat anything that could break those as high risk.

## Layout

- Everything is one file: `index.html` (about 3,000 lines, with CSS, HTML and JS inline). No build step, no dependencies, no package.json. The only outside load is Google Fonts (Urbanist, Inter).
- Assets sit beside it at the repo root: `campus.jpg`, `campus-hi.jpg` (satellite map), `logo.png`, `wordmark.png`. File names are case-sensitive on GitHub Pages.
- Keep it one file unless Nick asks to split it. The deploy and the teammates both assume it.

## Run, check, deploy

- Run locally: `python3 -m http.server 8000`, then open http://localhost:8000. Opening the file directly also works.
- There are no tests. After a change, load the page and check the browser console for errors, then run: Play story, each Demo button, Locate on one indoor and one outdoor worker, Phone View, Worker's view, and the Thermo chat. Say plainly which of these you did not check.
- Deploy is GitHub Pages: "Deploy from a branch", `main`, `/ (root)`. A push to `main` goes live within about 10 minutes. The repo is public, because Pages on a free plan requires it.

## History you need

- Until 2026-11-14 the project lived in a Claude artifact (https://claude.ai/artifact/P1asgviC4uxEymPG1DcGf4), edited by Nick and teammates at the same time. This repo was far behind it.
- Before the first edit here, confirm `index.html` is current: it should contain `GEM_DEFAULT_MODEL = "gemini-3.8-flash"`. If it doesn't, stop and ask Nick to download the artifact's five files into the repo first.
- The artifact and the repo do not sync. If Nick describes a change you can't find, a teammate probably made it in the artifact.
- A teammate edit landed in the artifact at about 9:54 PM ET on 2026-11-14 (roughly 29 lines across the Locate, smooth-motion and demo code, plus a one-line CSS or HTML change). Nobody reviewed it when this file was written. Trust the code over this file where they disagree.
- The project was first called "Beacon". Internal names still say so (localStorage keys `beacon.*`, the comment "Ask Beacon"). Do not rename the storage keys, or saved API keys are lost.

## Map of index.html

CSS is four `<style>` blocks. Later blocks override earlier ones, so make visual changes in the later block that owns the thing, never in the base block.

1. Base (no id): the original layout. It still declares old colors and fonts that `#theme` overrides.
2. `#theme`: the current look. Palette variables (off-white `#F6F5F0`, frame `#E9E7DF`, ink `#15161A`, green `#14966E`, amber `#E0A21E`, red `#E2472E`; Urbanist for display, Inter for text), Phone View, the phone-cutout Worker's view, the header beat cycle, the shake keyframes.
3. `#motion`: small-scale motion (press, color fades, enter and leave, recovery check mark), all inside `prefers-reduced-motion: no-preference`.
4. `#locate2`: the Locate screen's time-to-reach ring and route lines.
5. `#map3d`: the 3D map (`#bcanvas` building layer, `#pins` overlay, the 3D button).

JS is one `<script>`. Sections are marked with `/* ===== name ===== */` and `/* ----- name ----- */` comments; grep for those. In order:

- **the site**: `BUILDING` (the ILC: floors, stairs, doors, zones), `WORLD`, `CAMPUS` (building outlines from OpenStreetMap).
- **walking routes outdoors**: `POND`, the `NAV` grid, A* in `outPath`, plus `outLen` and `doorFor`. Routes go around buildings and the pond and enter the ILC through a door.
- **trips**: one plan per walk, shared by the walk itself, the drawn route and the time to reach.
- **models**: `HeartModel` (each person's own normal), `AirModel`, `SunModel`, `FloorTracker`, `WifiMap`.
- **hub**: `Hub` takes readings in and produces status, the activity log and alerts. `hub.snapshot(t)` is what the UI renders.
- **simulated clips**: `SimPerson`, `Sim`. Demo scenarios set targets here.
- **the app**: `renderRail`, `renderPins`, `renderBanner`, `renderPop`, `renderLocate` (`buildScene` is the 3D building for indoor workers, `buildFlat` the flat map for outdoor ones), `renderAll`, `refresh`.
- **smooth motion**: per-frame glide between simulation updates.
- **run**: `tick`, `applyFx`, `setRate`, `fastWalk`, `resolve`, `runDemo`.
- **the worker's clip**: `renderClip`, `renderClips`, and the live sync code.
- **Ask Beacon**: the Thermo chat.
- **the 60-second story**: `playStory`, `stopStory`.
- The end of the script wires events, handles `#clip` mode and starts the header beat timer.

Status levels are `ok` (Okay), `watch` (Questionable), `danger` (In danger) and `gray` (clip not reporting, shown as Questionable). Demo scenarios map to workers in `runDemo`: Rose B1, Joe B2, Mae B3, Dev B5, Ana B6.

## Decisions to keep

Nick asked for each of these. Don't undo one as a side effect of other work.

- **Removed on purpose**: the Sound button, Street View buttons, the "Scan it to wear it" QR code, the demo Speed buttons, floor-number badges on map pins (`.pin .fl { display: none }`), and the dotted trail around a selected worker (`drawTrail` now draws only when replaying a log entry). Some leftover CSS for these remains and is harmless.
- **Demo timing**: no global speed-up. An effect reaches full alert in about 3 seconds. Any Questionable or In danger worker is told to rest by their clip and is back to Okay five seconds later (2s wait, 3s recovery), with no responder needed (`autoRest`). The story holds this back until its "rest" beat and no longer plays the Locate walk. The Locate screen and the "On my way" button still work.
- **Story**: Rose Patel's info card closes when the story ends. It runs at 6 AM on the light map (`setNight(false)`); the day clock starts at 6 AM.
- **About card**: the heading is "Statistics that drive our Mission". The button is "Directions", not Google Maps, to keep people in the app.
- **Header animation**: the logo line traces at linear speed and shows only once drawn (no ghost line). Then the Okay, Questionable and In danger dots pop in turn, and the cycle repeats. It is driven by `body[data-beat]` and the `BEAT` timer, and applies to the header only.
- **Heart chart**: shows two dashed lines, the fixed alarm (180 minus age) and the worker's own alarm (red, "Her alarm N", the start of their "Too high" band: normal + 2.5 spread + 15).
- **Smooth readings**: heart rate and air temp are eased every frame in `LIVE` (`paintLive`) and drawn from that everywhere: list, banner, card numbers (`[data-live]`), bar markers (`[data-mk]`) and the chart's live end. The worker card is patched in place, not rebuilt: `rowSkel` builds a row once, `patchRow` updates it, and the chart (`#pp-plot`) is rebuilt only when a 5-second reading arrives. `paintChart` scrolls the chart with the clock and eases its vertical scale (`DISP`). The lit band on a bar is the one the reading is heading for, taken from the simulated clip's target, so a jump from Normal to Too high skips Elevated. List cards slide to their new place before they shake.
- **Status change**: when a worker becomes Questionable or In danger, their card and the matching counter shake (`shake()`), harder for danger.
- **Phone View** (`#phone-btn`, `body.phone`): wraps the app in a phone outline on black. Every `@media (max-width: 760px)` rule is duplicated under `body.phone`, so change both together. `isPhone()` covers both cases. Header buttons stay on one row, and Worker's view fills the phone.
- **Removed on request (2026-11-15)**: the header Worker button (`#clip-btn` is `hidden`, so Worker's view can't be opened from the dashboard), the Alert and On my way buttons on the map banner, the worker card and the Locate card. Elements kept hidden where code still references them.
- **3D map** (on by default, `#z-3d` toggles, saved in localStorage `beacon.map3d`): `.world` (photo, outlines, replay trail) is tilted with `perspective() rotateX(TILT)`; with the tilt at 0 the same transform is the flat map. Buildings are drawn each frame on `#bcanvas` by `drawBuildings()` from `CAMPUS` outlines, with estimated heights in `BH` and roofs cut from the photo. Pins live in `#pins` and are projected onto the ground each frame (`mapFrame`, `paintPin`), so they stay upright and on top. `camTransform()` keeps the ground filling the pane (no black), which limits zoom-out more in 3D than in 2D. Building taps in 3D go through `buildingAt()`. Walls are a shaded version of each building's average roof colour, sampled once from `campus.jpg` by `roofColors()` (needs http; from file:// the walls stay neutral gray). In 3D, Locate has no separate window: `renderLocate` shows a small card (`#locate.mini`), the camera frames you and them (`locFit`), the building they're in turns to glass with its floors drawn and theirs lit, and the route is drawn on the map from `locDom.way`. In 2D the old full Locate window is still used. While locating, the camera tilts to `TILT_LOC` (60°) and zooms in about 1.35× past a plain fit; it returns to `TILT` on close.

## Motion rules

- Animate `transform` and `opacity` only. Keep UI motion under 300ms, never use `transition: all`, and never ease-in on an entrance.
- Every animation ships with a `prefers-reduced-motion` variant.
- Nick uses Emil Kowalski's animation skills (github.com/emilkowalski/skill). If they are installed, use them for motion work.

## Thermo chat (Gemini)

- `askGemini()` posts to `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` with the key in the `x-goog-api-key` header. It sends `crewData()` as JSON under the `ASK_RULES` system prompt.
- The default model is `GEM_DEFAULT_MODEL = "gemini-3.8-flash"`. A browser can override it through localStorage `beacon.gemini.model`.
- The request sends no `temperature`, `topP` or `topK`, which Google deprecated for these models. It sends `maxOutputTokens: 1500` and `thinkingConfig: { thinkingLevel: "low" }`. On 2026-11-14 the only response seen was "This model is currently experiencing high demand", which is a server overload. No successful answer from 3.8 has been seen yet, so the `thinkingConfig` field name is still unconfirmed. If Google rejects it, delete that field.
- Errors show in the chat as "Couldn't answer: " followed by Google's message. A blocked network call shows "I can't connect from here", which is what happens inside the Claude artifact.
- Offered to Nick but not built: on an overload, retry once and then fall back to `gemini-3.7-flash`. Ask before adding it.

**Never put an API key in this repo.** The user pastes the key through the key icon in the chat, and it is saved only in that browser (localStorage `beacon.gemini.key`). The repo is public. If a key shows up in the conversation, don't write it to any file, commit or command.

## Features that only work inside the Claude artifact

Both are guarded and must keep failing quietly outside it.

- `window.claude.use("room")` gives live sync between the dashboard and a phone opened at `#clip` (the full-screen Worker's view). Elsewhere `room` is null and Worker's view runs locally only.
- `window.claude.use("sample")` writes the AI shift summary in `summarize()`. Elsewhere it falls back to a computed line.
- `ARTIFACT_URL` and `CLIP_URL` still point at the artifact.
- Live phone sync on GitHub Pages would need a real backend. That is a large change, so raise it with Nick before starting.

## Open items

- Not yet checked in a browser: the trail removal, the pin-badge removal and the Gemini 3.8 switch.
- "Remove the small yellow numbers" was read as the floor badges on pins. If Nick meant the amber battery percentage on worker cards (`.bat`), restore `.pin .fl` and hide that instead.
- The header loop is constant decorative motion. If it distracts during judging, stop it after a few cycles.

## Working with Nick

- Short, direct answers. Disagree plainly when something is a bad idea.
- If a request is ambiguous, ask before building. For larger work, confirm the core behavior first and fill in cosmetic details yourself.
- Flag risks and anything you couldn't verify without being asked.
