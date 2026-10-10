"""
The app server: the HTTP API of BUILD_PLAN §4.2, and the UI's static files.

  .venv/bin/python -m app.server                    # mock LLM (no board, no model)
  LLM=llamaserver .venv/bin/python -m app.server    # llama-server at LLM_URL

Environment:
  LLM        mock | llamaserver                     (default mock)
  LLM_URL    http://localhost:8080                  (llama-server; adb-forwarded on the board)
  LLM_LANE   npu | cpu | laptop                     (label only: where that server runs)
  LLM_MODEL  model file name, for the report and the proof panel
  PORT       8000

Speech is Track B's `app/voice.py` (§4.1). Until it exists, the server runs
text-only: WAV turns return an error and no audio is produced.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import report
from app.backends import LlamaServerBackend, MockBackend
from app.dialog import Dialog
from app.form import ROOT

try:                                    # Track B's module; optional until it lands
    from app import voice
except ImportError:
    voice = None

UI_DIR = ROOT / "ui"
VOCAB = ROOT / "data" / "vocab.txt"
MAX_BODY = 20 * 1024 * 1024             # a 60 s 16 kHz WAV is ~2 MB

_FALLBACK_PAGE = """<!doctype html><meta charset=utf-8><title>Incident dev page</title>
<body style="font:15px system-ui;max-width:760px;margin:24px auto;padding:0 16px">
<p><b>Dev page</b> (Track B's <code>ui/index.html</code> isn't there yet). Type a turn:</p>
<form onsubmit="go(event)"><input id=t style="width:75%" autofocus> <button>Send</button>
<button type=button onclick="post('/api/reset',{})">Reset</button></form>
<pre id=o style="white-space:pre-wrap;background:#f4f4f4;padding:12px"></pre>
<script>
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
const j=await r.json();document.getElementById('o').textContent=j.say+'\\n\\n'+JSON.stringify(j,null,1);}
function go(e){e.preventDefault();const t=document.getElementById('t');post('/api/turn',{text:t.value});t.value='';}
post('/api/reset',{});
</script>"""


def make_backend():
    kind = os.environ.get("LLM", "mock")
    if kind == "mock":
        return MockBackend()
    if kind == "llamaserver":
        return LlamaServerBackend(url=os.environ.get("LLM_URL", "http://localhost:8080"),
                                  lane=os.environ.get("LLM_LANE", "npu"),
                                  model_file=os.environ.get("LLM_MODEL", "unknown"),
                                  cache_prompt=True,
                                  thinking=False if "qwen3" in os.environ.get("LLM_MODEL", "").lower()
                                  else None)
    raise SystemExit(f"LLM must be mock or llamaserver, not {kind!r}")


class App:
    """One dialog (one user at a time, like the demo) plus the audio of each turn."""

    def __init__(self, dialog: Dialog, voice_mod=voice):
        self.dialog = dialog
        self.voice = voice_mod
        self.audio: dict[int, bytes] = {}
        self.lock = threading.Lock()
        self.vocab = VOCAB.read_text() if VOCAB.exists() else ""

    def _speak(self, result: dict) -> dict:
        if self.voice is None or not result.get("say"):
            return result
        t = self.voice.speak(result["say"])
        result["timings_ms"]["tts"] = t.wall_ms if t.wav else None
        result["devices"]["tts"] = t.device
        if t.wav:
            self.audio[result["turn_id"]] = t.wav
            result["audio_url"] = f"/api/audio/{result['turn_id']}.wav"
            for old in [k for k in self.audio if k < result["turn_id"] - 20]:
                del self.audio[old]                    # keep memory bounded
        elif t.error:
            result["error"] = (result["error"] + "; " if result["error"] else "") + f"tts: {t.error}"
        return result

    def turn_text(self, text: str) -> dict:
        with self.lock:
            return self._speak(self.dialog.turn(text))

    def turn_wav(self, wav: bytes) -> dict:
        with self.lock:
            if self.voice is None:
                r = self.dialog.turn("")
                r["error"] = "speech-to-text is not available (app/voice.py missing)"
                return r
            stt = self.voice.transcribe(wav, prompt=self.vocab)
            r = self.dialog.turn(stt.text)
            r["timings_ms"]["stt"] = stt.wall_ms
            if r["timings_ms"]["total"] is not None:
                r["timings_ms"]["total"] += stt.wall_ms
            r["devices"]["stt"] = stt.device
            if stt.error:
                r["error"] = (r["error"] + "; " if r["error"] else "") + f"stt: {stt.error}"
            return self._speak(r)

    def edit(self, field: str, value) -> dict:
        with self.lock:
            return self._speak(self.dialog.edit(field, value))

    def reset(self) -> dict:
        with self.lock:
            return self._speak(self.dialog.reset())

    def proof(self) -> dict:
        """NPU placement from llama-server's startup log, read over adb. Null
        fields when there's no board (laptop dev), never made-up values."""
        out = {"llm": {"lane": getattr(self.dialog.backend, "lane", "mock"),
                       "model": getattr(self.dialog.backend, "model_file", "mock"),
                       "offloaded": None, "layers": None, "htp0_model_mib": None,
                       "log_lines": [], "error": ""},
               "stt": None, "tts": None}
        if out["llm"]["lane"] != "npu":
            out["llm"]["error"] = "LLM is not on the board's NPU lane"
            return out
        try:
            from board.board import lane_log
            from board.npu_proof import placement
            log = lane_log("npu")
            out["llm"].update(placement(log))
            out["llm"]["log_lines"] = [ln for ln in log.splitlines()
                                       if re.search(r"offloaded \d+/\d+ layers|HTP0 .*buffer size", ln)]
        except Exception as e:                       # noqa: BLE001 (no adb, no board)
            out["llm"]["error"] = f"could not read the board log: {e}"
        return out


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {fmt % args}\n")

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("request body too large")
            return self.rfile.read(n) if n else b""

        def do_POST(self):
            try:
                body = self._body()
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                if self.path == "/api/turn":
                    if ctype in ("audio/wav", "audio/x-wav", "audio/wave"):
                        return self._json(app.turn_wav(body))
                    return self._json(app.turn_text(json.loads(body or b"{}").get("text", "")))
                if self.path == "/api/edit":
                    req = json.loads(body)
                    return self._json(app.edit(req["field"], req["value"]))
                if self.path == "/api/reset":
                    return self._json(app.reset())
                return self._json({"error": "not found"}, 404)
            except (ValueError, KeyError) as e:
                return self._json({"error": f"bad request: {e}"}, 400)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/state":
                return self._json(app.dialog.last)
            if path == "/api/reports":
                return self._json(report.list_reports(app.dialog.report_dir))
            if (m := re.fullmatch(r"/api/reports/(IR-\d+)", path)):
                r = report.load(m.group(1), app.dialog.report_dir)
                return self._json(r) if r else self._json({"error": "no such report"}, 404)
            if (m := re.fullmatch(r"/api/audio/(\d+)\.wav", path)):
                wav = app.audio.get(int(m.group(1)))
                return self._send(200, wav, "audio/wav") if wav else self._json({"error": "no audio"}, 404)
            if path == "/api/proof":
                return self._json(app.proof())
            return self._static(path)

        def _static(self, path: str):
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            f = (UI_DIR / rel).resolve()
            if path in ("/", "/index.html") and not f.exists():
                return self._send(200, _FALLBACK_PAGE.encode(), "text/html; charset=utf-8")
            if UI_DIR.resolve() not in f.parents or not f.is_file():
                return self._json({"error": "not found"}, 404)
            types = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                     ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
                     ".json": "application/json", ".wav": "audio/wav"}
            return self._send(200, f.read_bytes(), types.get(f.suffix, "application/octet-stream"))

    return Handler


def serve(app: App, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app = App(Dialog(make_backend()))
    print(f"LLM={os.environ.get('LLM', 'mock')}  voice={'yes' if voice else 'no (text only)'}  "
          f"http://localhost:{port}", flush=True)
    serve(app, port).serve_forever()
