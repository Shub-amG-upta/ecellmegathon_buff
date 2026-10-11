"""
Wake-word server for app/voice.py spot(): POST /kws (audio/wav, a short clip) -> {"detected", "keyword"}.

Wraps sherpa-onnx's `sherpa-onnx-keyword-spotter` with an English streaming zipformer KWS model
(sherpa-onnx-kws-zipformer-gigaspeech-3.3M-*, ~3 MB). Same shape as voice/tts_server.py: one process per clip,
two modes, same HTTP API:

  local   run the program on this machine (development; device label "laptop")
  adb     push the clip to the QIDK and run the program there with `adb shell` (the model runs on the board's
          CPU; only this thin wrapper runs on the laptop, because the board has no Python)

The keywords file holds the wake phrase as the model's own tokens. Make it once from the model folder
(needs `pip install sherpa-onnx sentencepiece`, laptop only):

  printf 'HEY QUALCOMM @HEY_QUALCOMM\\nHI QUALCOMM @HEY_QUALCOMM\\n' > keywords_raw.txt
  sherpa-onnx-cli text2token --tokens tokens.txt --tokens-type bpe --bpe-model bpe.model \\
      keywords_raw.txt keywords.txt

  python -m voice.kws_server --mode local --bin <sherpa-onnx-keyword-spotter> --model-dir <kws model folder>
  python -m voice.kws_server --mode adb    (board folder default /data/local/tmp/kws)

GET /health   {"status": "ok", ...} when the program, model and keywords files are present.
Port 8085. Stdlib only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import posixpath
import re
import shlex
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEVICE_DIR = "/data/local/tmp/kws"
DEFAULT_MODEL = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
MAX_CLIP_BYTES = 16000 * 2 * 10 + 44          # 10 s of 16 kHz mono 16-bit: a wake clip is ~1-3 s


def clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def model_files(names: list[str], model_dir: str, join=os.path.join, int8: bool = False) -> dict:
    """encoder / decoder / joiner / tokens / keywords paths from the folder's file names. Prefers the int8
    files when int8 (smaller, faster on the CPU), else the float ones."""
    def pick(part: str) -> str:
        cands = sorted(n for n in names if n.endswith(".onnx") and part in n)
        q = [n for n in cands if ".int8." in n]
        f = [n for n in cands if ".int8." not in n]
        best = (q or f) if int8 else (f or q)
        return join(model_dir, best[0]) if best else ""
    return {"encoder": pick("encoder"), "decoder": pick("decoder"), "joiner": pick("joiner"),
            "tokens": join(model_dir, "tokens.txt"), "keywords": join(model_dir, "keywords.txt")}


def kws_args(files: dict, wav: str, threads: int, threshold: float, score: float) -> list[str]:
    """Arguments of sherpa-onnx-keyword-spotter (after the program name)."""
    return [f"--tokens={files['tokens']}", f"--encoder={files['encoder']}", f"--decoder={files['decoder']}",
            f"--joiner={files['joiner']}", f"--keywords-file={files['keywords']}", f"--num-threads={threads}",
            f"--keywords-threshold={threshold}", f"--keywords-score={score}", wav]


_KEYWORD = re.compile(r'"keyword"\s*:\s*"([^"]*)"')


def detected(output: str) -> str:
    """The first keyword the program reported, "" for none. It prints one JSON object per detection
    ({"start_time": .., "keyword": "HEY QUALCOMM", ...}); an empty keyword means nothing was found."""
    for m in _KEYWORD.finditer(output or ""):
        if m.group(1).strip():
            return m.group(1).strip()
    return ""


@dataclass
class Spot:
    keyword: str = ""
    error: str = ""
    log: str = ""


class LocalRunner:
    device = "laptop"

    def __init__(self, bin_path: str, model_dir: str, threads: int = 1, threshold: float = 0.25,
                 score: float = 1.0, int8: bool = True, keywords: str = ""):
        self.bin = bin_path
        names = [Path(p).name for p in glob.glob(os.path.join(model_dir, "*"))]
        self.files = model_files(names, model_dir, int8=int8)
        if keywords:
            self.files["keywords"] = keywords
        self.model = Path(model_dir).name
        self.threads, self.threshold, self.score = threads, threshold, score

    def health(self) -> list[str]:
        return [f"missing: {p}" for p in [self.bin, *self.files.values()] if not p or not os.path.exists(p)]

    def spot(self, wav: bytes) -> Spot:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clip.wav")
            Path(path).write_bytes(wav)
            try:
                p = subprocess.run([self.bin, *kws_args(self.files, path, self.threads, self.threshold, self.score)],
                                   capture_output=True, text=True, timeout=30, env=clean_env())
            except (OSError, subprocess.TimeoutExpired) as e:
                return Spot(error=str(e))
        log = (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0:
            return Spot(error=f"exit {p.returncode}: {log.strip()[-300:]}", log=log)
        return Spot(keyword=detected(log), log=log)


class AdbRunner:
    """Runs the spotter on the QIDK. Layout pushed by board/voice_up.sh (KWS_MODEL set):
    <dir>/bin/sherpa-onnx-keyword-spotter, <dir>/lib/*.so, <dir>/<model>/ (with keywords.txt)."""
    device = "cpu"

    def __init__(self, device_dir: str = DEVICE_DIR, model: str = DEFAULT_MODEL, threads: int = 1,
                 threshold: float = 0.25, score: float = 1.0, int8: bool = True, serial: str = "",
                 timeout_s: float = 20):
        self.dir, self.serial, self.timeout_s = device_dir.rstrip("/"), serial, timeout_s
        self.model = model
        self.model_dir = f"{self.dir}/{model}"
        self.threads, self.threshold, self.score, self.int8 = threads, threshold, score, int8
        self.clip = f"{self.dir}/clip.wav"
        self.files: dict = {}

    def _adb(self, *args: str) -> subprocess.CompletedProcess:
        cmd = ["adb"] + (["-s", self.serial] if self.serial else []) + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s, env=clean_env())

    def _files(self) -> dict:
        if not self.files:
            p = self._adb("shell", f"ls {shlex.quote(self.model_dir)}")
            self.files = model_files(p.stdout.split(), self.model_dir, join=posixpath.join, int8=self.int8)
        return self.files

    def command(self) -> str:
        args = " ".join(shlex.quote(a) for a in kws_args(self._files(), self.clip, self.threads, self.threshold,
                                                         self.score))
        return f"cd {self.dir} && LD_LIBRARY_PATH={self.dir}/lib ./bin/sherpa-onnx-keyword-spotter {args}"

    def health(self) -> list[str]:
        try:
            paths = [f"{self.dir}/bin/sherpa-onnx-keyword-spotter", *self._files().values()]
            p = self._adb("shell", " ; ".join(f"[ -e {shlex.quote(x)} ] || echo missing: {x}" for x in paths))
        except (OSError, subprocess.TimeoutExpired) as e:
            return [f"adb: {e}"]
        if p.returncode != 0:
            return [f"adb: {(p.stderr or p.stdout).strip()[:200]}"]
        return [ln for ln in p.stdout.splitlines() if ln.startswith("missing")] + \
               [f"no {k} .onnx in {self.model_dir}" for k in ("encoder", "decoder", "joiner") if not self.files[k]]

    def spot(self, wav: bytes) -> Spot:
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "clip.wav")
            Path(local).write_bytes(wav)
            try:
                pushed = self._adb("push", local, self.clip)
                if pushed.returncode != 0:
                    return Spot(error=f"adb push: {(pushed.stderr or pushed.stdout).strip()[:200]}")
                p = self._adb("shell", self.command())
            except subprocess.TimeoutExpired:
                return Spot(error=f"board spotter exceeded {self.timeout_s}s")
            except OSError as e:
                return Spot(error=f"adb: {e}")
        log = (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0:
            return Spot(error=f"board exit {p.returncode}: {log.strip()[-300:]}", log=log)
        return Spot(keyword=detected(log), log=log)


def make_handler(runner, lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code: int, obj: dict):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-KWS-Device", runner.device)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/") != "/health":
                return self._json(404, {"error": "not found"})
            problems = runner.health()
            self._json(200 if not problems else 503, {"status": "ok" if not problems else "error",
                                                      "problems": problems, "device": runner.device,
                                                      "model": runner.model})

        def do_POST(self):
            if self.path.rstrip("/") != "/kws":
                return self._json(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_CLIP_BYTES:
                return self._json(413, {"error": "clip longer than 10 s"})
            wav = self.rfile.read(n)
            if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
                return self._json(400, {"error": "body must be a WAV file"})
            with lock:                         # one clip at a time; the board's CPU is shared with STT and TTS
                s = runner.spot(wav)
            if s.error:
                return self._json(500, {"error": s.error})
            self._json(200, {"detected": bool(s.keyword), "keyword": s.keyword})
    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["local", "adb"], required=True)
    ap.add_argument("--port", type=int, default=8085)
    ap.add_argument("--bin", help="local: path to sherpa-onnx-keyword-spotter(.exe)")
    ap.add_argument("--model-dir", help="local: the KWS model folder")
    ap.add_argument("--keywords", default="", help="local: keywords file (default <model-dir>/keywords.txt)")
    ap.add_argument("--device-dir", default=DEVICE_DIR, help="adb: folder on the board")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="adb: model folder name on the board")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--threshold", type=float, default=0.25, help="higher = fewer false wakes, more misses")
    ap.add_argument("--score", type=float, default=1.0, help="boost for the keyword's tokens")
    ap.add_argument("--float", action="store_true", help="use the float .onnx files instead of int8")
    ap.add_argument("--serial", default="")
    a = ap.parse_args(argv)
    if a.mode == "local":
        if not (a.bin and a.model_dir):
            ap.error("--mode local needs --bin and --model-dir")
        runner = LocalRunner(a.bin, a.model_dir, a.threads, a.threshold, a.score, not a.float, a.keywords)
    else:
        runner = AdbRunner(a.device_dir, a.model, a.threads, a.threshold, a.score, not a.float, a.serial)
    print(json.dumps({"mode": a.mode, "device": runner.device, "model": runner.model, "port": a.port,
                      "problems": runner.health()}), flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(runner, threading.Lock()))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
