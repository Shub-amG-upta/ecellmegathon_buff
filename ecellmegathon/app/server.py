"""
The app server: the HTTP API of BUILD_PLAN §4.2, and the UI's static files.

  .venv/bin/python -m app.server                    # mock LLM (no board, no model)
  LLM=llamaserver .venv/bin/python -m app.server    # llama-server at LLM_URL

Environment:
  LLM        mock | llamaserver                     (default mock)
  LLM_URL    http://127.0.0.1:8080                  (llama-server; adb-forwarded on the board)
  LLM_LANE   npu | cpu | laptop                     (label only: where that server runs)
  LLM_MODEL  model file name, for the report and the proof panel
  PORT       8000
  VOICE      on | off   off = text only, even when app/voice.py is present
  STT_TRIM   on | off   cut silence at both ends of a clip before STT (default on)
  TTS_SPLIT  on | off   also serve the reply sentence by sentence (audio_parts); off until the UI plays them
  POSTFIX    on | off   the v4 post-model fixes (app/postfix.py; default on)
  PROMPT     1 | 2      system prompt version (2 from v5)
  ANSWER     full | compact   compact (v6): the model writes only the fields object or S/C/N
  PREFILL    on | off   write the answer's fixed start into the prompt instead of decoding it (default off)
  TYPE_MODEL <gguf>     v7: a second model decides the incident type on the first description (app/router.py);
             TYPE_MODEL_URL default http://127.0.0.1:8084 (board.board lane "type")
             TYPE_PREFILL=on (short type answer), TYPE_PARALLEL=1 (type call next to the main call): app/router.py
  LLM_CONSTRAINT  schema | gbnf   how the answer is constrained (gbnf: no whitespace; compare on the board)
  HINGLISH   0 | 1      Hindi / Hinglish speech (default 0): the UI may then choose "hi" per turn; STT gets
             language=hi + translate (Whisper must be multilingual: board/voice_up.sh HINGLISH=1), and the
             prompt gets the Hinglish rules (app/prompt.py). Replies stay English. eval/hinglish/README.md
  STT_LANGUAGE, STT_TRANSLATE   the Hindi turns' Whisper language (default hi) and translate (default 1)
  WAKE       auto | kws | stt | off   "Hey Qualcomm" detector for POST /api/wake (app/wake.py); auto = kws when
             KWS_URL is set (voice/kws_server.py on the board), else stt (the Whisper already running)

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
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import report
from app.audio import trim_silence
from app.backends import LlamaServerBackend, MockBackend
from app.dialog import Dialog
from app.form import ROOT
from app import say_hi, wake
from app.prompt import hinglish

try:                                    # Track B's module; VOICE=off runs text-only
    from app import voice
except ImportError:
    voice = None
if os.environ.get("VOICE", "on") == "off":
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


def _join(errors: str, new: str, stage: str) -> str:
    """Append one stage's error, prefixed once (voice.py may already prefix it)."""
    new = new if new.startswith(f"{stage}:") else f"{stage}: {new}"
    return f"{errors}; {new}" if errors else new


def make_backend():
    """The LLM backend from the environment; wrapped in the v7 type router when TYPE_MODEL is set."""
    main = _main_backend()
    tm = os.environ.get("TYPE_MODEL")
    if not tm or isinstance(main, MockBackend):
        return main
    from app.form import load_schema
    from app.router import TypeRouter
    types = next(f for f in load_schema()["fields"] if f.get("key") == "t")["values"]
    tb = LlamaServerBackend(url=os.environ.get("TYPE_MODEL_URL", "http://127.0.0.1:8084"), lane="npu",
                            model_file=tm, cache_prompt=True, timeout_s=30,
                            thinking=False if "qwen3" in tm.lower() else None)
    return TypeRouter(main, tb, types)


def _main_backend():
    kind = os.environ.get("LLM", "mock")
    if kind == "mock":
        return MockBackend()
    if kind == "llamaserver":
        return LlamaServerBackend(url=os.environ.get("LLM_URL", "http://127.0.0.1:8080"),
                                  lane=os.environ.get("LLM_LANE", "npu"),
                                  model_file=os.environ.get("LLM_MODEL", "unknown"),
                                  cache_prompt=True,
                                  thinking=False if "qwen3" in os.environ.get("LLM_MODEL", "").lower()
                                  else None)
    raise SystemExit(f"LLM must be mock or llamaserver, not {kind!r}")


TTS_WAIT_S = 30.0
LANGUAGES = {"en": "English", "hi": "Hindi / Hinglish"}


def stt_settings(lang: str | None) -> dict | None:
    """What a spoken turn in this UI language asks Whisper for, or None to send nothing (HINGLISH=0, or
    a client that names no language, like record mode: Whisper's launch flags apply, as before).
    English: language=en, no translation. Hindi / Hinglish: the language (STT_LANGUAGE, default hi;
    "auto" detects) and translation into English (STT_TRANSLATE, default 1), so everything after STT
    stays English."""
    if not hinglish() or lang not in LANGUAGES:
        return None
    if lang == "hi":
        language = os.environ.get("STT_LANGUAGE", "hi")
        return {"language": "hi" if language == "en" else language,
                "translate": os.environ.get("STT_TRANSLATE", "1") != "0"}
    return {"language": "en", "translate": False}


def split_sentences(text: str) -> list[str]:
    """Sentences for TTS, split after . ! ? followed by a space (no split inside "10:45 AM" or "IR-0001")."""
    return [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]


def join_wavs(wavs: list[bytes]) -> bytes:
    """One WAV from several with the same format (the TTS voice's)."""
    import io
    import wave
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        for k, data in enumerate(wavs):
            with wave.open(io.BytesIO(data)) as src:
                if k == 0:
                    dst.setparams(src.getparams())
                dst.writeframes(src.readframes(src.getnframes()))
    return out.getvalue()



# Words Whisper should expect on this turn, after the vocabulary list (the end of a prompt weighs most).
# One-word answers are where base.en slips ("My" for high, "Sub-Make" for submit; QIDK 2026-10-11).
def stt_hint(dialog) -> str:
    if dialog.state == "review":
        return "Submit. Cancel. Yes, submit it."
    if dialog.asked == "s":
        return "Low. Medium. High. Critical."
    return ""

class _TtsJob:
    """One turn's speech, being made in the background."""

    def __init__(self):
        self.done = threading.Event()
        self.wav: bytes = b""
        self.ms: float | None = None
        self.error = ""

    def finish(self, t) -> None:
        self.wav, self.ms, self.error = t.wav, (t.wall_ms if t.wav else None), t.error
        self.done.set()


class App:
    """One dialog (one user at a time, like the demo) plus the audio of each turn."""

    def __init__(self, dialog: Dialog, voice_mod=voice):
        self.dialog = dialog
        self.voice = voice_mod
        self.audio: dict = {}                          # turn id, or "<turn id>-<k>" for a sentence
        self.lock = threading.Lock()
        self.vocab = VOCAB.read_text() if VOCAB.exists() else ""
        self._voices = None                            # (when asked, the TTS server's voices)

    def hindi_replies(self) -> bool:
        """Can replies be spoken in Hindi? Only if the TTS server offers a "hi" voice (asked at most every 30 s)."""
        if self.voice is None or not hasattr(self.voice, "tts_voices"):
            return False
        now = time.monotonic()
        if self._voices is None or now - self._voices[0] > 30:
            self._voices = (now, self.voice.tts_voices())
        return "hi" in self._voices[1]

    def _speak(self, result: dict, reply: str = "en") -> dict:
        """Start speech for this turn in the background and return at once: the UI shows the form
        while TTS runs. `audio_url` waits for the whole reply (GET blocks until ready, up to TTS_WAIT_S).
        With TTS_SPLIT=on, a reply of several sentences is also served sentence by sentence
        (`audio_parts`, in order, each ready as soon as it's made), so the UI can start playing the
        first sentence while the rest is still being made. The TTS time is filled into /api/state when
        it's done and sent as X-TTS-Ms with the audio; in the turn response it is None (not measured
        yet), never a guess."""
        if self.voice is None or not result.get("say"):
            return result
        # Hindi spoken replies (the page's "Spoken replies: हिंदी"): the same reply, said in Hindi with the Hindi
        # voice. The chat text, the form and the report stay English. Without a Hindi voice: English.
        hindi = reply == "hi" and self.hindi_replies()
        spoken = say_hi.to_hindi(result["say"]) if hindi else result["say"]
        speak = (lambda text: self.voice.speak(text, voice="hi")) if hindi else self.voice.speak
        if hindi:
            result["reply_language"] = "hi"
        tid = result["turn_id"]
        parts = split_sentences(spoken) if os.environ.get("TTS_SPLIT", "off") == "on" else []
        whole = _TtsJob()
        part_jobs = [_TtsJob() for _ in parts] if len(parts) > 1 else []
        self.audio[tid] = whole
        for k, job in enumerate(part_jobs):
            self.audio[f"{tid}-{k}"] = job
        for old in [k for k in self.audio if int(str(k).split("-")[0]) < tid - 20]:
            del self.audio[old]                        # keep memory bounded
        result["audio_url"] = f"/api/audio/{tid}.wav"
        if part_jobs:
            result["audio_parts"] = [f"/api/audio/{tid}-{k}.wav" for k in range(len(part_jobs))]

        def run(say=spoken):
            t0 = time.perf_counter()
            if part_jobs:
                done, first_ms = [], None
                for sentence, job in zip(parts, part_jobs):
                    t = speak(sentence)
                    job.finish(t)
                    done.append(t)
                    if first_ms is None and t.wav:
                        first_ms = (time.perf_counter() - t0) * 1000
                wav = join_wavs([t.wav for t in done]) if all(t.wav for t in done) else b""
                err = "; ".join(t.error for t in done if t.error)
                t = SimpleNamespace(wav=wav, wall_ms=(time.perf_counter() - t0) * 1000, error=err,
                                    device=next((x.device for x in done if x.device), None))
            else:
                t, first_ms = speak(say), None
            whole.finish(t)
            with self.lock:                            # later reads of /api/state see the TTS time
                last = self.dialog.last
                if last.get("turn_id") == tid:
                    last["timings_ms"]["tts"] = t.wall_ms if t.wav else None
                    if first_ms is not None:
                        last["timings_ms"]["tts_first"] = first_ms
                    last["devices"]["tts"] = t.device
                    if not t.wav:
                        last["audio_url"] = None
                        if t.error:
                            last["error"] = _join(last["error"], t.error, "tts")

        threading.Thread(target=run, daemon=True).start()
        return result

    def turn_text(self, text: str, reply: str = "en") -> dict:
        with self.lock:
            return self._speak(self.dialog.turn(text), reply)

    def turn_wav(self, wav: bytes, lang: str | None = None, reply: str = "en") -> dict:
        with self.lock:
            if self.voice is None:
                r = self.dialog.turn("")
                r["error"] = "speech-to-text is not available (app/voice.py missing)"
                return r
            trim = {"skipped": "off"}
            if os.environ.get("STT_TRIM", "on") != "off":
                wav, trim = trim_silence(wav)
            asked = stt_settings(lang)
            prompt = "\n".join(x for x in (self.vocab, stt_hint(self.dialog)) if x)
            stt = self.voice.transcribe(wav, prompt=prompt, **(asked or {}))
            woke, text = wake.find(stt.text)              # "Hey Qualcomm, there was a fire..." -> "there was a fire..."
            r = self.dialog.turn(text)
            if woke:
                r["wake_phrase_removed"] = True
            if asked:                                  # the UI shows "translated from Hindi"
                r["stt_language"] = {"lang": lang, **asked}
            elif lang == "hi":
                r["error"] = _join(r["error"], "Hindi needs the server started with HINGLISH=1 "
                                   "(multilingual Whisper); this turn was heard as English", "stt")
            r["audio_trim"] = trim
            r["timings_ms"]["stt"] = stt.wall_ms
            if r["timings_ms"]["total"] is not None:
                r["timings_ms"]["total"] += stt.wall_ms
            r["devices"]["stt"] = stt.device
            if stt.error:
                r["error"] = _join(r["error"], stt.error, "stt")
            return self._speak(r, reply)

    def edit(self, field: str, value, reply: str = "en") -> dict:
        with self.lock:
            return self._speak(self.dialog.edit(field, value), reply)

    def reset(self, reply: str = "en") -> dict:
        with self.lock:
            return self._speak(self.dialog.reset(), reply)

    def config(self) -> dict:
        """What the UI may offer: languages (Hindi only with HINGLISH=1), whether speech works, and the
        hands-free wake word (engine "off" = no hands-free)."""
        langs = ["en", "hi"] if hinglish() else ["en"]
        return {"languages": [{"code": c, "name": LANGUAGES[c]} for c in langs],
                "voice": self.voice is not None, "replies": "en",
                "reply_languages": ["en", "hi"] if self.hindi_replies() else ["en"],
                "wake": {"engine": wake.engine(self.voice), "phrase": wake.PHRASE}}

    def wake_check(self, wav: bytes) -> dict:
        """Does this short clip start with "Hey Qualcomm"? Not under the dialog lock: it doesn't touch the
        report. kws: the board's keyword spotter; stt: Whisper + app/wake.py on the text."""
        eng = wake.engine(self.voice)
        out = {"wake": False, "engine": eng, "error": ""}
        if eng == "off":
            out["error"] = "hands-free is off (no speech-to-text, or WAKE=off)"
            return out
        if os.environ.get("STT_TRIM", "on") != "off":
            wav, _ = trim_silence(wav)
        if eng == "kws":
            k = self.voice.spot(wav)
            out.update(wake=k.detected and not k.error, keyword=k.keyword, ms=k.wall_ms, device=k.device,
                       error=k.error)
            return out
        # The phrase as a hint: the multilingual Whisper (HINGLISH=1) heard a TTS "Hey Qualcomm" as "Hey Quarkum"
        # 3/3 without it and "Hey Qualcomm" 2/2 with it, same speed; speech, noise and silence gave no false wake.
        stt = self.voice.transcribe(wav, prompt=f"{wake.PHRASE}.", **(stt_settings("en") or {}))
        hit, rest = wake.find(stt.text)
        out.update(wake=hit and not stt.error, heard=stt.text, rest=rest, ms=stt.wall_ms, device=stt.device,
                   error=stt.error)
        return out

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


def _query(path: str) -> dict:
    from urllib.parse import parse_qsl, urlsplit
    return dict(parse_qsl(urlsplit(path).query))


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {fmt % args}\n")

        def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
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
                reply = (self.headers.get("X-Reply-Language") or "en").strip().lower()   # spoken replies
                if self.path.split("?")[0] == "/api/turn":
                    if ctype in ("audio/wav", "audio/x-wav", "audio/wave"):
                        lang = self.headers.get("X-Language") or _query(self.path).get("lang")
                        return self._json(app.turn_wav(body, lang, reply))
                    return self._json(app.turn_text(json.loads(body or b"{}").get("text", ""), reply))
                if self.path == "/api/wake":
                    if ctype not in ("audio/wav", "audio/x-wav", "audio/wave"):
                        return self._json({"error": "POST /api/wake takes audio/wav"}, 400)
                    return self._json(app.wake_check(body))
                if self.path == "/api/edit":
                    req = json.loads(body)
                    return self._json(app.edit(req["field"], req["value"], reply))
                if self.path == "/api/reset":
                    return self._json(app.reset(reply))
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
            if (m := re.fullmatch(r"/api/audio/(\d+)(-\d+)?\.wav", path)):
                job = app.audio.get(f"{m.group(1)}{m.group(2)}" if m.group(2) else int(m.group(1)))
                if job is None or not job.done.wait(TTS_WAIT_S) or not job.wav:
                    return self._json({"error": (job.error if job else "") or "no audio"}, 404)
                return self._send(200, job.wav, "audio/wav",
                                  {"X-TTS-Ms": f"{job.ms:.1f}"} if job.ms is not None else None)
            if path == "/api/config":
                return self._json(app.config())
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
