"""Beacon server. No installs needed: python3 server.py --sim

  --sim     run fake badges so the app works with no hardware
  --port    default 8000
"""
import argparse
import json
import mimetypes
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from hub import ROOT, Hub
from sim import Sim

WEB = os.path.join(ROOT, "web")


class App:
    def __init__(self, hub):
        self.hub = hub
        self.snap = "{}"
        self.version = 0
        self.cond = threading.Condition()

    def tick_forever(self):
        while True:
            now = time.time()
            try:
                if self.hub.sim:
                    with self.hub.lock:
                        self.hub.sim.step(now)
                snap = json.dumps(self.hub.snapshot(now))
                with self.cond:
                    self.snap = snap
                    self.version += 1
                    self.cond.notify_all()
            except Exception as e:  # keep the demo alive no matter what
                print("tick error:", repr(e))
            time.sleep(max(0.0, 1.0 - (time.time() - now)))


def make_handler(app):
    hub = app.hub

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _headers(self, code, ctype, length=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def send_json(self, obj, code=200):
            body = (obj if isinstance(obj, str) else json.dumps(obj)).encode()
            self._headers(code, "application/json", len(body))
            self.wfile.write(body)

        def read_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw or b"{}")

        def do_OPTIONS(self):
            self._headers(204, "text/plain", 0)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                return self.send_json(app.snap)
            if path == "/api/building":
                b = dict(hub.building)
                b["floor_heights"] = hub.floor_heights()
                b["wifi_points"] = [{"floor": p["floor"], "x": p["x"], "y": p["y"]} for p in hub.wifi.points]
                b["default_me"] = next((p["badge"] for p in hub.people.values() if p.get("caregiver")), None)
                b["sim"] = hub.sim is not None
                return self.send_json(b)
            if path == "/api/stream":
                return self.stream()
            return self.static(path)

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.close_connection = True
            seen = -1
            try:
                while True:
                    with app.cond:
                        app.cond.wait_for(lambda: app.version != seen, timeout=15)
                        seen, snap = app.version, app.snap
                    self.wfile.write(("data: " + snap + "\n\n").encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def static(self, path):
            if path in ("/", ""):
                path = "/index.html"
            if path == "/setup":
                path = "/setup.html"
            full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
            if not full.startswith(WEB) or not os.path.isfile(full):
                return self.send_json({"error": "Not found"}, 404)
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                body = f.read()
            self._headers(200, ctype, len(body))
            self.wfile.write(body)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                d = self.read_json()
            except ValueError:
                return self.send_json({"ok": False, "error": "Body isn't valid JSON."}, 400)
            try:
                if path == "/api/ingest":
                    for pkt in d if isinstance(d, list) else [d]:
                        hub.ingest(pkt)
                    return self.send_json({"ok": True})
                if path == "/api/ack":
                    return self.send_json(hub.ack(d["id"], d.get("by")))
                if path == "/api/zero":
                    return self.send_json(hub.zero())
                if path == "/api/floor-check":
                    return self.send_json(hub.floor_check(d["badge"], int(d["floor"])))
                if path == "/api/survey":
                    return self.send_json(hub.survey(d["badge"], int(d["floor"]), float(d["x"]), float(d["y"])))
                if path == "/api/survey/clear":
                    return self.send_json(hub.clear_survey(d.get("floor")))
                if path == "/api/demo":
                    if not hub.sim:
                        return self.send_json({"ok": False, "error": "Demo buttons only work with --sim."}, 400)
                    with hub.lock:
                        return self.send_json(hub.sim.action(d.get("action"), time.time()))
            except KeyError as e:
                return self.send_json({"ok": False, "error": "Missing field: %s" % e}, 400)
            return self.send_json({"ok": False, "error": "Not found"}, 404)

    return Handler


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="run fake badges")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    hub = Hub(persist=not args.sim)
    if args.sim:
        hub.sim = Sim(hub)
        print("Simulator: learning everyone's normal (15 simulated minutes)...")
        hub.sim.boot(time.time())
    app = App(hub)
    threading.Thread(target=app.tick_forever, daemon=True).start()

    srv = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    srv.daemon_threads = True
    ip = lan_ip()
    print("\nBeacon is running")
    print("  App:          http://localhost:%d" % args.port)
    print("  Setup:        http://localhost:%d/setup" % args.port)
    print("  Badges post:  http://%s:%d/api/ingest" % (ip, args.port))
    print("  Phones:       http://%s:%d   (use an https tunnel for the compass arrow)\n" % (ip, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
