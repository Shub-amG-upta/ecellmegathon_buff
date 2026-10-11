"""
The whole spoken turn as the user lives it, through the running app (BOARD or LAPTOP): POST /api/turn with a
recorded clip, then GET the reply's audio. Per turn: speech-to-text, model, code, text-to-speech, and
**time to sound** (from sending the clip until the reply's audio is ready to play), split by first descriptions,
later model turns, and turns the fast path answered.

Clips are the speech eval recordings (eval/RECORDING.md: <case>_t<turn>_<speaker>.wav, turns from 1). Each
dialog is played in order for one speaker after POST /api/reset, so every turn has its real context.

  .venv/bin/python eval/turn_time.py --label BOARD --audio <recordings folder>          # app at 127.0.0.1:8000
  .venv/bin/python eval/turn_time.py --label BOARD --audio <folder> --speaker ana --max-dialogs 10

Saves results/turn_time_<label>_<time>.json. Numbers are labelled as measured; nothing is estimated.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CLIP = re.compile(r"^(?P<case>[\w-]+)_t(?P<turn>\d+)_(?P<speaker>[\w-]+)\.wav$")


def post(url: str, path: str, body: bytes, ctype: str) -> dict:
    req = urllib.request.Request(url + path, data=body, method="POST", headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def audio_ready(url: str, audio_url: str | None) -> tuple[float | None, float | None]:
    """(ms until the reply's audio arrived, the TTS engine's own ms from X-TTS-Ms)."""
    if not audio_url:
        return None, None
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url + audio_url, timeout=120) as r:
            r.read()
            tts = r.headers.get("X-TTS-Ms")
    except urllib.error.HTTPError:
        return None, None
    return (time.perf_counter() - t0) * 1000, (float(tts) if tts else None)


def dialogs(folder: Path, speaker: str | None) -> dict:
    out: dict = defaultdict(dict)
    for f in sorted(folder.glob("*.wav")):
        m = _CLIP.match(f.name)
        if m and (not speaker or m["speaker"] == speaker):
            out[(m["case"], m["speaker"])][int(m["turn"])] = f
    return out


def stats(xs: list[float]) -> dict | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return {"n": len(xs), "median": round(st.median(xs), 1), "p90": round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 1),
            "max": round(xs[-1], 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--label", required=True, choices=["BOARD", "LAPTOP"])
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--speaker", default="")
    ap.add_argument("--max-dialogs", type=int, default=0)
    a = ap.parse_args()
    ds = dialogs(a.audio, a.speaker or None)
    if not ds:
        print(f"no <case>_t<turn>_<speaker>.wav clips in {a.audio}", file=sys.stderr)
        return 1
    rows = []
    for n, ((case, speaker), turns) in enumerate(sorted(ds.items())):
        if a.max_dialogs and n >= a.max_dialogs:
            break
        post(a.url, "/api/reset", b"{}", "application/json")
        for i in sorted(turns):
            t0 = time.perf_counter()
            r = post(a.url, "/api/turn", turns[i].read_bytes(), "audio/wav")
            reply_ms = (time.perf_counter() - t0) * 1000
            audio_ms, tts_ms = audio_ready(a.url, r.get("audio_url"))
            tm = r.get("timings_ms") or {}
            kind = "fast path" if r.get("fast_path") else ("first description" if i == 1 else "later model turn")
            rows.append({"case": case, "speaker": speaker, "turn": i, "kind": kind, "transcript": r.get("transcript"),
                         "stt_ms": tm.get("stt"), "llm_ms": tm.get("llm_wall"), "code_ms": tm.get("code"),
                         "reply_ms": round(reply_ms, 1), "tts_ms": tts_ms,
                         "time_to_sound_ms": round(reply_ms + audio_ms, 1) if audio_ms is not None else None,
                         "error": r.get("error") or ""})
            print(f"{case} t{i} {speaker}: {kind:18s} stt {tm.get('stt')} · llm {tm.get('llm_wall')} · "
                  f"reply {reply_ms:.0f} ms · sound after {rows[-1]['time_to_sound_ms']} ms", flush=True)
    summary = {"label": a.label, "turns": len(rows), "errors": sum(bool(r["error"]) for r in rows), "by_kind": {}}
    for kind in ("first description", "later model turn", "fast path", "all"):
        sel = [r for r in rows if kind == "all" or r["kind"] == kind]
        summary["by_kind"][kind] = {k: stats([r[k] for r in sel]) for k in
                                    ("stt_ms", "llm_ms", "reply_ms", "tts_ms", "time_to_sound_ms")}
    print(json.dumps(summary, indent=1))
    out = ROOT / "results" / f"turn_time_{a.label}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "turns": rows}, indent=1))
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
