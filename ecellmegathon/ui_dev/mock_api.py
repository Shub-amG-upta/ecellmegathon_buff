"""
A stand-in for Track A's app/server.py, for building the UI (Track B) before
the real server exists. Speaks the BUILD_PLAN §4.2 API exactly and serves ui/.

Voice is REAL: it calls app.voice.transcribe / app.voice.speak, so the mic ->
STT -> TTS -> audio loop is exercised end to end against the laptop or board
engines. Extraction is FAKE: a few keyword rules instead of the LLM, labelled
"mock" in `devices`, with LLM timings null (not measured). Never report a number
from this server, and never demo it as the product.

  .venv/Scripts/python -m ui_dev.mock_api          http://localhost:8000
  .venv/Scripts/python -m ui_dev.mock_api --no-voice   skip STT/TTS calls (text only)

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import voice

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
SCHEMA = json.loads((ROOT / "schema" / "incident_form.json").read_text(encoding="utf-8"))
FIELDS = SCHEMA["fields"]
BY_KEY = {f["key"]: f for f in FIELDS if f["key"]}
MANDATORY = [f["name"] for f in FIELDS if f["mandatory"]]
VOCAB_FILE = ROOT / "data" / "vocab.txt"

TYPE_WORDS = [("water_leak", r"leak|flood|water"), ("fire_smoke", r"fire|smoke|burning"),
              ("electrical", r"spark|electric|short circuit|shock"),
              ("chemical_spill", r"chemical|spill"), ("slip_trip_fall", r"slip|trip|fell|fall"),
              ("injury", r"injur|hurt|bleed|cut"), ("security", r"theft|stolen|intruder|break.?in"),
              ("equipment_failure", r"broke|failure|not working|stopped"), ("near_miss", r"near miss|almost")]
FOLLOW_UP = {"water_leak": ["Inspect source pipe", "Check for electrical exposure"],
             "fire_smoke": ["Inspect fire suppression system", "File fire safety report"]}


def empty_form() -> dict:
    form = {f["name"]: [] if f["kind"] in ("text_list", "catalogue") else None for f in FIELDS}
    form["description"] = ""
    return form


def fake_extract(text: str, asked: str | None) -> dict:
    """Keyword rules standing in for the LLM. Returns update_report args."""
    t, args = text.lower(), {}
    for kind, pat in TYPE_WORDS:
        if re.search(pat, t):
            args["t"] = kind
            break
    for sev in ("critical", "high", "medium", "low"):
        if re.search(rf"\b{sev}\b", t):
            args["s"] = sev
            break
    m = re.search(r"\b((?:near|in|at|outside|inside) (?:the )?[\w ]*?"
                  r"(?:room|floor|lab|pantry|kitchen|corridor|lobby|parking|office|stairs|gate)\s*\d*)", text, re.I)
    if m:
        args["l"] = m.group(1).strip()
    elif asked == "l" and text.strip():
        args["l"] = text.strip().rstrip(".")[:60]
    m = re.search(r"\b((?:around |at |about )?\d{1,2}(?::\d{2})?\s?(?:am|pm|a\.m\.|p\.m\.)|just now|"
                  r"this (?:morning|afternoon|evening)|yesterday(?: \w+)?|\d+ minutes ago|an hour ago)",
                  text, re.I)
    if m:
        args["d"] = m.group(1)
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if re.search(r"isolated|switched off|turned off|cleaned|evacuated|called|cordoned", sentence, re.I):
            args.setdefault("a", []).append(sentence.strip().rstrip(".")[:60])
    return args


class Session:
    def __init__(self, use_voice: bool):
        self.use_voice = use_voice
        self.lock = threading.Lock()
        self.audio: dict[int, bytes] = {}
        self.reports: dict[str, dict] = {}
        self.turn_id = 0
        self.reset()

    def reset(self) -> dict:
        self.form, self.state, self.asked, self.turns = empty_form(), "collecting", None, []
        return self.result("", "Hi. Tell me what happened.", None, {}, {})

    def missing(self) -> list[str]:
        return [n for n in MANDATORY if self.form.get(n) in (None, "", [])]

    def readback(self) -> str:
        f = self.form
        dt = (f["date_time"] or {}).get("phrase", "")
        return (f"Here's what I have: {(f['incident_type'] or '').replace('_', ' ')} "
                f"{f['location'] or ''} {dt}, {f['severity'] or ''} severity. "
                "Say submit, or tell me what to change.")

    def result(self, transcript, say, tool_call, timings, devices, dropped=None, error="") -> dict:
        self.turn_id += 1
        tid = self.turn_id
        audio_url, tts_ms = None, None
        if self.use_voice and say:
            t = voice.speak(say)
            tts_ms = t.wall_ms
            devices = {**devices, "tts": t.device}
            if t.wav:
                self.audio[tid] = t.wav
                audio_url = f"/api/audio/{tid}.wav"
            elif not error:
                error = t.error
        timings = {"stt": None, "llm_prefill": None, "llm_decode": None, "llm_wall": None,
                   "code": None, "tts": tts_ms, "total": None, **timings}
        self.last = {"turn_id": tid, "state": self.state, "transcript": transcript, "say": say,
                     "audio_url": audio_url, "asked": self.asked, "form": json.loads(json.dumps(self.form)),
                     "missing": self.missing(), "tool_call": tool_call, "dropped": dropped or [],
                     "report_id": self.form.get("_report_id"), "timings_ms": timings,
                     "devices": {"stt": None, "llm": "mock", "tts": None, **devices}, "error": error}
        self.last["form"].pop("_report_id", None)
        return self.last

    def turn(self, text: str | None, wav: bytes | None) -> dict:
        t0 = time.perf_counter()
        timings, devices = {}, {}
        if wav is not None:
            if not self.use_voice:
                return self.result("", "Voice is off on this server; type instead.", None, {}, {},
                                   error="voice disabled (--no-voice)")
            vocab = VOCAB_FILE.read_text(encoding="utf-8") if VOCAB_FILE.exists() else ""
            r = voice.transcribe(wav, prompt=vocab)
            timings["stt"], devices["stt"] = r.wall_ms, r.device
            if r.error or not r.text:
                return self.result("", "Sorry, I didn't catch that.", None, timings, devices,
                                   error=r.error)
            text = r.text
        text = (text or "").strip()
        if not text:
            return self.result("", "Sorry, I didn't catch that.", None, timings, devices)
        low = text.lower()
        if re.search(r"\b(cancel|discard)\b", low):
            tool = {"tool": "cancel_report", "args": {}}
            self.state, say = "cancelled", "Okay, I've discarded this report."
        elif re.search(r"\bsubmit\b", low):
            tool = {"tool": "submit_report", "args": {}}
            if self.missing():
                field = next(f for f in FIELDS if f["name"] == self.missing()[0])
                self.asked, say = field["key"], f"I still need the {field['name'].replace('_', ' ')}. {field['ask']}"
            else:
                rid = f"IR-{len(self.reports) + 1:04d}"
                self.state, self.form["_report_id"] = "submitted", rid
                self.reports[rid] = {"id": rid, **{k: v for k, v in self.form.items() if k != "_report_id"},
                                     "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                     "turns": self.turns}
                say = f"Report {rid} submitted."
        else:
            args = fake_extract(text, self.asked)
            tool = {"tool": "update_report" if args else "no_op", "args": args}
            for k, v in args.items():
                name = BY_KEY[k]["name"]
                if k == "d":
                    self.form[name] = {"phrase": v, "iso": None, "resolved": False}
                elif isinstance(v, list):
                    self.form[name] = (self.form[name] or []) + v
                else:
                    self.form[name] = v
            self.form["description"] = (self.form["description"] + " " + text).strip()
            if self.form["incident_type"]:
                self.form["follow_up"] = FOLLOW_UP.get(self.form["incident_type"], ["Notify facilities team"])
            miss = self.missing()
            if miss:
                field = next(f for f in FIELDS if f["name"] == miss[0])
                self.asked, say = field["key"], field["ask"]
            else:
                self.state, self.asked, say = "review", None, self.readback()
        self.turns.append({"transcript": text, "tool_call": tool})
        timings["code"] = round((time.perf_counter() - t0) * 1000 - (timings.get("stt") or 0), 1)
        res = self.result(text, say, tool, timings, devices)
        res["timings_ms"]["total"] = round((time.perf_counter() - t0) * 1000, 1)
        return res

    def edit(self, field: str, value) -> dict:
        f = next((x for x in FIELDS if x["name"] == field), None)
        if f is None or f["kind"] == "derived":
            return self.result("", "", None, {}, {}, error=f"cannot edit {field!r}")
        if field == "date_time":
            value = {"phrase": str(value), "iso": None, "resolved": False}
        elif f["kind"] in ("text_list", "catalogue") and isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        self.form[field] = value
        return self.result("", self.readback(), {"tool": "edit", "args": {field: value}}, {}, {})


def make_handler(session: Session):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def json(self, obj, code=200):
            self.send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            p = self.path.split("?")[0]
            with session.lock:
                if p == "/api/state":
                    return self.json(session.last)
                if p == "/api/reports":
                    return self.json([{"id": r["id"], "incident_type": r["incident_type"],
                                       "location": r["location"], "submitted_at": r["submitted_at"]}
                                      for r in session.reports.values()])
                if p.startswith("/api/reports/"):
                    r = session.reports.get(p.rsplit("/", 1)[1])
                    return self.json(r) if r else self.json({"error": "not found"}, 404)
                if p.startswith("/api/audio/"):
                    m = re.fullmatch(r"/api/audio/(\d+)\.wav", p)
                    wav = session.audio.get(int(m.group(1))) if m else None
                    return self.send(200, wav, "audio/wav") if wav else self.send(404, b"", "text/plain")
                if p == "/api/proof":
                    return self.json({"llm": None, "stt": None, "tts": None,
                                      "mock": "ui_dev/mock_api.py: no model, nothing to prove"})
            target = (UI / (p.lstrip("/") or "index.html")).resolve()
            if UI.resolve() not in target.parents or not target.is_file():
                return self.send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send(200, target.read_bytes(), ctype)

        def do_POST(self):
            p = self.path.split("?")[0]
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            with session.lock:
                if p == "/api/turn":
                    if ctype == "audio/wav":
                        return self.json(session.turn(None, body))
                    try:
                        return self.json(session.turn(json.loads(body).get("text", ""), None))
                    except ValueError:
                        return self.json({"error": "body must be audio/wav or JSON"}, 400)
                if p == "/api/edit":
                    d = json.loads(body or b"{}")
                    return self.json(session.edit(d.get("field", ""), d.get("value")))
                if p == "/api/reset":
                    return self.json(session.reset())
            self.json({"error": "not found"}, 404)
    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    session = Session(use_voice=not a.no_voice)
    print(f"MOCK API (fake extraction, real voice={not a.no_voice}) on http://localhost:{a.port}",
          flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(session)).serve_forever()


if __name__ == "__main__":
    main()
