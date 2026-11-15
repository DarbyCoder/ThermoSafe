"""ThermoSafe server: serves the dashboard, watches real clips, and phones a worker whose clip drops off the network.

No installs needed:

    python3 thermo_server.py                 # http://localhost:8000

Phone calls go through Twilio. Set these before starting (never commit them):

    export TWILIO_ACCOUNT_SID=ACxxxxxxxx
    export TWILIO_AUTH_TOKEN=xxxxxxxx
    export TWILIO_FROM=+14135550100           # your Twilio number
    export PUBLIC_URL=https://xxxx.ngrok-free.app   # optional: lets the call hear "press 1" and report call progress

Without PUBLIC_URL the call still rings and speaks, but key presses and call status can't come back.
Without Twilio settings the dashboard plays a practice call instead.

Clips POST their readings to /api/ingest (JSON with an "id", as the badge firmware already does).
A clip that hasn't posted for OFFLINE_AFTER seconds shows as offline in the dashboard.
"""
import argparse
import base64
import json
import mimetypes
import os
import threading
import time
import uuid
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = os.path.dirname(os.path.abspath(__file__))
OFFLINE_AFTER = float(os.environ.get("OFFLINE_AFTER", "4"))
VOICE = os.environ.get("TWILIO_VOICE", "Polly.Joanna-Neural")
MESSAGE = ("Hello, this is an automated call made because your ThermoSafe is disconnected from the network. "
           "Please circle back to the site manager to get it repaired and restarted. Stay safe!")
MENU = "If you are making your way back to the site manager, press 1. If you need help where you are, press 2."
REPLIES = {
    "1": "Thank you. Your site manager knows you are on your way back. Stay safe!",
    "2": "Thank you. Your site manager has been told you need help, and will call you now. Stay safe!",
}

lock = threading.Lock()
devices = {}   # clip id -> {"seen": unix time, "last": last packet}
calls = {}     # call id -> record shown in the dashboard


def cfg():
    return {k: os.environ.get(k, "").strip() for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "PUBLIC_URL")}


def twilio_ready():
    c = cfg()
    return bool(c["TWILIO_ACCOUNT_SID"] and c["TWILIO_AUTH_TOKEN"] and c["TWILIO_FROM"])


def line(rec, who, text):
    rec["lines"].append({"t": time.time(), "who": who, "text": text})


def twiml(cid):
    c = cfg()
    say = '<Say voice="%s">%s</Say>' % (VOICE, escape(MESSAGE))
    menu = '<Say voice="%s">%s</Say>' % (VOICE, escape(MENU))
    if c["PUBLIC_URL"]:
        action = escape("%s/twilio/gather?%s" % (c["PUBLIC_URL"].rstrip("/"), urlencode({"c": cid})))
        return ('<?xml version="1.0" encoding="UTF-8"?><Response>%s'
                '<Gather input="dtmf speech" numDigits="1" timeout="6" speechTimeout="auto" action="%s" method="POST">%s</Gather>'
                '<Say voice="%s">We did not hear a response. Your site manager has been told. Goodbye.</Say></Response>') % (say, action, menu, VOICE)
    return '<?xml version="1.0" encoding="UTF-8"?><Response>%s<Pause length="1"/>%s</Response>' % (say, say)


def place_call(rec):
    """Ask Twilio to ring the worker. Runs in a thread so the dashboard never waits on the phone network."""
    c = cfg()
    form = {"To": rec["to"], "From": c["TWILIO_FROM"], "Twiml": twiml(rec["id"])}
    if c["PUBLIC_URL"]:
        form["StatusCallback"] = "%s/twilio/status?%s" % (c["PUBLIC_URL"].rstrip("/"), urlencode({"c": rec["id"]}))
        form["StatusCallbackEvent"] = ["initiated", "ringing", "answered", "completed"]
    body = urlencode(form, doseq=True).encode()
    api = os.environ.get("TWILIO_API", "https://api.twilio.com").rstrip("/")   # only changed for testing against a stand-in
    url = "%s/2010-04-01/Accounts/%s/Calls.json" % (api, c["TWILIO_ACCOUNT_SID"])
    auth = base64.b64encode(("%s:%s" % (c["TWILIO_ACCOUNT_SID"], c["TWILIO_AUTH_TOKEN"])).encode()).decode()
    req = Request(url, data=body, headers={"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode())
        with lock:
            rec["sid"] = j.get("sid")
            rec["status"] = "ringing" if c["PUBLIC_URL"] else "placed"
            line(rec, "system", "Calling %s" % rec["to"])
            if not c["PUBLIC_URL"]:   # no progress reports will come back: show what the call says now
                line(rec, "thermo", MESSAGE)
    except HTTPError as e:
        msg = e.read().decode(errors="replace")
        try:
            msg = json.loads(msg).get("message", msg)
        except ValueError:
            pass
        with lock:
            rec["status"] = "failed"
            line(rec, "system", "Twilio refused the call: %s" % msg)
    except (URLError, OSError) as e:
        with lock:
            rec["status"] = "failed"
            line(rec, "system", "Couldn't reach Twilio: %s" % e)


def device_list(now):
    with lock:
        return {k: {"age": round(now - v["seen"], 1), "online": now - v["seen"] < OFFLINE_AFTER} for k, v in devices.items()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj))

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode(errors="replace") if n else ""

    def do_OPTIONS(self):
        self.send(204, b"", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/health":
            return self.json({"ok": True, "twilio": twilio_ready(), "public": bool(cfg()["PUBLIC_URL"]), "offline_after": OFFLINE_AFTER})
        if u.path == "/api/devices":
            return self.json(device_list(time.time()))
        if u.path == "/api/calls":
            with lock:
                return self.json(sorted(calls.values(), key=lambda r: r["t"]))
        # the dashboard and its pictures
        rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        full = os.path.realpath(os.path.join(ROOT, rel))
        if not full.startswith(ROOT + os.sep) or not os.path.isfile(full) or os.path.basename(full).startswith("."):
            return self.send(404, "Not found", "text/plain")
        with open(full, "rb") as f:
            data = f.read()
        return self.send(200, data, mimetypes.guess_type(full)[0] or "application/octet-stream")

    def do_POST(self):
        u = urlparse(self.path)
        raw = self.body()
        if u.path == "/api/ingest":   # a clip's readings: the badge firmware posts {"id": "B1", ...} every second
            try:
                d = json.loads(raw or "{}")
            except ValueError:
                return self.json({"ok": False, "error": "Body isn't valid JSON."}, 400)
            now = time.time()
            with lock:
                for pkt in d if isinstance(d, list) else [d]:
                    if isinstance(pkt, dict) and pkt.get("id") and not pkt.get("ref"):
                        devices[str(pkt["id"])] = {"seen": now, "last": pkt}
            return self.json({"ok": True})
        if u.path == "/api/call":   # the dashboard asks to phone a worker whose clip went offline
            try:
                d = json.loads(raw or "{}")
            except ValueError:
                return self.json({"ok": False, "error": "Body isn't valid JSON."}, 400)
            to = "".join(ch for ch in str(d.get("to", "")) if ch.isdigit() or ch == "+")
            if not to:
                return self.json({"ok": False, "error": "No phone number."}, 400)
            if to[0] != "+":
                to = "+1" + to if len(to) == 10 else "+" + to
            if not twilio_ready():
                return self.json({"ok": False, "error": "Twilio isn't set up on the server."}, 503)
            rec = {"id": uuid.uuid4().hex[:12], "t": time.time(), "worker": d.get("worker"), "name": d.get("name", ""),
                   "to": to, "status": "dialing", "digits": None, "lines": []}
            with lock:
                calls[rec["id"]] = rec
            threading.Thread(target=place_call, args=(rec,), daemon=True).start()
            return self.json({"ok": True, "id": rec["id"]})
        if u.path.startswith("/twilio/"):   # Twilio reports back: key presses and call progress
            q = parse_qs(u.query)
            f = {k: v[0] for k, v in parse_qs(raw).items()}
            cid = (q.get("c") or [""])[0]
            with lock:
                rec = calls.get(cid)
                if u.path == "/twilio/status" and rec:
                    st = f.get("CallStatus", "")
                    if st and st != rec["status"]:
                        rec["status"] = st
                        if st == "in-progress":
                            line(rec, "system", "Answered")
                            line(rec, "thermo", MESSAGE)
                            line(rec, "thermo", MENU)
                        elif st in ("no-answer", "busy", "failed", "canceled"):
                            line(rec, "system", {"no-answer": "No answer", "busy": "Line busy", "failed": "Call failed", "canceled": "Canceled"}[st])
                        elif st == "completed":
                            line(rec, "system", "Call ended (%ss)" % f.get("CallDuration", "?"))
                    return self.send(200, "<Response/>", "text/xml")
                if u.path == "/twilio/gather":
                    digit, speech = f.get("Digits", ""), f.get("SpeechResult", "")
                    if rec:
                        if digit:
                            rec["digits"] = digit
                            line(rec, "worker", "Pressed %s" % digit)
                        if speech:
                            line(rec, "worker", speech)
                            if not digit:
                                low = speech.lower()
                                rec["digits"] = "2" if "help" in low else "1" if any(w in low for w in ("yes", "way", "coming", "back", "one")) else rec["digits"]
                    reply = REPLIES.get(rec["digits"] if rec else digit, "Thank you. Your site manager has been told. Stay safe!")
                    if rec:
                        line(rec, "thermo", reply)
                    return self.send(200, '<?xml version="1.0" encoding="UTF-8"?><Response><Say voice="%s">%s</Say></Response>' % (VOICE, escape(reply)), "text/xml")
            return self.send(200, "<Response/>", "text/xml")
        return self.json({"ok": False, "error": "Not found"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    c = cfg()
    print("\nThermoSafe is running")
    print("  Dashboard:    http://localhost:%d" % a.port)
    print("  Clips post:   http://<this laptop's IP>:%d/api/ingest" % a.port)
    print("  Phone calls:  %s" % ("Twilio ready, from %s" % c["TWILIO_FROM"] if twilio_ready() else "practice calls only (Twilio settings not found)"))
    print("  Key presses:  %s\n" % ("via %s" % c["PUBLIC_URL"] if c["PUBLIC_URL"] else "off (set PUBLIC_URL to an https tunnel, e.g. ngrok http %d)" % a.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
