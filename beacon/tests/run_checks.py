"""Runs the whole demo in fast-forward and checks the things judges will see.
python3 tests/run_checks.py"""
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from hub import Hub  # noqa: E402
from sim import Sim  # noqa: E402

hub = Hub(persist=False)
sim = Sim(hub)
hub.sim = sim
now = 1_000_000.0
sim.boot(now)
t = now

floor_hits, floor_total, pos_err = 0, 0, []
worst = {}


def run(seconds, track=None):
    global t, floor_hits, floor_total
    for _ in range(seconds):
        t += 1
        sim.step(t)
        truth = sim.truth()
        snap = {p["id"]: p for p in hub.snapshot(t)["people"]}
        for bid, tr in truth.items():
            s = snap[bid]
            floor_total += 1
            floor_hits += s["floor"] == tr["floor"]
            if s["x"] is not None and s["floor"] == tr["floor"]:
                pos_err.append(math.hypot(s["x"] - tr["x"], s["y"] - tr["y"]))
            rank = {"ok": 0, "learning": 0, "watch": 1, "alert": 2}.get(s["status"], 0)
            worst[bid] = max(worst.get(bid, 0), rank)
        if track:
            track(snap, t)


def show(label):
    print("\n" + label)
    for p in hub.snapshot(t)["people"]:
        print("  %-8s %-14s hr=%-4s normal=%-3s %-28s %s" % (
            p["name"], p["status_word"], p["hr"], p["normal"], p["place"], p["sentence"]))


show("After boot (15 min learned):")
worst.clear()
run(300)
calm_before = dict(worst)
print("  worst status in 5 quiet minutes (0 ok, 1 watch, 2 alert):", calm_before)
worst.clear()

sim.action("rose_episode", t)
sim.action("joe_95", t)
alert_at = {}
both_95 = []


def watch_alerts(snap, tt):
    for bid in ("B1", "B2"):
        if snap[bid]["status"] == "alert" and bid not in alert_at:
            alert_at[bid] = tt - (t_start)
    r, j = snap["B1"]["hr"], snap["B2"]["hr"]
    if r and j and abs(r - j) <= 3 and r >= 90:
        both_95.append((round(tt - t_start), r, j, snap["B1"]["status"], snap["B2"]["status"]))


t_start = t
run(70, watch_alerts)
show("70 s after Rose's episode + Joe held at 95:")
print("  alert after (s):", alert_at)
print("  moments both read ~95:", both_95[:4], "... total", len(both_95))
print("  Joe worst status rank during window:", worst.get("B2"))

worst.clear()
sim.action("mae_stairs", t)
run(150)
show("Mae took the stairs to floor 3:")
print("  Mae worst status rank while climbing (0 ok, 1 watch, 2 alert):", worst.get("B3"))

sim.action("walk_to_rose", t)
run(120)
show("You walked to Rose:")
me = [p for p in hub.snapshot(t)["people"] if p["id"] == "B4"][0]
print("  Your badge hears Rose at ~%s m" % me["peers"].get("B1"))

print("\nFloor correct: %.1f%% of %d readings" % (100 * floor_hits / floor_total, floor_total))
print("Position error on the right floor: median %.1f m, 90th pct %.1f m" % (
    statistics.median(pos_err), sorted(pos_err)[int(0.9 * len(pos_err))]))

assert max(calm_before.values()) == 0, "No one should be flagged during quiet minutes"
assert alert_at.get("B1"), "Rose should alert"
assert "B2" not in alert_at, "Joe should not alert at 95"
assert worst.get("B3", 0) < 2, "Mae on the stairs should not alert"
print("\nAll checks passed.")
