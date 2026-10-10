"""
TTS server for app/voice.py (Track B): POST /tts {"text": ...} -> audio/wav.

Wraps sherpa-onnx's `sherpa-onnx-offline-tts` program with a Piper (VITS) voice.
One process per request, which keeps it simple and keeps no model in memory
between turns. Two modes, same HTTP API:

  local   run the program on this machine (development; device label "laptop")
  adb     run the program ON THE QIDK with `adb shell`, then pull the WAV back
          with `adb exec-out cat`. The model runs on the board's CPU; only this
          thin HTTP wrapper runs on the laptop, because the board has no Python.

  python -m voice.tts_server --mode local --bin <path to sherpa-onnx-offline-tts>
                             --model-dir <vits-piper-... folder>
  python -m voice.tts_server --mode adb   (board paths default to /data/local/tmp/tts)

GET /health   {"status": "ok", ...} when the program and voice files are present.

Reply headers: X-TTS-Device (laptop | cpu), X-TTS-Model (voice name), and X-TTS-Ms
only when the engine printed its own elapsed time (never a guess).

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_TEXT_CHARS = 600          # a read-back is ~300 chars; longer is a caller bug
DEVICE_DIR = "/data/local/tmp/tts"
DEFAULT_VOICE = "vits-piper-en_US-lessac-medium"


def clean_env() -> dict:
    # Same rule as board/board.py: a QAIRT install exports a PYTHONPATH that breaks tools.
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def voice_files(model_dir: str, onnx: str = "", board: bool = False) -> dict:
    """The three paths a Piper voice needs, inside a sherpa-onnx `vits-piper-*` folder.
    board=True builds POSIX paths for the QIDK whatever the laptop's OS is."""
    join = posixpath.join if board else os.path.join
    base = posixpath.basename(model_dir.rstrip("/")) if board else Path(model_dir).name
    name = onnx or f"{base.removeprefix('vits-piper-')}.onnx"
    return {"model": join(model_dir, name), "tokens": join(model_dir, "tokens.txt"),
            "data_dir": join(model_dir, "espeak-ng-data")}


def tts_args(files: dict, out_wav: str, text: str, threads: int, speed: float) -> list[str]:
    """Arguments of sherpa-onnx-offline-tts (after the program name)."""
    return [f"--vits-model={files['model']}", f"--vits-tokens={files['tokens']}",
            f"--vits-data-dir={files['data_dir']}", f"--vits-length-scale={1.0 / speed:.3f}",
            f"--num-threads={threads}", f"--output-filename={out_wav}", text]


_ELAPSED = re.compile(r"Elapsed seconds:\s*([0-9]*\.?[0-9]+)")


def engine_ms(output: str) -> float | None:
    """sherpa-onnx prints 'Elapsed seconds: 0.412 s'. None if it did not."""
    m = _ELAPSED.search(output or "")
    return round(float(m.group(1)) * 1000, 1) if m else None


@dataclass
class Synth:
    wav: bytes = b""
    engine_ms: float | None = None
    error: str = ""
    log: str = field(default="", repr=False)


class LocalRunner:
    device = "laptop"

    def __init__(self, bin_path: str, model_dir: str, onnx: str = "", threads: int = 2,
                 speed: float = 1.0, timeout_s: float = 60):
        self.bin, self.files = bin_path, voice_files(model_dir, onnx)
        self.model = Path(self.files["model"]).stem
        self.threads, self.speed, self.timeout_s = threads, speed, timeout_s

    def health(self) -> list[str]:
        missing = [p for p in [self.bin, *self.files.values()] if not os.path.exists(p)]
        return [f"missing: {p}" for p in missing]

    def synth(self, text: str) -> Synth:
        fd, out = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            p = subprocess.run([self.bin, *tts_args(self.files, out, text, self.threads, self.speed)],
                               capture_output=True, text=True, timeout=self.timeout_s,
                               env=clean_env())
            log = (p.stdout or "") + (p.stderr or "")
            wav = Path(out).read_bytes() if os.path.exists(out) else b""
            if p.returncode != 0 or not wav:
                return Synth(error=f"exit {p.returncode}: {log.strip()[-300:]}", log=log)
            return Synth(wav=wav, engine_ms=engine_ms(log), log=log)
        except subprocess.TimeoutExpired:
            return Synth(error=f"engine exceeded {self.timeout_s}s")
        except OSError as e:
            return Synth(error=f"cannot run {self.bin}: {e}")
        finally:
            try:
                os.remove(out)
            except OSError:
                pass


class AdbRunner:
    """Runs the engine on the QIDK. Layout pushed by board/voice_up.sh:
    <dir>/bin/sherpa-onnx-offline-tts, <dir>/lib/*.so, <dir>/<voice>/."""
    device = "cpu"

    def __init__(self, device_dir: str = DEVICE_DIR, voice: str = DEFAULT_VOICE, onnx: str = "",
                 threads: int = 2, speed: float = 1.0, serial: str = "", timeout_s: float = 60):
        self.dir, self.serial = device_dir.rstrip("/"), serial
        self.files = voice_files(f"{self.dir}/{voice}", onnx, board=True)
        self.model = posixpath.basename(self.files["model"]).removesuffix(".onnx")
        self.out = f"{self.dir}/out.wav"
        self.threads, self.speed, self.timeout_s = threads, speed, timeout_s

    def _adb(self, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
        cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + list(args)
        return subprocess.run(cmd, capture_output=True, text=not binary,
                              timeout=self.timeout_s, env=clean_env())

    def command(self, text: str) -> str:
        """The board-side shell line. Every argument is quoted for the board's sh."""
        args = " ".join(shlex.quote(a) for a in tts_args(self.files, self.out, text,
                                                         self.threads, self.speed))
        return (f"cd {self.dir} && rm -f {self.out} && LD_LIBRARY_PATH={self.dir}/lib "
                f"./bin/sherpa-onnx-offline-tts {args}")

    def health(self) -> list[str]:
        paths = [f"{self.dir}/bin/sherpa-onnx-offline-tts", *self.files.values()]
        try:
            p = self._adb("shell", " ; ".join(f"[ -e {shlex.quote(x)} ] || echo missing: {x}"
                                              for x in paths))
        except (OSError, subprocess.TimeoutExpired) as e:
            return [f"adb: {e}"]
        if p.returncode != 0:
            return [f"adb: {(p.stderr or p.stdout).strip()[:200]}"]
        return [ln for ln in p.stdout.splitlines() if ln.startswith("missing")]

    def synth(self, text: str) -> Synth:
        try:
            p = self._adb("shell", self.command(text))
            log = (p.stdout or "") + (p.stderr or "")
            if p.returncode != 0:
                return Synth(error=f"board exit {p.returncode}: {log.strip()[-300:]}", log=log)
            pulled = self._adb("exec-out", f"cat {self.out}", binary=True)
            wav = pulled.stdout or b""
            if pulled.returncode != 0 or wav[:4] != b"RIFF":
                return Synth(error=f"could not pull {self.out} from the board", log=log)
            return Synth(wav=wav, engine_ms=engine_ms(log), log=log)
        except subprocess.TimeoutExpired:
            return Synth(error=f"board engine exceeded {self.timeout_s}s")
        except OSError as e:
            return Synth(error=f"adb: {e}")


class Cache:
    """WAVs by exact text. The engine reloads the voice on every run (~2-4 s,
    LAPTOP), but every question the assistant asks is a fixed template, so most
    turns repeat a phrase already spoken. Oldest entry goes first when full."""

    def __init__(self, max_items: int = 256):
        self.max_items, self.items = max_items, {}

    def get(self, text: str) -> bytes | None:
        return self.items.get(text)

    def put(self, text: str, wav: bytes) -> None:
        if self.max_items <= 0:
            return
        self.items.pop(text, None)
        self.items[text] = wav
        while len(self.items) > self.max_items:
            self.items.pop(next(iter(self.items)))


def schema_phrases(schema_path: str) -> list[str]:
    """The fixed questions in schema/incident_form.json (each field's `ask`)."""
    with open(schema_path, encoding="utf-8") as f:
        fields = json.load(f).get("fields", [])
    return [" ".join(x["ask"].split()) for x in fields if x.get("ask")]


def make_handler(runner, lock: threading.Lock, cache: Cache | None = None):
    cache = cache if cache is not None else Cache(0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write(f"[tts {time.strftime('%H:%M:%S')}] {fmt % args}\n")

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") != "/health":
                return self._json(404, {"error": "not found"})
            problems = runner.health()
            self._json(200 if not problems else 503,
                       {"status": "ok" if not problems else "error", "problems": problems,
                        "device": runner.device, "model": runner.model})

        def do_POST(self):
            if self.path.rstrip("/") != "/tts":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                text = json.loads(self.rfile.read(n).decode("utf-8")).get("text", "")
            except (ValueError, AttributeError):
                return self._json(400, {"error": 'body must be JSON {"text": "..."}'})
            text = " ".join(str(text).split())
            if not text:
                return self._json(400, {"error": "empty text"})
            if len(text) > MAX_TEXT_CHARS:
                return self._json(413, {"error": f"text longer than {MAX_TEXT_CHARS} chars"})
            # X-TTS-No-Cache: 1 forces a fresh synthesis (timing checks); the result is still cached.
            fresh = self.headers.get("X-TTS-No-Cache", "") == "1"
            with lock:  # one synthesis at a time; the board is shared with the LLM
                hit = None if fresh else cache.get(text)
                s = Synth(wav=hit) if hit is not None else runner.synth(text)
                if hit is None and not s.error:
                    cache.put(text, s.wav)
            if s.error:
                return self._json(500, {"error": s.error})
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(s.wav)))
            self.send_header("X-TTS-Device", runner.device)
            self.send_header("X-TTS-Model", runner.model)
            self.send_header("X-TTS-Cache", "hit" if hit is not None else "miss")
            if s.engine_ms is not None:
                self.send_header("X-TTS-Ms", str(s.engine_ms))
            self.end_headers()
            self.wfile.write(s.wav)
    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["local", "adb"], required=True)
    ap.add_argument("--port", type=int, default=8083)
    ap.add_argument("--bin", help="local: path to sherpa-onnx-offline-tts(.exe)")
    ap.add_argument("--model-dir", help="local: the vits-piper-* folder")
    ap.add_argument("--device-dir", default=DEVICE_DIR, help="adb: folder on the board")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help="adb: voice folder name on the board")
    ap.add_argument("--onnx", default="", help="voice .onnx file name if it differs from the folder")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--speed", type=float, default=1.0, help=">1 speaks faster")
    ap.add_argument("--serial", default="", help="adb: device serial")
    ap.add_argument("--cache", type=int, default=256, help="WAVs kept by text (0 = off)")
    ap.add_argument("--warm", default="", help="schema JSON whose `ask` questions are "
                    "spoken at startup, e.g. schema/incident_form.json")
    a = ap.parse_args(argv)
    if a.mode == "local":
        if not (a.bin and a.model_dir):
            ap.error("--mode local needs --bin and --model-dir")
        runner = LocalRunner(a.bin, a.model_dir, a.onnx, a.threads, a.speed)
    else:
        runner = AdbRunner(a.device_dir, a.voice, a.onnx, a.threads, a.speed, a.serial)
    problems = runner.health()
    cache = Cache(a.cache)
    warmed, failed = 0, []
    if a.warm and not problems:
        for text in schema_phrases(a.warm):
            s = runner.synth(text)
            if s.error:
                failed.append(f"{text!r}: {s.error}")
            else:
                cache.put(text, s.wav)
                warmed += 1
    print(json.dumps({"mode": a.mode, "device": runner.device, "model": runner.model,
                      "port": a.port, "problems": problems, "warmed": warmed,
                      "warm_failed": failed}), flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(runner, threading.Lock(), cache))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
