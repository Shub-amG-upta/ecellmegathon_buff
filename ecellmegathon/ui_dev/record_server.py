"""
Record mode's server (Track B, BUILD_PLAN §4.5 and §7 "Speech eval"). Not app/server.py.

Serves ui/ (open /record.html), gives the page the recording list, and saves each take
straight into eval/audio/ under the name eval/wer.py reads:

  .venv/Scripts/python -m ui_dev.record_server          http://localhost:8001/record.html

  GET  /api/rec/lines?speaker=NAME   eval/speech_subset.jsonl, each line with `file` and `done`
  POST /api/rec/save?case=..&turn=..&speaker=..   body: audio/wav -> eval/audio/<case>_t<turn>_<speaker>.wav

A take is refused unless it passes app.voice.check_wav (16 kHz mono 16-bit), is at least 0.6 s,
is not silent (peak >= 300), and (case, turn) is a line of the subset: the same checks as
eval/record_lines.sh, so both recorders give the same files. Speakers are lowercase first names. Re-recording overwrites. The subset is dev-only by
construction (eval/speech_subset.py), so no test-split line can be recorded here.

Stdlib only.
"""
from __future__ import annotations

import argparse
import array
import io
import json
import mimetypes
import os
import re
import sys
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.voice import check_wav

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
SUBSET = ROOT / "eval" / "speech_subset.jsonl"
AUDIO = ROOT / "eval" / "audio"
MAX_BODY = 20 * 1024 * 1024

# Speaker names as eval/record_lines.sh takes them (lowercase first name), plus the noisy suffix.
SPEAKER = re.compile(r"^[a-z0-9]+(-noisy)?$")
MIN_SECONDS = 0.6
SILENT_PEAK = 300
NAME = re.compile(r"^(?P<case>[A-Za-z0-9-]+)_t(?P<turn>\d+)_(?P<speaker>[A-Za-z0-9-]+)\.wav$")


def recording_name(case: str, turn: int, speaker: str) -> str:
    """The file name for one take: <case>_t<turn>_<speaker>.wav (turn numbers from 1)."""
    if not SPEAKER.match(speaker or ""):
        raise ValueError("speaker: lowercase letters and digits only (e.g. sarthak)")
    if not re.fullmatch(r"[A-Za-z0-9-]+", case or "") or int(turn) < 1:
        raise ValueError(f"bad line {case!r} turn {turn!r}")
    return f"{case}_t{int(turn)}_{speaker}.wav"


def clip_problem(wav: bytes) -> str:
    """"" for a usable take, else why not. Format first (app.voice.check_wav), then the
    same length and silence limits as eval/record_lines.sh."""
    bad = check_wav(wav)
    if bad:
        return bad
    with wave.open(io.BytesIO(wav), "rb") as w:
        n = w.getnframes()
        samples = array.array("h", w.readframes(n))
    if sys.byteorder == "big":
        samples.byteswap()
    if n / 16000 < MIN_SECONDS:
        return f"too short ({n / 16000:.2f} s, need {MIN_SECONDS} s)"
    peak = max((abs(x) for x in samples), default=0)
    if peak < SILENT_PEAK:
        return f"silent (peak {peak}): check the mic and its permission"
    return ""


def noisy_speaker(speaker: str) -> str:
    """The speaker name for the noisy pass."""
    return speaker if speaker.endswith("-noisy") else f"{speaker}-noisy"


def load_lines(path: Path = SUBSET) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def lines_for(speaker: str, audio_dir: Path = AUDIO, path: Path = SUBSET) -> list[dict]:
    """The recording list for one speaker. A `-noisy` speaker gets only the noisy lines."""
    noisy = speaker.endswith("-noisy")
    out = []
    for ln in load_lines(path):
        if noisy and not ln.get("noisy"):
            continue
        name = recording_name(ln["case"], ln["turn"], speaker)
        out.append({**ln, "file": name, "done": (audio_dir / name).is_file()})
    return out


def save_take(case: str, turn: int, speaker: str, wav: bytes,
              audio_dir: Path = AUDIO, path: Path = SUBSET) -> Path:
    """Validate and write one take. Raises ValueError with the reason."""
    name = recording_name(case, turn, speaker)
    line = next((ln for ln in load_lines(path) if ln["case"] == case and ln["turn"] == int(turn)), None)
    if line is None:
        raise ValueError(f"{case} turn {turn} is not in {path.name}")
    if speaker.endswith("-noisy") and not line.get("noisy"):
        raise ValueError(f"{case} turn {turn} is not a noisy line")
    bad = clip_problem(wav)
    if bad:
        raise ValueError(f"audio rejected: {bad}")
    audio_dir.mkdir(parents=True, exist_ok=True)
    tmp = audio_dir / (name + ".part")
    tmp.write_bytes(wav)
    os.replace(tmp, audio_dir / name)
    return audio_dir / name


def make_handler(audio_dir: Path = AUDIO, subset: Path = SUBSET):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            sys.stderr.write(f"[rec {time.strftime('%H:%M:%S')}] {fmt % a}\n")

        def send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def json(self, obj, code: int = 200):
            self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/api/rec/lines":
                try:
                    return self.json({"lines": lines_for(q.get("speaker", ""), audio_dir, subset),
                                      "audio_dir": str(audio_dir)})
                except ValueError as e:
                    return self.json({"error": str(e)}, 400)
            rel = "record.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (UI / rel).resolve()
            if UI.resolve() not in f.parents or not f.is_file():
                return self.json({"error": "not found"}, 404)
            self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/api/rec/save":
                return self.json({"error": "not found"}, 404)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self.json({"error": "recording too large"}, 413)
            try:
                p = save_take(q.get("case", ""), int(q.get("turn", "0")), q.get("speaker", ""),
                              self.rfile.read(n), audio_dir, subset)
            except ValueError as e:
                return self.json({"error": str(e)}, 400)
            return self.json({"saved": p.name, "bytes": p.stat().st_size})
    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--audio-dir", default=str(AUDIO))
    a = ap.parse_args()
    print(f"record mode: http://localhost:{a.port}/record.html  (saving to {a.audio_dir})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(Path(a.audio_dir))).serve_forever()


if __name__ == "__main__":
    main()
