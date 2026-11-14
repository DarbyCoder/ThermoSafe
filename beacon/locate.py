"""Where is the badge?

Floor: air pressure. Each badge has a barometer, and one reference badge sits still
on the ground floor. Weather moves both by the same amount, so the difference is
pure height. Height / floor height = floor.

Position on the floor: the building's own Wi-Fi. A badge scans the access points it
can hear; we match that pattern against spots we recorded earlier (k nearest).

Close range: badges hear each other over Bluetooth. Signal strength -> rough metres.
"""
import math


def pressure_to_alt(p_hpa):
    return 44330.0 * (1.0 - (p_hpa / 1013.25) ** 0.1903)


def rssi_to_m(rssi, p1m=-59.0, n=2.2):
    return 10 ** ((p1m - rssi) / (10 * n))


class FloorTracker:
    """Turns pressure into a steady floor number."""

    def __init__(self):
        self.h = None
        self.floor = None
        self.cand = None
        self.cand_secs = 0.0
        self.last_t = None

    def update(self, t, p, p_ref, offset_m, floor_heights):
        raw = pressure_to_alt(p) - pressure_to_alt(p_ref) - offset_m
        dt = 1.0 if self.last_t is None else max(0.0, min(5.0, t - self.last_t))
        self.last_t = t
        if self.h is None:
            self.h = raw
        else:
            self.h += (1 - math.exp(-dt / 2.0)) * (raw - self.h)

        cand = min(floor_heights, key=lambda f: abs(floor_heights[f] - self.h))
        if self.floor is None:
            self.floor = cand
        elif cand != self.floor:
            self.cand_secs = self.cand_secs + dt if cand == self.cand else dt
            self.cand = cand
            if self.cand_secs >= 3.0:   # must hold for 3 s, so the stairs don't flicker
                self.floor = cand
                self.cand_secs = 0.0
        else:
            self.cand, self.cand_secs = None, 0.0
        return self.floor, self.h


def scan_distance(a, b):
    keys = set(a) | set(b)
    if not keys:
        return 999.0
    return math.sqrt(sum((a.get(k, -100) - b.get(k, -100)) ** 2 for k in keys) / len(keys))


class WifiMap:
    """Wi-Fi fingerprints: [{floor, x, y, scan: {mac: rssi}}]."""

    def __init__(self, points=None):
        self.points = points or []

    def add(self, floor, x, y, scan):
        self.points.append({"floor": floor, "x": x, "y": y, "scan": dict(scan)})

    def clear(self, floor=None):
        self.points = [p for p in self.points if floor is not None and p["floor"] != floor]

    def locate(self, scan, floor=None, k=3):
        if not scan or not self.points:
            return None
        cands = [p for p in self.points if floor is None or p["floor"] == floor] or self.points
        best = sorted(((scan_distance(scan, p["scan"]), p) for p in cands), key=lambda d: d[0])[:k]
        w = [1.0 / (d + 1.0) for d, _ in best]
        tot = sum(w)
        x = sum(wi * p["x"] for wi, (_, p) in zip(w, best)) / tot
        y = sum(wi * p["y"] for wi, (_, p) in zip(w, best)) / tot
        votes = {}
        for wi, (_, p) in zip(w, best):
            votes[p["floor"]] = votes.get(p["floor"], 0) + wi
        return {"x": x, "y": y, "floor": max(votes, key=votes.get), "match": best[0][0]}
