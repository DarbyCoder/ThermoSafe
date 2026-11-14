"use strict";

const COLORS = {
  ok: "var(--ok)", watch: "var(--watch)", alert: "var(--alert)",
  sensor: "var(--muted)", offline: "var(--muted)", learning: "var(--muted)",
};
const TILT_X = 55, TURN_Z = -28;   // must match .scene in style.css
const ARROW = "M35 4 L62 60 L35 47 L8 60 Z";
const CHECK = "M14 36 L29 51 L57 20";

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const app = document.getElementById("app");
const sheet = document.getElementById("sheet");

let state = null;
let building = { floors: [1], width_m: 90, depth_m: 50 };
let meId = "";
let live = false;
let view = { name: "list", id: null };
let built = null;
let heading = null;
let compass = false;
let lastAngle = 0;
let seenAlerts = new Set();

const person = (id) => state && state.people.find((p) => p.id === id);
const mePerson = () => (meId ? person(meId) : null);
const objPronoun = (p) => ({ her: "her", his: "him" }[p.pronoun] || "them");

async function start() {
  try { building = await (await fetch("/api/building")).json(); } catch (e) { /* keep defaults */ }
  const saved = store.get("beacon.me");
  meId = saved !== null ? saved : building.default_me || "";
  window.addEventListener("hashchange", route);
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => { built = null; render(); }, 200); });
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !sheet.hidden) closePicker(); });
  sheet.addEventListener("click", (e) => { if (e.target === sheet) closePicker(); });
  connect();
  route();
}

function connect() {
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => { state = JSON.parse(e.data); live = true; render(); notify(); };
  es.onerror = () => { live = false; render(); };
}

function route() {
  const m = location.hash.match(/^#\/p\/(.+)$/);
  view = m ? { name: "find", id: decodeURIComponent(m[1]) } : { name: "list" };
  built = null;
  window.scrollTo(0, 0);
  render();
}

function render() {
  if (!state) {
    app.innerHTML = `<div class="wrap"><header class="top">${brand()}</header><p class="empty">Connecting to the badges.</p></div>`;
    return;
  }
  if (view.name === "find") renderFind(); else renderList();
}

function notify() {
  const now = new Set(state.people.filter((p) => p.status === "alert").map((p) => p.id));
  for (const id of now) {
    if (!seenAlerts.has(id) && navigator.vibrate) { try { navigator.vibrate([300, 120, 300]); } catch (e) { /* ignore */ } }
  }
  seenAlerts = now;
  const first = state.people.find((p) => p.status === "alert");
  document.title = first ? `${first.title} – Beacon` : "Beacon";
}

/* ---------- shared bits ---------- */

function brand() {
  return `<div class="brand"><svg viewBox="0 0 26 26" aria-hidden="true">
    <circle cx="13" cy="17" r="4" fill="var(--alert)"/>
    <path d="M6.5 13.5a7 7 0 0 1 13 0" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>
    <path d="M2.5 11a11.5 11.5 0 0 1 21 0" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" opacity=".45"/>
  </svg>Beacon</div>`;
}

function meButton() {
  const m = mePerson();
  return `<button class="me-btn" data-act="pick">${m ? "You: " + esc(m.name) : "Choose your badge"}</button>`;
}

function onWay(p) {
  if (!p.acked_by) return "";
  const m = mePerson();
  return m && p.acked_by === m.name ? "You're on the way." : `${esc(p.acked_by)} is on the way.`;
}

/* ---------- screen 1: everyone ---------- */

function renderList() {
  const ppl = state.people.filter((p) => p.id !== meId || p.status === "alert");
  const alerts = ppl.filter((p) => p.status === "alert");
  const others = ppl.filter((p) => p.status !== "alert");
  let h = `<div class="wrap"><header class="top">${brand()}${meButton()}</header>`;
  for (const a of alerts) {
    h += `<section class="alert" role="alert">
      <h2>${esc(a.title)}</h2>
      <p>${esc(a.sentence)}</p>
      <p class="where">${esc(a.place)}</p>
      <button class="btn-light" data-go="${esc(a.id)}">Find ${esc(a.name)}</button>
      ${a.acked_by ? `<p class="otw">${onWay(a)}</p>` : ""}
    </section>`;
  }
  if (alerts.length && others.length) h += `<h2 class="sub">Everyone else</h2>`;
  if (!state.people.length) {
    h += `<p class="empty">No badges are sending yet. Turn a badge on and it shows up here within a few seconds.</p>`;
  }
  h += others.map(row).join("");
  h += `<div class="foot"><span class="live-dot ${live ? "" : "off"}"></span>${live ? "Live" : "Reconnecting"}${state.sim ? ". Showing demo badges." : ""}</div></div>`;
  app.innerHTML = h;
}

function row(p) {
  return `<button class="row" data-go="${esc(p.id)}" style="--c:${COLORS[p.status]}">
    <span class="bar"></span>
    <span>
      <span class="name">${esc(p.name)}</span>${p.status !== "ok" ? `<span class="tag">${esc(p.status_word)}</span>` : ""}
      <span class="sent">${esc(p.sentence)}</span>
      <span class="place">${esc(p.place)}</span>
    </span>
    ${p.hr ? `<span class="hr">${p.hr}<small>bpm</small></span>` : `<span class="hr none">–</span>`}
  </button>`;
}

/* ---------- screen 2: find one person ---------- */

function renderFind() {
  const p = person(view.id);
  if (!p) {
    built = null;
    app.innerHTML = `<div class="wrap find"><header class="top"><button class="back" data-act="back">‹ Everyone</button></header>
      <p class="empty">That badge isn't sending right now.</p></div>`;
    return;
  }
  if (!built || built.id !== p.id) buildFind(p);
  updateFind(p);
}

function buildFind(p) {
  app.innerHTML = `<div class="wrap find">
    <div class="col-left">
      <header class="top"><button class="back" data-act="back">‹ Everyone</button>${meButton()}</header>
      <div class="who"><h1>${esc(p.name)}</h1><div class="hr" id="f-hr"></div></div>
      <p class="status-line" id="f-status"></p>
      <div class="dir" id="f-dir">
        <div class="arrow-wrap" id="f-aw"><svg class="arrow" id="f-arrow" viewBox="0 0 70 70" aria-hidden="true"><path id="f-ap" d="${ARROW}" fill="currentColor"/></svg></div>
        <div><div class="big" id="f-big"></div><div class="then" id="f-then"></div><div class="hint" id="f-hint"></div></div>
      </div>
    </div>
    <div class="col-right">
      <div class="bldg" id="f-bldg"></div>
      <p class="bldg-caption" id="f-cap"></p>
    </div>
    <div class="col-left">
      <section class="chart" id="f-chart"></section>
      <div class="actions" id="f-actions"></div>
    </div>
  </div>`;
  built = { id: p.id, scene: buildScene(document.getElementById("f-bldg"), p) };
}

function updateFind(p) {
  const $ = (id) => document.getElementById(id);
  $("f-hr").innerHTML = p.hr ? `${p.hr}<small>bpm</small>` : "";
  const st = $("f-status");
  st.style.setProperty("--c", COLORS[p.status]);
  st.innerHTML = `<span class="dot"></span><span><b>${esc(p.status_word)}.</b> ${esc(p.sentence)} ${p.acked_by ? `<span class="otw-line">${onWay(p)}</span>` : ""}</span>`;

  drawDirection(p);
  updateScene(p);
  $("f-cap").textContent = p.place;
  $("f-chart").innerHTML = chartHtml(p);
  const acts = actionsHtml(p);
  const box = $("f-actions");
  if (box.dataset.h !== acts) { box.innerHTML = acts; box.dataset.h = acts; }
  box.hidden = !acts;
}

/* direction: arrow + plain words */

function nearestStairs(m) {
  const list = building.stairs && building.stairs.length ? building.stairs : [{ x: building.width_m / 2, y: building.depth_m / 2, name: "stairs" }];
  return list.reduce((a, b) => (Math.hypot(b.x - m.x, b.y - m.y) < Math.hypot(a.x - m.x, a.y - m.y) ? b : a));
}

function screenAngle(dx, dy) {
  if (compass && heading !== null) {
    const bearing = (Math.atan2(dx, dy) * 180) / Math.PI + (building.north_offset_deg || 0);
    return bearing - heading;
  }
  // same rotation the 3D view uses, so the arrow matches the picture
  const a = (TURN_Z * Math.PI) / 180;
  const vx = dx, vy = -dy;
  const x1 = vx * Math.cos(a) - vy * Math.sin(a);
  const y1 = (vx * Math.sin(a) + vy * Math.cos(a)) * Math.cos((TILT_X * Math.PI) / 180);
  return (Math.atan2(x1, -y1) * 180) / Math.PI;
}

function direction(p) {
  const m = mePerson();
  if (!meId) return { plain: true, big: "Where are you?", then: "Choose the badge you're wearing to get directions.", hint: `<button class="link-btn" data-act="pick">Choose my badge</button>` };
  if (p.id === meId) return { plain: true, big: "This is your badge.", then: "" };
  if (!m || m.status === "offline" || m.floor == null) return { plain: true, big: "Directions need your badge.", then: "Your badge isn't sending a location right now." };
  if (p.floor == null) return { plain: true, big: "Floor unknown", then: `${p.name}'s badge isn't sending a location right now.` };

  const diff = p.floor - m.floor;
  const peer = m.peers ? m.peers[p.id] : undefined;
  if (diff === 0 && peer !== undefined && peer < 4) {
    return { arrow: "here", big: "You're right here", then: `${p.name} is about ${Math.max(1, Math.round(peer))} m from you.` };
  }
  if (m.x == null || p.x == null) {
    return { plain: true, big: `Floor ${p.floor}`, then: diff ? `${Math.abs(diff)} floor${Math.abs(diff) > 1 ? "s" : ""} ${diff > 0 ? "up" : "down"} from you. Exact spot unknown.` : "Your floor. Exact spot unknown." };
  }
  if (diff !== 0) {
    const s = nearestStairs(m);
    const d = Math.round(Math.hypot(s.x - m.x, s.y - m.y));
    return {
      arrow: screenAngle(s.x - m.x, s.y - m.y),
      big: d <= 3 ? `Take the ${s.name}` : `${d} m to the ${s.name}`,
      then: `Then go ${diff > 0 ? "up" : "down"} ${Math.abs(diff)} floor${Math.abs(diff) > 1 ? "s" : ""} to floor ${p.floor}.`,
    };
  }
  const close = peer !== undefined && peer < 10;
  const d = close ? peer : Math.hypot(p.x - m.x, p.y - m.y);
  const tail = p.place.replace(/^Floor \d+,?\s*/, "");
  return {
    arrow: screenAngle(p.x - m.x, p.y - m.y),
    big: `${Math.max(1, Math.round(d))} m away`,
    then: close ? "You're close. Look around you." : `On your floor${tail ? ", " + tail : ""}.`,
  };
}

function compassAvailable() {
  return window.isSecureContext && "DeviceOrientationEvent" in window && /Android|iPhone|iPad|Mobi/i.test(navigator.userAgent);
}

function drawDirection(p) {
  const d = direction(p);
  const box = document.getElementById("f-dir");
  if (!box) return;
  box.classList.toggle("plain", !!d.plain);
  document.getElementById("f-aw").hidden = !!d.plain;
  document.getElementById("f-big").textContent = d.big;
  document.getElementById("f-then").textContent = d.then;
  let hint = d.hint || "";
  if (!d.plain && d.arrow !== "here") {
    hint = compass ? "Hold your phone flat and follow the arrow."
      : "The arrow matches the map." + (compassAvailable() ? ` <button class="link-btn" data-act="compass">Use my compass</button>` : "");
  }
  const h = document.getElementById("f-hint");
  if (h.dataset.h !== hint) { h.innerHTML = hint; h.dataset.h = hint; }

  const svg = document.getElementById("f-arrow");
  const path = document.getElementById("f-ap");
  if (d.arrow === "here") {
    svg.classList.add("here");
    path.setAttribute("d", CHECK);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "8");
    svg.style.transform = "none";
  } else if (typeof d.arrow === "number") {
    svg.classList.remove("here");
    path.setAttribute("d", ARROW);
    path.setAttribute("fill", "currentColor");
    path.removeAttribute("stroke");
    lastAngle += ((((d.arrow - lastAngle) % 360) + 540) % 360) - 180;
    svg.style.transform = `rotate(${lastAngle}deg)`;
  }
}

async function enableCompass() {
  try {
    if (typeof DeviceOrientationEvent.requestPermission === "function") {
      if ((await DeviceOrientationEvent.requestPermission()) !== "granted") return;
    }
  } catch (e) { return; }
  const onTurn = (e) => {
    let h = null;
    if (typeof e.webkitCompassHeading === "number") h = e.webkitCompassHeading;
    else if (e.absolute && typeof e.alpha === "number") h = 360 - e.alpha;
    if (h === null) return;
    heading = h;
    compass = true;
    const p = view.name === "find" && person(view.id);
    if (p && built) drawDirection(p);
  };
  window.addEventListener("deviceorientationabsolute", onTurn);
  window.addEventListener("deviceorientation", onTurn);
}

/* the building, standing on the satellite photo */

function buildScene(el, p) {
  const W = building.width_m, D = building.depth_m, floors = building.floors || [1];
  const cw = el.clientWidth, ch = el.clientHeight;
  const ppm = Math.min((cw * 0.64) / W, (ch * 0.95) / D);
  const fw = W * ppm, fd = D * ppm;
  const gap = Math.max(34, Math.min(ch * 0.13, 72));
  const topPct = 50 + Math.min(16, (((floors.length - 1) * gap * 0.82) / ch) * 50);

  const scene = document.createElement("div");
  scene.className = "scene flat";
  scene.style.top = `${topPct}%`;
  scene.appendChild(satellite(ppm));

  const fl = {};
  const level = {};
  floors.forEach((n, i) => {
    const f = document.createElement("div");
    f.className = "floor";
    f.style.cssText = `width:${fw}px;height:${fd}px;left:${-fw / 2}px;top:${-fd / 2}px;transform:translateZ(${i * gap}px)`;
    scene.appendChild(f);
    fl[n] = f;
    level[n] = i;
  });

  el.innerHTML = "";
  el.appendChild(scene);
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  el.appendChild(overlay);
  if (building.lat != null) el.insertAdjacentHTML("beforeend", `<span class="credit">Imagery: Esri, Maxar, Earthstar Geographics</span>`);
  requestAnimationFrame(() => requestAnimationFrame(() => scene.classList.remove("flat")));
  setTimeout(() => overlay.classList.add("shown"), matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 950);

  const s = { scene, fl, level, W, D, fw, fd, gap, cw, ch, sy: (ch * topPct) / 100, overlay, nums: {} };
  for (const n of floors) {
    // floor numbers live in the flat overlay; upright 3D labels make browsers slice the floors
    const pt = project(s, 0, 0, n);
    const lab = document.createElement("div");
    lab.className = "floor-num";
    lab.textContent = n;
    lab.style.left = `${pt.left}px`;
    lab.style.top = `${pt.top}px`;
    overlay.appendChild(lab);
    s.nums[n] = lab;
  }
  s.pin = makePin(s, p.name.slice(0, 1), "");
  s.you = makePin(s, "You", "you");
  return s;
}

// where a point on a floor lands on screen (same maths as the CSS transform)
function project(s, x, y, floor) {
  const px = -s.fw / 2 + (x / s.W) * s.fw;
  const py = -s.fd / 2 + (1 - y / s.D) * s.fd;
  const pz = (s.level[floor] || 0) * s.gap;
  const a = (TURN_Z * Math.PI) / 180, b = (TILT_X * Math.PI) / 180, P = 1200;
  const x1 = px * Math.cos(a) - py * Math.sin(a);
  const y1 = px * Math.sin(a) + py * Math.cos(a);
  const y2 = y1 * Math.cos(b) - pz * Math.sin(b);
  const z2 = y1 * Math.sin(b) + pz * Math.cos(b);
  const k = P / (P - z2);
  return { left: s.cw / 2 + (s.cw / 2 + x1 - s.cw / 2) * k, top: s.ch / 2 + (s.sy + y2 - s.ch / 2) * k };
}

function satellite(ppm) {
  const g = document.createElement("div");
  g.className = "ground";
  if (building.lat == null) return g;
  const z = 18, n = 2 ** z, R = 2;
  const lat = (building.lat * Math.PI) / 180;
  const xt = ((building.lon + 180) / 360) * n;
  const yt = ((1 - Math.log(Math.tan(lat) + 1 / Math.cos(lat)) / Math.PI) / 2) * n;
  const mpp = (156543.03392 * Math.cos(lat)) / n;   // metres per tile pixel
  const ts = 256 * mpp * ppm;                         // one tile on screen
  const cx = Math.floor(xt), cy = Math.floor(yt);
  for (let dx = -R; dx <= R; dx++) {
    for (let dy = -R; dy <= R; dy++) {
      const img = new Image();
      img.alt = "";
      img.decoding = "async";
      img.onerror = () => img.remove();
      img.style.cssText = `width:${ts + 0.6}px;height:${ts + 0.6}px;left:${(cx + dx - xt) * ts}px;top:${(cy + dy - yt) * ts}px`;
      img.src = `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${cy + dy}/${cx + dx}`;
      g.appendChild(img);
    }
  }
  const s = (R + 1) * ts;
  g.insertAdjacentHTML("beforeend", `<div class="shade" style="left:${-s}px;top:${-s}px;width:${2 * s}px;height:${2 * s}px"></div>`);
  g.style.transform = `translateZ(-2px) rotateZ(${-(building.north_offset_deg || 0)}deg)`;
  return g;
}

function makePin(s, label, cls) {
  const ring = document.createElement("div");
  ring.className = "ring " + cls;
  const head = document.createElement("div");
  head.className = "marker " + cls;
  head.innerHTML = `<div class="head">${esc(label)}</div><div class="stem"></div>`;
  s.overlay.appendChild(head);
  return { ring, head };
}

function putPin(pin, who, color) {
  const s = built.scene;
  const f = who.floor != null ? s.fl[who.floor] : null;
  if (!f) { pin.ring.remove(); pin.head.hidden = true; return; }
  if (pin.ring.parentNode !== f) f.appendChild(pin.ring);
  const x = who.x ?? s.W / 2, y = who.y ?? s.D / 2;
  pin.ring.style.left = `${(x / s.W) * 100}%`;
  pin.ring.style.top = `${(1 - y / s.D) * 100}%`;
  pin.ring.style.setProperty("--c", color);
  const pt = project(s, x, y, who.floor);
  pin.head.hidden = false;
  pin.head.style.left = `${pt.left}px`;
  pin.head.style.top = `${pt.top}px`;
  pin.head.style.setProperty("--c", color);
}

function updateScene(p) {
  const s = built.scene;
  const m = mePerson();
  for (const [n, f] of Object.entries(s.fl)) {
    f.classList.toggle("on", +n === p.floor);
    f.classList.toggle("mine", !!(m && m.id !== p.id && +n === m.floor));
    f.style.setProperty("--c", COLORS[p.status]);
    s.nums[n].classList.toggle("on", +n === p.floor);
    s.nums[n].style.setProperty("--c", COLORS[p.status]);
  }
  if (m && m.id !== p.id) putPin(s.you, m, "var(--you)");
  else { s.you.ring.remove(); s.you.head.hidden = true; }
  putPin(s.pin, p, COLORS[p.status]);
}

/* heart chart */

function chartHtml(p) {
  const h = p.history || [];
  const title = `<h2>Heart rate, last 10 minutes</h2>`;
  if (h.length < 2) return title + `<p class="hint">Waiting for readings.</p>`;
  const W = 600, H = 130, pad = 6;
  let lo = Infinity, hi = -Infinity;
  for (const r of h) { lo = Math.min(lo, r[1], r[2]); hi = Math.max(hi, r[1], r[3]); }
  lo = Math.floor(lo - 4); hi = Math.ceil(hi + 4);
  const X = (t) => (((t + 600) / 600) * W).toFixed(1);
  const Y = (v) => (pad + ((hi - v) / (hi - lo)) * (H - 2 * pad)).toFixed(1);
  const band = "M" + h.map((r) => `${X(r[0])},${Y(r[3])}`).join("L") + "L" + h.slice().reverse().map((r) => `${X(r[0])},${Y(r[2])}`).join("L") + "Z";
  const line = "M" + h.map((r) => `${X(r[0])},${Y(r[1])}`).join("L");
  const last = h[h.length - 1];
  const stroke = p.status === "alert" || p.status === "watch" ? COLORS[p.status] : "var(--ink)";
  return `${title}
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Heart rate now ${last[1]}. Expected ${Math.round(last[2])} to ${Math.round(last[3])}.">
      <path d="${band}" fill="var(--band)" stroke="var(--ok)" stroke-width="1" vector-effect="non-scaling-stroke"/>
      <path d="${line}" fill="none" stroke="${stroke}" stroke-width="2.5" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    </svg>
    <div class="axis"><span>10 min ago</span><span>Now</span></div>
    <div class="legend"><span class="sw"></span>Expected for ${objPronoun(p)} right now: ${Math.round(last[2])} to ${Math.round(last[3])} bpm</div>`;
}

function actionsHtml(p) {
  const m = mePerson();
  let h = "";
  if ((p.status === "alert" || p.status === "watch") && p.id !== meId) {
    h += m && p.acked_by === m.name
      ? `<button class="btn" disabled>You're on the way</button>`
      : `<button class="btn" data-act="ack" data-id="${esc(p.id)}">I'm on my way</button>`;
  }
  if (p.phone) h += `<a class="btn ghost" href="tel:${esc(p.phone)}">Call ${esc(p.name)}</a>`;
  return h;
}

/* ---------- actions ---------- */

async function ack(id) {
  const m = mePerson();
  try {
    await fetch("/api/ack", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, by: m ? m.name : "Someone" }) });
  } catch (e) { /* the next update will show the real state */ }
}

function openPicker() {
  const ppl = [...state.people].sort((a, b) => a.name.localeCompare(b.name));
  const opts = ppl.map((p) => `<button class="opt" role="radio" aria-checked="${p.id === meId}" data-act="choose" data-id="${esc(p.id)}"><span class="radio"></span>${esc(p.name)}</button>`).join("")
    + `<button class="opt" role="radio" aria-checked="${!meId}" data-act="choose" data-id=""><span class="radio"></span>I'm not wearing one</button>`;
  sheet.innerHTML = `<div class="panel" role="dialog" aria-modal="true" aria-labelledby="pick-h">
    <h2 id="pick-h">Which badge are you wearing?</h2>
    <p>Directions start from your badge.</p>
    <div role="radiogroup">${opts}</div>
    <button class="btn done" data-act="close">Done</button>
    <p class="small"><a href="/setup">Team setup page</a></p>
  </div>`;
  sheet.hidden = false;
  const sel = sheet.querySelector('[aria-checked="true"]');
  if (sel) sel.focus();
}

function closePicker() {
  sheet.hidden = true;
  sheet.innerHTML = "";
}

function onClick(e) {
  const go = e.target.closest("[data-go]");
  if (go) { location.hash = "#/p/" + encodeURIComponent(go.dataset.go); return; }
  const a = e.target.closest("[data-act]");
  if (!a) return;
  const act = a.dataset.act;
  if (act === "back") location.hash = "#/";
  else if (act === "pick") openPicker();
  else if (act === "close") closePicker();
  else if (act === "compass") enableCompass();
  else if (act === "ack") ack(a.dataset.id);
  else if (act === "choose") {
    meId = a.dataset.id;
    store.set("beacon.me", meId);
    openPicker();
    built = null;
    render();
  }
}

start();
