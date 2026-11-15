"""ThermoSafe server: serves the dashboard, watches real clips, and phones a worker whose clip drops off the network.

No installs needed:

    python3 thermo_server.py                 # http://localhost:8000   (Windows: double-click start.bat)

Settings can go in a file named .env next to this one (copy .env.example; git ignores .env).
If cloudflared is installed and PUBLIC_URL isn't set, the server opens a free Cloudflare tunnel itself.

Phone calls go through Twilio. Set these before starting (never commit them):

    export TWILIO_ACCOUNT_SID=ACxxxxxxxx
    export TWILIO_AUTH_TOKEN=xxxxxxxx
    export TWILIO_FROM=+14135550100           # your Twilio number
    export PUBLIC_URL=https://xxxx.ngrok-free.app   # optional: lets the call hear "press 1" and report call progress

Without PUBLIC_URL the call still rings and speaks, but key presses and call status can't come back.

Gemini writes each call and understands spoken answers when this is set (on this laptop only, never in the repo):

    export GEMINI_API_KEY=xxxxxxxx
    export GEMINI_MODEL=gemini-3.7-flash      # optional

If Gemini is slow (over about 3 seconds) or unavailable, the call uses the fixed script, so it never goes silent.
Without Twilio settings the dashboard plays a practice call instead.

Clips POST their readings to /api/ingest (JSON with an "id", as the badge firmware already does).
A clip that hasn't posted for OFFLINE_AFTER seconds shows as offline in the dashboard.
"""
import argparse
import atexit
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_env():
    """KEY=value lines from .env (never committed). Settings already in the environment win."""
    path = os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            ln = raw.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k, v = k.replace("export ", "", 1).strip(), v.strip().strip('"').strip("'")
            if k and v and not os.environ.get(k):
                os.environ[k] = v
    return True


ENV_FILE = load_env()
OFFLINE_AFTER = float(os.environ.get("OFFLINE_AFTER", "4"))
VOICE = os.environ.get("TWILIO_VOICE", "Polly.Joanna-Neural")
MESSAGE = ("Hello, this is an automated call made because your ThermoSafe is disconnected from the network. "
           "Please circle back to the site manager to get it repaired and restarted. Stay safe!")
MENU = "If you are making your way back to the site manager, press 1. If you need help where you are, press 2."
REPLIES = {
    "1": "Thank you. Your site manager knows you are on your way back. Stay safe!",
    "2": "Thank you. Your site manager has been told you need help, and will call you now. Stay safe!",
}

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
GEMINI_WAIT = float(os.environ.get("GEMINI_WAIT", "3"))
gem_no_thinking = False   # set once Google says this model doesn't take the thinking setting   # seconds; past this the fixed words are used
CALL_RULES = ("You write the words of an automated phone call from ThermoSafe, a safety system for custodial and grounds workers at UMass Amherst. "
              "The worker's ThermoSafe clip has dropped off the network. Write what the call says, in plain spoken English, at most 45 words, "
              "no lists, no markdown, no emojis, no phone numbers. Say it is an automated call from ThermoSafe, greet them by first name, "
              "say their clip is disconnected from the network, ask them to circle back to the site manager to get it repaired and restarted, "
              "and end with 'Stay safe!'. You may mention where they were last heard from and how long ago. Do not ask questions; a menu follows.")
REPLY_RULES = ("You handle a worker's spoken answer on an automated ThermoSafe call about their disconnected clip. "
               "The call asked: if you are making your way back to the site manager, press 1; if you need help where you are, press 2. "
               "Classify what they said and write the short spoken reply (at most 25 words, plain English, end with 'Stay safe!'). "
               "Use 'returning' if they are heading back or will come, 'help' if they are hurt, unwell, stuck or ask for help, otherwise 'other'. "
               "For 'help' say their site manager has been told and will call them now. For 'other' say their site manager has been told. "
               'Answer only with JSON: {"intent": "returning" | "help" | "other", "reply": "..."}')

lock = threading.Lock()
devices = {}   # clip id -> {"seen": unix time, "last": last packet}
calls = {}     # call id -> record shown in the dashboard


def cfg():
    return {k: os.environ.get(k, "").strip() for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "PUBLIC_URL")}


def twilio_ready():
    c = cfg()
    return bool(c["TWILIO_ACCOUNT_SID"] and c["TWILIO_AUTH_TOKEN"] and c["TWILIO_FROM"])


def line(rec, who, text, ai=False):
    rec["lines"].append({"t": time.time(), "who": who, "text": text, "ai": ai})


def gemini(system, prompt, as_json=False, wait=None):
    """One Gemini answer, or None if there's no key, it errors, or it takes longer than `wait` seconds."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    deadline = time.time() + (wait or GEMINI_WAIT)
    api = os.environ.get("GEMINI_API", "https://generativelanguage.googleapis.com").rstrip("/")
    url = "%s/v1beta/models/%s:generateContent" % (api, GEMINI_MODEL)
    global gem_no_thinking
    gen = {"maxOutputTokens": 400} if gem_no_thinking else {"maxOutputTokens": 400, "thinkingConfig": {"thinkingLevel": "low"}}
    if as_json:
        gen["responseMimeType"] = "application/json"
    for attempt in range(2):   # if Google rejects the thinking setting, ask once more without it
        left = deadline - time.time()
        if left < 0.3:
            return None
        body = json.dumps({"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}).encode()
        req = Request(url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urlopen(req, timeout=left) as r:
                j = json.loads(r.read().decode())
            parts = ((j.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            text = "".join(x.get("text", "") for x in parts if not x.get("thought")).strip()
            return text or None
        except HTTPError as e:
            msg = e.read().decode(errors="replace")
            if attempt == 0 and e.code == 400 and "thinking" in msg.lower() and "thinkingConfig" in gen:
                gen.pop("thinkingConfig", None); gem_no_thinking = True
                continue
            print("Gemini error %s: %s" % (e.code, msg[:200]))
            return None
        except (URLError, OSError, ValueError) as e:
            print("Gemini unavailable:", e)
            return None
    return None


def clean_speech(text, limit=420):
    """Make Gemini's words safe and short enough to speak."""
    t = " ".join(str(text).replace("*", "").replace("#", "").split())
    return t[:limit].rsplit(" ", 1)[0] + "." if len(t) > limit else t


def call_words(rec):
    """Gemini writes this call from what the dashboard knows; the fixed message if it can't in time."""
    ctx = rec.get("ctx") or {}
    facts = {k: v for k, v in {"first_name": ctx.get("first") or (rec.get("name") or "").split(" ")[0], "last_heard_near": ctx.get("place"),
             "seconds_since_last_heard": ctx.get("offline_s"), "why": ctx.get("reason")}.items() if v not in (None, "")}
    words = gemini(CALL_RULES, "Facts: " + json.dumps(facts))
    if words:
        words = clean_speech(words)
        if "thermosafe" in words.lower() and len(words) > 40:
            return words, True
    return MESSAGE, False


def twiml(cid, words=MESSAGE):
    c = cfg()
    say = '<Say voice="%s">%s</Say>' % (VOICE, escape(words))
    menu = '<Say voice="%s">%s</Say>' % (VOICE, escape(MENU))
    if c["PUBLIC_URL"]:
        action = escape("%s/twilio/gather?%s" % (c["PUBLIC_URL"].rstrip("/"), urlencode({"c": cid})))
        return ('<?xml version="1.0" encoding="UTF-8"?><Response>%s'
                '<Gather input="dtmf speech" numDigits="1" timeout="6" speechTimeout="auto" action="%s" method="POST">%s</Gather>'
                '<Say voice="%s">We did not hear a response. Your site manager has been told. Goodbye.</Say></Response>') % (say, action, menu, VOICE)
    return '<?xml version="1.0" encoding="UTF-8"?><Response>%s<Pause length="1"/>%s</Response>' % (say, say)


call_mode = None   # which way of placing a call this Twilio account accepted (trial accounts allow fewer options)


def call_forms(rec, words, c):
    """Ways to ask Twilio for this call, fullest first. A trial account may refuse some options, so the next one is tried."""
    pub = c["PUBLIC_URL"].rstrip("/") if c["PUBLIC_URL"] else ""
    base = {"To": rec["to"], "From": c["TWILIO_FROM"]}
    tw = twiml(rec["id"], words)
    forms = []
    if pub:
        forms.append(("full", dict(base, Twiml=tw, StatusCallback="%s/twilio/status?%s" % (pub, urlencode({"c": rec["id"]})),
                                   StatusCallbackEvent=["initiated", "ringing", "answered", "completed"])))
    forms.append(("script", dict(base, Twiml=tw)))
    if pub:
        forms.append(("link", dict(base, Url="%s/twilio/voice?%s" % (pub, urlencode({"c": rec["id"]})))))
    if call_mode:   # start with what worked last time
        forms.sort(key=lambda f: f[0] != call_mode)
    return forms


def place_call(rec):
    """Ask Twilio to ring the worker. Runs in a thread so the dashboard never waits on the phone network."""
    global call_mode
    c = cfg()
    words, ai = call_words(rec)
    with lock:
        rec["words"], rec["ai"] = words, ai
    api = os.environ.get("TWILIO_API", "https://api.twilio.com").rstrip("/")   # only changed for testing against a stand-in
    url = "%s/2010-04-01/Accounts/%s/Calls.json" % (api, c["TWILIO_ACCOUNT_SID"])
    auth = base64.b64encode(("%s:%s" % (c["TWILIO_ACCOUNT_SID"], c["TWILIO_AUTH_TOKEN"])).encode()).decode()
    msg = ""
    for mode, form in call_forms(rec, words, c):
        req = Request(url, data=urlencode(form, doseq=True).encode(), headers={"Authorization": "Basic " + auth, "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(req, timeout=15) as r:
                j = json.loads(r.read().decode())
        except HTTPError as e:
            msg = e.read().decode(errors="replace")
            try:
                msg = json.loads(msg).get("message", msg)
            except ValueError:
                pass
            if e.code == 400 and "parameter" in msg.lower():   # an option this account won't take: try a simpler way
                print("Twilio refused the '%s' way (%s); trying a simpler one" % (mode, msg[:90]))
                continue
            break
        except (URLError, OSError) as e:
            with lock:
                rec["status"] = "failed"
                line(rec, "system", "Couldn't reach Twilio: %s" % e)
            return
        if call_mode != mode:
            print("Twilio accepted calls the '%s' way" % mode)
        call_mode = mode
        with lock:
            rec["sid"], rec["mode"] = j.get("sid"), mode
            rec["status"] = "ringing" if mode == "full" else "placed"
            line(rec, "system", "Calling %s" % rec["to"])
            if mode == "script":   # no reports come back until a key press: show what the call says now
                line(rec, "thermo", words, ai)
                if c["PUBLIC_URL"]:
                    line(rec, "thermo", MENU)
        return
    with lock:
        rec["status"] = "failed"
        low = msg.lower()
        hint = (" (this Twilio trial blocks custom calls: upgrade the account in Twilio's Console to fix it)" if "parameter" in low
                else " (set TWILIO_FROM to the trial number Twilio assigned to this phone: pick it as 'To' in the Console's Try out Voice and copy its 'From')" if "assigned" in low else "")
        line(rec, "system", "Twilio refused the call: %s%s" % (msg, hint))


def demo_phones():
    """DEMO_CALL_B5=+1... in .env: the phone that rings for that worker (keeps real numbers out of the repo)."""
    return {k[len("DEMO_CALL_"):]: v.strip() for k, v in os.environ.items() if k.startswith("DEMO_CALL_") and v.strip()}


def find_cloudflared():
    p = os.environ.get("CLOUDFLARED") or shutil.which("cloudflared")
    if p and os.path.isfile(p):
        return p
    home = os.path.expanduser("~")
    for c in (r"C:\Program Files (x86)\cloudflared\cloudflared.exe", r"C:\Program Files\cloudflared\cloudflared.exe",
              os.path.join(home, "Downloads", "cloudflared-windows-amd64.exe"), os.path.join(home, "Downloads", "cloudflared.exe"),
              os.path.join(ROOT, "cloudflared.exe"), os.path.join(ROOT, "cloudflared-windows-amd64.exe")):
        if os.path.isfile(c):
            return c
    return None


def start_tunnel(port):
    """A free Cloudflare quick tunnel to this server, so Twilio can report key presses. Its link becomes PUBLIC_URL."""
    exe = find_cloudflared()
    if not exe:
        print("  Tunnel:       cloudflared not found (winget install --id Cloudflare.cloudflared), so key presses are off")
        return None
    try:
        proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", "http://localhost:%d" % port],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")
    except OSError as e:
        print("  Tunnel:       couldn't start cloudflared (%s), so key presses are off" % e)
        return None
    atexit.register(proc.terminate)

    def watch():
        for ln in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", ln)
            if m and not os.environ.get("PUBLIC_URL"):
                os.environ["PUBLIC_URL"] = m.group(0)
                print("  Tunnel ready: %s  (key presses on; give it about 10 seconds before the first call)" % m.group(0))
    threading.Thread(target=watch, daemon=True).start()
    print("  Tunnel:       starting Cloudflare tunnel...")
    return proc


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
            return self.json({"ok": True, "twilio": twilio_ready(), "public": bool(cfg()["PUBLIC_URL"]), "gemini": bool(os.environ.get("GEMINI_API_KEY", "").strip()), "thermosafe_api": bool(os.environ.get("THERMOSAFE_DASHBOARD_KEY", "").strip()), "offline_after": OFFLINE_AFTER, "demo_phones": demo_phones()})
        if u.path == "/api/thermosafe/snapshot":
            api = os.environ.get("THERMOSAFE_API_URL", "http://127.0.0.1:8000").rstrip("/")
            key = os.environ.get("THERMOSAFE_DASHBOARD_KEY", "").strip()
            if not key:
                return self.json({"ok": False, "error": "THERMOSAFE_DASHBOARD_KEY is not configured for the dashboard bridge."}, 503)
            req = Request(api + "/v1/snapshot", headers={"X-Dashboard-Key": key, "Accept": "application/json"})
            try:
                with urlopen(req, timeout=4) as response:
                    return self.send(response.status, response.read(), "application/json")
            except HTTPError as e:
                return self.json({"ok": False, "error": "ThermoSafe API returned HTTP %s." % e.code}, 502)
            except (URLError, OSError) as e:
                return self.json({"ok": False, "error": "ThermoSafe API is unavailable: %s" % e}, 502)
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
                   "to": to, "status": "dialing", "digits": None, "lines": [], "ctx": d.get("context") or {}}
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
                if u.path == "/twilio/voice":   # the "link" way: Twilio asks for the call's words once it's answered
                    if rec:
                        rec["status"] = "in-progress"
                        line(rec, "system", "Answered")
                        line(rec, "thermo", rec.get("words") or MESSAGE, rec.get("ai", False))
                        if cfg()["PUBLIC_URL"]:
                            line(rec, "thermo", MENU)
                    return self.send(200, twiml(cid, (rec or {}).get("words") or MESSAGE), "text/xml")
                if u.path == "/twilio/status" and rec:
                    st = f.get("CallStatus", "")
                    if st and st != rec["status"]:
                        rec["status"] = st
                        if st == "in-progress":
                            line(rec, "system", "Answered")
                            line(rec, "thermo", rec.get("words") or MESSAGE, rec.get("ai", False))
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
            if u.path == "/twilio/gather":
                reply, ai = None, False
                if speech and not digit:   # they spoke: Gemini works out what they mean and answers
                    got = gemini(REPLY_RULES, "The worker said: " + json.dumps(speech), as_json=True)
                    try:
                        j = json.loads(got) if got else None
                    except ValueError:
                        j = None
                    if isinstance(j, dict) and j.get("intent") in ("returning", "help", "other"):
                        digit = {"returning": "1", "help": "2", "other": ""}[j["intent"]]
                        reply, ai = clean_speech(j.get("reply") or "", 240) or None, True
                    else:   # no Gemini: a simple guess from the words
                        low = speech.lower()
                        digit = "2" if any(w in low for w in ("help", "hurt", "stuck", "sick")) else "1" if any(w in low for w in ("yes", "way", "coming", "back", "one", "heading")) else ""
                with lock:
                    if rec and digit:
                        rec["digits"] = digit
                    reply = reply or REPLIES.get(digit, "Thank you. Your site manager has been told. Stay safe!")
                    if rec:
                        line(rec, "thermo", reply, ai)
                return self.send(200, '<?xml version="1.0" encoding="UTF-8"?><Response><Say voice="%s">%s</Say></Response>' % (VOICE, escape(reply)), "text/xml")
            return self.send(200, "<Response/>", "text/xml")
        return self.json({"ok": False, "error": "Not found"}, 404)


def lan_ip():
    import socket
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
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    c = cfg()
    print("\nThermoSafe is running")
    print("  Settings:     %s" % (".env loaded" if ENV_FILE else "no .env file (copy .env.example to .env)"))
    print("  Dashboard:    http://localhost:%d" % a.port)
    print("  ThermoSafe API bridge: %s" % ("configured" if os.environ.get("THERMOSAFE_DASHBOARD_KEY", "").strip() else "not configured; set THERMOSAFE_DASHBOARD_KEY in .env"))
    print("  Clips post:   http://%s:%d/api/ingest" % (lan_ip(), a.port))
    print("  Phone calls:  %s" % ("Twilio ready, from %s" % c["TWILIO_FROM"] if twilio_ready() else "practice calls only (Twilio settings not found)"))
    for wid, num in sorted(demo_phones().items()):
        print("  Demo phone:   %s rings %s" % (wid, num))
    print("  Gemini:       %s" % ("writes each call and reads spoken answers (%s)" % GEMINI_MODEL if os.environ.get("GEMINI_API_KEY", "").strip() else "off (fixed words; set GEMINI_API_KEY)"))
    if c["PUBLIC_URL"]:
        print("  Key presses:  via %s" % c["PUBLIC_URL"])
    elif os.environ.get("TUNNEL", "").lower() not in ("off", "0", "no"):
        start_tunnel(a.port)
    else:
        print("  Key presses:  off (TUNNEL=off and no PUBLIC_URL)")
    print("")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
