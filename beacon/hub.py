"""Holds every badge's state and turns raw packets into what the app shows."""
import json
import math
import os
import threading
import time
from collections import deque

from heart import HeartModel
from locate import FloorTracker, WifiMap, pressure_to_alt, rssi_to_m

ROOT = os.path.dirname(os.path.abspath(__file__))

STATUS_WORD = {
    "alert": "Needs a check", "watch": "Keep an eye", "sensor": "Check sensor",
    "offline": "Offline", "learning": "Learning", "ok": "OK",
}
STATUS_ORDER = {"alert": 0, "watch": 1, "sensor": 2, "offline": 3, "learning": 4, "ok": 5}
OBJECT = {"her": "her", "his": "him", "their": "them"}
POSSESSIVE = {"her": "Her", "his": "His", "their": "Their"}


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Badge:
    def __init__(self, bid, person):
        self.id = bid
        self.person = person
        self.heart = HeartModel(rest_max=person.get("rest_max", 130),
                                rest_min=person.get("rest_min", 40))
        self.tracker = FloorTracker()
        self.last_t = None
        self.light = None      # photoresistor, 0..100
        self.sun_s = 0.0       # seconds of strong light (about UV 6+), drains twice as fast in shade
        self.light_t = None
        self.p = None
        self.p_hist = deque(maxlen=15)
        self.height = None
        self.floor = None
        self.scan = None
        self.scan_t = None
        self.x = None
        self.y = None
        self.pos_floor = None
        self.peers = {}
        self.acked_by = None


class Hub:
    def __init__(self, persist=True):
        self.lock = threading.RLock()
        self.persist = persist
        self.building = load_json(os.path.join(ROOT, "config", "building.json"), {})
        people = load_json(os.path.join(ROOT, "config", "people.json"), [])
        self.people = {p["badge"]: p for p in people}
        self.badges = {}
        self.ref = None
        self.ref_hist = deque(maxlen=15)
        self.sim = None
        self.cal_path = os.path.join(ROOT, "data", "calibration.json")
        self.wifi_path = os.path.join(ROOT, "data", "wifi_map.json")
        self.cal = {"offsets": {}, "floor_heights": {}}
        self.wifi = WifiMap()
        if persist:
            self.cal = load_json(self.cal_path, self.cal)
            self.wifi = WifiMap(load_json(self.wifi_path, []))

    # --- config helpers -------------------------------------------------
    def floor_heights(self):
        fh = self.building.get("floor_height_m", 4.5)
        g = self.building.get("ground_floor", 1)
        heights = {f: (f - g) * fh for f in self.building.get("floors", [1])}
        for k, v in self.cal.get("floor_heights", {}).items():
            heights[int(k)] = v
        return heights

    def save(self):
        if not self.persist:
            return
        os.makedirs(os.path.dirname(self.cal_path), exist_ok=True)
        with open(self.cal_path, "w") as f:
            json.dump(self.cal, f, indent=1)
        with open(self.wifi_path, "w") as f:
            json.dump(self.wifi.points, f)

    def badge(self, bid):
        if bid not in self.badges:
            person = self.people.get(bid, {"badge": bid, "name": "Badge " + bid, "pronoun": "their"})
            self.badges[bid] = Badge(bid, person)
        return self.badges[bid]

    # --- incoming packets -----------------------------------------------
    def ingest(self, pkt, t=None):
        t = t if t is not None else time.time()
        with self.lock:
            if pkt.get("ref"):
                self.ref = (t, float(pkt["p"]))
                self.ref_hist.append(float(pkt["p"]))
                return
            b = self.badge(str(pkt["id"]))
            b.last_t = t
            b.heart.update(t, pkt.get("hr"), pkt.get("q"), pkt.get("act", 0))

            if pkt.get("p"):
                b.p = float(pkt["p"])
                b.p_hist.append(b.p)
                if self.ref and t - self.ref[0] < 30:
                    off = self.cal["offsets"].get(b.id, 0.0)
                    b.floor, b.height = b.tracker.update(t, b.p, self.ref[1], off, self.floor_heights())

            if pkt.get("light") is not None:
                lv = max(0.0, min(100.0, float(pkt["light"])))
                dt = min(5.0, t - b.light_t) if b.light_t else 1.0
                b.light = lv if b.light is None else b.light + 0.3 * (lv - b.light)
                b.light_t = t
                b.sun_s = b.sun_s + dt if b.light / 100 * 11 >= 6 else max(0.0, b.sun_s - 2 * dt)

            if pkt.get("wifi"):
                b.scan = {str(m).lower(): float(r) for m, r in pkt["wifi"]}
                b.scan_t = t
                self._place(b)

            for pid, rssi in pkt.get("ble", []):
                old = b.peers.get(pid)
                r = float(rssi) if not old or t - old[1] > 10 else old[0] + 0.3 * (float(rssi) - old[0])
                b.peers[pid] = (r, t)

            if b.acked_by and not b.heart.alerting:
                b.acked_by = None

    def _place(self, b):
        res = self.wifi.locate(b.scan, floor=b.floor)
        if not res:
            return
        floor = b.floor if b.floor is not None else res["floor"]
        if b.x is None or b.pos_floor != floor:
            b.x, b.y = res["x"], res["y"]
        else:
            b.x += 0.4 * (res["x"] - b.x)
            b.y += 0.4 * (res["y"] - b.y)
        b.pos_floor = floor

    # --- setup actions --------------------------------------------------
    def zero(self, now=None):
        """All badges sit next to the reference badge; remember each sensor's offset."""
        now = now or time.time()
        with self.lock:
            if not self.ref_hist:
                return {"ok": False, "error": "The reference badge isn't sending. Turn it on first."}
            ref_alt = pressure_to_alt(sum(self.ref_hist) / len(self.ref_hist))
            done = []
            for b in self.badges.values():
                if b.last_t and now - b.last_t < 15 and b.p_hist:
                    alt = pressure_to_alt(sum(b.p_hist) / len(b.p_hist))
                    self.cal["offsets"][b.id] = alt - ref_alt
                    b.tracker = FloorTracker()
                    b.height = b.floor = None
                    done.append(b.id)
            self.save()
            return {"ok": True, "zeroed": done}

    def floor_check(self, bid, floor):
        with self.lock:
            b = self.badges.get(bid)
            if not b or b.height is None:
                return {"ok": False, "error": "No height yet from " + bid + "."}
            self.cal["floor_heights"][str(int(floor))] = round(b.tracker.h, 2)
            self.save()
            return {"ok": True, "floor": floor, "height_m": round(b.tracker.h, 2)}

    def survey(self, bid, floor, x, y, now=None):
        now = now or time.time()
        with self.lock:
            b = self.badges.get(bid)
            if not b or not b.scan or now - b.scan_t > 15:
                return {"ok": False, "error": bid + " hasn't sent a Wi-Fi scan in the last 15 seconds."}
            self.wifi.add(int(floor), float(x), float(y), b.scan)
            self.save()
            return {"ok": True, "points": len(self.wifi.points)}

    def clear_survey(self, floor=None):
        with self.lock:
            self.wifi.clear(floor)
            self.save()
            return {"ok": True, "points": len(self.wifi.points)}

    def ack(self, bid, by):
        with self.lock:
            b = self.badges.get(bid)
            if b:
                b.acked_by = by or "Someone"
            return {"ok": bool(b)}

    # --- what the app reads ---------------------------------------------
    def place(self, floor, x, y):
        if floor is None:
            return "Floor unknown"
        text = "Floor %d" % floor
        if x is None:
            return text
        zones = [z for z in self.building.get("zones", []) if z["floor"] == floor]
        if zones:
            z = min(zones, key=lambda z: math.hypot(z["x"] - x, z["y"] - y))
            if math.hypot(z["x"] - x, z["y"] - y) < 25:
                text += ", near " + z["name"]
        return text

    def sentence(self, b, status, now):
        h = b.heart
        name = b.person.get("name", b.id)
        pr = b.person.get("pronoun", "their")
        obj, pos = OBJECT.get(pr, "them"), POSSESSIVE.get(pr, "Their")
        mu = round(h.baseline()[0])
        hr = round(h.hr) if h.hr else None
        act = h.activity()
        doing = {"sitting": "sitting still", "walking": "walking", "stairs": "on the stairs"}[act]
        if status == "offline":
            ago = int(now - b.last_t) if b.last_t else None
            return "Badge offline." + (" Last heard from %s ago." % fmt_ago(ago) if ago else "")
        if status == "sensor":
            return "Can't read the heart sensor. Check that it's touching skin."
        if status == "alert":
            if h.direction == "low":
                return "Heart rate %d, well below %s normal of about %d." % (hr, pos.lower(), mu)
            return "Heart rate %d while %s. %s normal is about %d." % (hr, doing, pos, mu)
        if status == "watch":
            way = "higher" if h.direction == "high" else "lower"
            return "%d is %s than usual for %s. Keeping an eye on it." % (hr, way, obj)
        if status == "learning":
            return "Learning %s normal heart rate." % pos.lower()
        if act == "sitting":
            return "Normal for %s." % obj
        return "%s. %d is expected." % ({"walking": "Walking", "stairs": "On the stairs"}[act], hr)

    def snapshot(self, now=None):
        now = now or time.time()
        with self.lock:
            out = []
            for b in self.badges.values():
                h = b.heart
                status = h.status(now)
                floor = b.floor if b.floor is not None else b.pos_floor
                hist = list(h.history)[-600::5]
                peers = {pid: round(rssi_to_m(r), 1) for pid, (r, pt) in b.peers.items() if now - pt < 10}
                out.append({
                    "id": b.id,
                    "name": b.person.get("name", b.id),
                    "pronoun": b.person.get("pronoun", "their"),
                    "phone": b.person.get("phone", ""),
                    "caregiver": bool(b.person.get("caregiver")),
                    "status": status,
                    "status_word": STATUS_WORD[status],
                    "sentence": self.sentence(b, status, now),
                    "title": "%s needs a check" % b.person.get("name", b.id) if status == "alert" else None,
                    "hr": round(h.hr) if h.hr and status not in ("sensor", "offline") else None,
                    "normal": round(h.baseline()[0]),
                    "activity": h.activity(),
                    "floor": floor,
                    "height_m": round(b.height, 1) if b.height is not None else None,
                    "x": round(b.x, 1) if b.x is not None else None,
                    "y": round(b.y, 1) if b.y is not None else None,
                    "place": self.place(floor, b.x, b.y),
                    "acked_by": b.acked_by,
                    "peers": peers,
                    "history": [[round(t - now), hr, lo, hi] for t, hr, _e, lo, hi in hist],
                    "seen_s": round(now - b.last_t) if b.last_t else None,
                    "light": round(b.light) if b.light is not None else None,
                    "uv_index": round(b.light / 100 * 11) if b.light is not None else None,
                    "sun_min": round(b.sun_s / 60),
                    "debug": {"p": b.p, "z": round(h.z, 2), "cusum": round(max(h.s_up, h.s_dn), 1),
                              "scan_aps": len(b.scan or {}), "offset_m": round(self.cal["offsets"].get(b.id, 0.0), 2)},
                })
            out.sort(key=lambda d: (STATUS_ORDER[d["status"]], d["caregiver"], d["name"]))
            return {
                "t": now,
                "ref_ok": bool(self.ref and now - self.ref[0] < 30),
                "sim": self.sim is not None,
                "wifi_points": len(self.wifi.points),
                "people": out,
            }


def fmt_ago(s):
    if s < 90:
        return "%d seconds" % s
    if s < 5400:
        return "%d minutes" % round(s / 60)
    return "%d hours" % round(s / 3600)
