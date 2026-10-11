"""
Measure the "Hey Qualcomm" wake word (rule: eval/DECISION_wake.md) through the running app's POST /api/wake.

  --pos DIR   clips that start with the phrase: wake_<n>_<speaker>.wav ("Hey Qualcomm", or "Hey Qualcomm, there
              was a fire in the lab"); every one should wake
  --neg DIR   normal speech that must not wake: e.g. the speech eval recordings (eval/RECORDING.md), the
              incident sentences said without the phrase

  .venv/bin/python eval/wake_eval.py --label BOARD --pos recordings/wake --neg recordings/speech
  (app server running with the board's STT, and KWS_URL when the keyword spotter is up)

Reports detection per speaker, false wakes, and time per check (median, p90), and saves
results/wake_<label>_<time>.json. The label is required: BOARD or LAPTOP, as for every other number.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEAKER = re.compile(r"_([A-Za-z0-9-]+)\.wav$")


def check(url: str, wav: bytes) -> tuple[dict, float]:
    req = urllib.request.Request(f"{url}/api/wake", data=wav, method="POST", headers={"Content-Type": "audio/wav"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    return out, (time.perf_counter() - t0) * 1000


def run(url: str, files: list[Path]) -> list[dict]:
    rows = []
    for f in files:
        out, ms = check(url, f.read_bytes())
        m = _SPEAKER.search(f.name)
        rows.append({"file": f.name, "speaker": m.group(1) if m else "?", "wake": bool(out.get("wake")),
                     "engine": out.get("engine"), "heard": out.get("heard"), "keyword": out.get("keyword"),
                     "error": out.get("error") or "", "ms": round(ms, 1)})
    return rows


def pct(xs: list[float], q: float) -> float | None:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--label", required=True, choices=["BOARD", "LAPTOP"])
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--pos", required=True, type=Path)
    ap.add_argument("--neg", type=Path)
    a = ap.parse_args()
    pos = run(a.url, sorted(a.pos.glob("*.wav")))
    neg = run(a.url, sorted(a.neg.glob("*.wav"))) if a.neg else []
    if not pos:
        print(f"no .wav files in {a.pos}", file=sys.stderr)
        return 1
    speakers = sorted({r["speaker"] for r in pos})
    by_speaker = {s: f"{sum(r['wake'] for r in pos if r['speaker'] == s)}/{sum(r['speaker'] == s for r in pos)}"
                  for s in speakers}
    ms = [r["ms"] for r in pos + neg]
    summary = {"label": a.label, "engine": pos[0]["engine"], "detected": f"{sum(r['wake'] for r in pos)}/{len(pos)}",
               "detected_by_speaker": by_speaker,
               "false_wakes": f"{sum(r['wake'] for r in neg)}/{len(neg)}" if neg else None,
               "check_ms_median": pct(ms, 0.5), "check_ms_p90": pct(ms, 0.9),
               "errors": sum(bool(r["error"]) for r in pos + neg)}
    print(json.dumps(summary, indent=1))
    for r in pos:
        if not r["wake"]:
            print(f"  missed {r['file']}: heard {r['heard']!r} {r['error']}")
    for r in neg:
        if r["wake"]:
            print(f"  FALSE WAKE {r['file']}: heard {r['heard']!r}")
    out = ROOT / "results" / f"wake_{a.label}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "pos": pos, "neg": neg}, indent=1))
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
