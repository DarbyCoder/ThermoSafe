"""Fake badges for the demo. They send exactly what a real ESP32 badge sends
(heart rate, activity, pressure, Wi-Fi scan, Bluetooth peers), so the real
floor, position and heart-rate code runs on them unchanged."""
import math
import random

CLIP_M = 1.2        # badge height above the floor
WALK = 1.2          # m/s
CLIMB = 0.35        # m/s vertical on stairs


class SimPerson:
    def __init__(self, bid, floor, x, y, base, gain, offset_hpa, rng, wobble=0.45):
        self.id = bid
        self.floor, self.x, self.y = floor, x, y
        self.z = 0.0
        self.base, self.gain = base, gain
        self.wobble = wobble   # how much their heart rate drifts minute to minute
        self.offset_hpa = offset_hpa
        self.rng = rng
        self.path = []
        self.act = 0.05
        self.hr = base
        self.wander = 0.0
        self.extra = 0.0
        self.extra_target = 0.0
        self.extra_until = None
        self.last_scan = -99

    def go(self, floor, x, y, stairs, fh):
        self.path = []
        if floor != self.floor:
            s = min(stairs, key=lambda s: math.hypot(s["x"] - self.x, s["y"] - self.y))
            self.path.append(("walk", self.floor, s["x"], s["y"]))
            self.path.append(("climb", floor, s["x"], s["y"]))
        self.path.append(("walk", floor, x, y))


class Sim:
    def __init__(self, hub, seed=7):
        self.hub = hub
        self.rng = random.Random(seed)
        b = hub.building
        self.b = b
        self.fh = b.get("floor_height_m", 4.5)
        self.g = b.get("ground_floor", 1)
        self.W, self.D = b["width_m"], b["depth_m"]
        r = self.rng
        self.people = {
            "B1": SimPerson("B1", 2, 64, 12, 58, 32, +0.55, r),   # Rose: low resting heart rate
            "B2": SimPerson("B2", 1, 45, 26, 91, 26, -0.40, r, wobble=0.9),   # Joe: high, variable resting rate
            "B3": SimPerson("B3", 1, 22, 40, 70, 34, +0.20, r),   # Mae
            "B4": SimPerson("B4", 1, 70, 15, 66, 30, -0.75, r),   # you, the person using the app
        }
        self.ref_pos = (45, 4)
        self.aps = []
        for f in b["floors"]:
            for i, (ax, ay) in enumerate([(15, 12), (45, 10), (75, 12), (15, 38), (45, 40), (75, 38), (45, 25)]):
                self.aps.append(("02:1c:4e:%02x:00:%02x" % (f, i), f, ax, ay))

    # --- physics --------------------------------------------------------
    def weather(self, t):
        return 1009.0 + 1.5 * math.sin(2 * math.pi * t / 7200)

    def pressure(self, t, height_m):
        return self.weather(t) * (1 - height_m / 44330.0) ** 5.255

    def height(self, p):
        return (p.floor - self.g) * self.fh + p.z + CLIP_M

    def scan(self, p):
        out = []
        for mac, f, ax, ay in self.aps:
            d = max(1.0, math.hypot(ax - p.x, ay - p.y))
            rssi = -38 - 27 * math.log10(d) - 16 * abs(f - p.floor) + self.rng.gauss(0, 4)
            if rssi > -90 and self.rng.random() > 0.1:
                out.append([mac, round(rssi)])
        out.sort(key=lambda a: -a[1])
        return out[:15]

    def ble(self, p):
        out = []
        for q in self.people.values():
            if q is p:
                continue
            d = math.sqrt((q.x - p.x) ** 2 + (q.y - p.y) ** 2 + (self.height(q) - self.height(p)) ** 2)
            rssi = -59 - 22 * math.log10(max(d, 0.5)) - 12 * abs(q.floor - p.floor) + self.rng.gauss(0, 3)
            if rssi > -95:
                out.append([q.id, round(rssi)])
        return out

    def move(self, p, dt):
        if not p.path:
            p.act = 0.05 + abs(self.rng.gauss(0, 0.03))
            return
        kind, f, tx, ty = p.path[0]
        if kind == "walk":
            d = math.hypot(tx - p.x, ty - p.y)
            step = WALK * dt
            if d <= step:
                p.x, p.y = tx, ty
                p.path.pop(0)
            else:
                p.x += (tx - p.x) / d * step
                p.y += (ty - p.y) / d * step
            p.act = 1.0 + self.rng.gauss(0, 0.1)
        else:
            direction = 1 if f > p.floor else -1
            p.z += direction * CLIMB * dt
            p.act = (1.6 if direction > 0 else 1.1) + self.rng.gauss(0, 0.12)
            if abs(p.z) >= self.fh:
                p.floor += direction
                p.z = 0.0
                if p.floor == f:
                    p.path.pop(0)

    def heart(self, p, t, dt):
        if p.extra_until and t > p.extra_until:
            p.extra_target, p.extra_until = 0.0, None
        p.extra += (1 - math.exp(-dt / 12.0)) * (p.extra_target - p.extra)
        target = p.base + p.gain * max(0.0, p.act - 0.05) + p.extra
        tau = 25.0 if target > p.hr else 65.0
        p.hr += (1 - math.exp(-dt / tau)) * (target - p.hr)
        p.wander = 0.97 * p.wander + self.rng.gauss(0, p.wobble)
        return p.hr + p.wander + self.rng.gauss(0, 1.2)

    # --- one tick -------------------------------------------------------
    def step(self, t, dt=1.0):
        self.hub.ingest({"ref": True, "p": round(self.pressure(t, 0.8) + self.rng.gauss(0, 0.02), 3)}, t)
        for p in self.people.values():
            self.move(p, dt)
            pkt = {
                "id": p.id,
                "hr": round(self.heart(p, t, dt)),
                "q": 0.95,
                "act": round(max(0.0, p.act), 2),
                "p": round(self.pressure(t, self.height(p)) + p.offset_hpa + self.rng.gauss(0, 0.02), 3),
            }
            if t - p.last_scan >= 5:
                pkt["wifi"] = self.scan(p)
                p.last_scan = t
            pkt["ble"] = self.ble(p)
            self.hub.ingest(pkt, t)

    def truth(self):
        return {p.id: {"floor": p.floor, "x": p.x, "y": p.y} for p in self.people.values()}

    # --- setup the real code needs --------------------------------------
    def survey(self):
        """Walk a grid with a spare badge, like the team would with the setup page."""
        probe = SimPerson("SURVEY", 1, 0, 0, 70, 30, 0, self.rng)
        for f in self.b["floors"]:
            for x in range(4, self.W, 7):
                for y in range(4, self.D, 7):
                    probe.floor, probe.x, probe.y = f, x, y
                    scans = [dict((m, r) for m, r in self.scan(probe)) for _ in range(3)]
                    macs = set().union(*scans)
                    avg = {m: sum(s.get(m, -100) for s in scans) / 3 for m in macs}
                    self.hub.wifi.add(f, x, y, avg)

    def boot(self, now, history_s=900):
        """Zero everyone next to the reference badge, then fast-forward 15 minutes
        so each person's normal is already learned when the demo opens."""
        start = now - history_s - 15
        saved = {i: (p.floor, p.x, p.y) for i, p in self.people.items()}
        for p in self.people.values():
            p.floor, (p.x, p.y) = self.g, self.ref_pos
        for i in range(12):
            self.step(start + i)
        self.hub.zero(now=start + 11)
        for i, p in self.people.items():
            p.floor, p.x, p.y = saved[i]
        self.survey()
        for p in self.people.values():
            p.last_scan = -99
        mae_walks = {200: (1, 70, 40), 420: (1, 22, 40)}
        joe_walks = {500: (1, 30, 12), 640: (1, 45, 26)}
        t = start + 12
        i = 0
        while t < now:
            if i in mae_walks:
                self.people["B3"].go(*mae_walks[i], self.b["stairs"], self.fh)
            if i in joe_walks:
                self.people["B2"].go(*joe_walks[i], self.b["stairs"], self.fh)
            self.step(t)
            t += 1
            i += 1

    # --- demo buttons ---------------------------------------------------
    def action(self, name, now):
        P = self.people
        st, fh = self.b["stairs"], self.fh
        if name == "rose_episode":
            P["B1"].path = []
            P["B1"].extra_target, P["B1"].extra_until = 38.0, None
        elif name == "joe_95":
            P["B2"].extra_target, P["B2"].extra_until = 95 - P["B2"].base, now + 60
        elif name == "mae_stairs":
            P["B3"].go(3, 30, 40, st, fh)
        elif name == "walk_to_rose":
            r = P["B1"]
            P["B4"].go(r.floor, r.x + 1.5, r.y + 1.0, st, fh)
        elif name == "reset":
            for p in P.values():
                p.extra_target, p.extra_until, p.path = 0.0, None, []
            P["B3"].go(1, 22, 40, st, fh)
            P["B4"].go(1, 70, 15, st, fh)
        else:
            return {"ok": False, "error": "Unknown demo action."}
        return {"ok": True}
