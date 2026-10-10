"""
Word error rate and latency of the STT engine on our own recordings (Track B, judging criterion E3).

Reads the turns in eval/cases.jsonl (Track A writes them; the reference text is each turn's `say`)
and the recordings in eval/audio/<case_id>_t<turn>_<speaker>.wav (16 kHz mono 16-bit; turn
numbers start at 1). Sends each file through app.voice.transcribe, exactly as the dialog does.

  .venv/Scripts/python eval/wer.py --label LAPTOP
  .venv/Scripts/python eval/wer.py --label BOARD --vocab data/vocab.txt --repeat 2

--label is required (BOARD or LAPTOP): a number without one is not reported. The STT engine and
model come from STT_URL / STT_DEVICE / STT_MODEL, as for the app. Results go to
results/wer_<label>_<model>_<laptop time>.json (results/ is gitignored).

Normalisation before scoring (the same for reference and hypothesis): lowercase, "a.m."/"p.m."
-> "am"/"pm", digits split from letters ("10am" -> "10 am"), punctuation dropped except
apostrophes inside words. Numbers are not spelled out, so "ten" vs "10" counts as an error.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import voice  # noqa: E402

AUDIO_NAME = re.compile(r"^(?P<case>[A-Za-z0-9-]+)_t(?P<turn>\d+)_(?P<speaker>[A-Za-z0-9-]+)\.wav$")


def normalise(text: str) -> list[str]:
    t = text.lower()
    t = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", t)
    t = re.sub(r"(\d)([a-z])", r"\1 \2", t)
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)
    t = re.sub(r"(?<![a-z0-9])'|'(?![a-z0-9])", " ", t)
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return t.split()


def edit_counts(ref: list[str], hyp: list[str]) -> dict:
    """Levenshtein alignment over words: substitutions, deletions, insertions."""
    rows, cols = len(ref) + 1, len(hyp) + 1
    d = [[(0, 0, 0, 0)] * cols for _ in range(rows)]   # (cost, sub, del, ins)
    for i in range(1, rows):
        d[i][0] = (i, 0, i, 0)
    for j in range(1, cols):
        d[0][j] = (j, 0, 0, j)
    for i in range(1, rows):
        for j in range(1, cols):
            if ref[i - 1] == hyp[j - 1]:
                d[i][j] = d[i - 1][j - 1]
                continue
            c, s, de, ins = d[i - 1][j - 1]
            best = (c + 1, s + 1, de, ins)
            c, s, de, ins = d[i - 1][j]
            best = min(best, (c + 1, s, de + 1, ins))
            c, s, de, ins = d[i][j - 1]
            best = min(best, (c + 1, s, de, ins + 1))
            d[i][j] = best
    _, s, de, ins = d[-1][-1]
    return {"sub": s, "del": de, "ins": ins, "ref_words": len(ref)}


def wer(counts: dict) -> float | None:
    n = counts["ref_words"]
    return None if n == 0 else (counts["sub"] + counts["del"] + counts["ins"]) / n


def load_references(cases_file: Path) -> dict[tuple[str, int], str]:
    refs = {}
    for line in cases_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        for n, turn in enumerate(case.get("turns", []), start=1):
            if turn.get("say"):
                refs[(case["id"], n)] = turn["say"]
    return refs


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


def run(cases_file: Path, audio_dir: Path, label: str, vocab: str, repeat: int) -> dict:
    refs = load_references(cases_file)
    rows, skipped = [], []
    for wav_path in sorted(audio_dir.glob("*.wav")):
        m = AUDIO_NAME.match(wav_path.name)
        if not m:
            skipped.append(f"{wav_path.name}: name is not <case>_t<turn>_<speaker>.wav")
            continue
        key = (m["case"], int(m["turn"]))
        if key not in refs:
            skipped.append(f"{wav_path.name}: no turn {key[1]} in case {key[0]}")
            continue
        audio = wav_path.read_bytes()
        for attempt in range(1, repeat + 1):
            r = voice.transcribe(audio, prompt=vocab)
            c = edit_counts(normalise(refs[key]), normalise(r.text))
            rows.append({"file": wav_path.name, "case": key[0], "turn": key[1],
                         "speaker": m["speaker"], "attempt": attempt, "ref": refs[key],
                         "hyp": r.text, **c, "wer": wer(c), "wall_ms": r.wall_ms,
                         "stt_ms": r.stt_ms, "device": r.device, "model": r.model, "error": r.error})

    def summary(sel: list[dict]) -> dict:
        ok = [r for r in sel if not r["error"]]
        tot = {k: sum(r[k] for r in ok) for k in ("sub", "del", "ins", "ref_words")}
        walls = [r["wall_ms"] for r in ok]
        return {"files": len({r["file"] for r in sel}), "calls": len(sel),
                "errors": len(sel) - len(ok), **tot,
                "wer": None if not tot["ref_words"] else round(wer(tot), 4),
                "wall_ms_median": round(statistics.median(walls), 1) if walls else None,
                "wall_ms_p90": pct(walls, 0.9)}

    speakers = sorted({r["speaker"] for r in rows})
    return {"label": label, "laptop_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "stt_url": voice._cfg("STT_URL", "http://localhost:8082"),
            "model": rows[0]["model"] if rows else voice._cfg("STT_MODEL", "ggml-base.en.bin"),
            "device": rows[0]["device"] if rows else voice._cfg("STT_DEVICE", "laptop"),
            "vocab_prompt": bool(vocab), "repeat": repeat,
            "overall": summary(rows),
            "by_speaker": {s: summary([r for r in rows if r["speaker"] == s]) for s in speakers},
            "skipped": skipped, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="STT word error rate on eval/audio")
    ap.add_argument("--label", required=True, choices=["BOARD", "LAPTOP"])
    ap.add_argument("--cases", default=str(ROOT / "eval" / "cases.jsonl"))
    ap.add_argument("--audio", default=str(ROOT / "eval" / "audio"))
    ap.add_argument("--vocab", default="", help="file passed to Whisper as the prompt")
    ap.add_argument("--repeat", type=int, default=1, help="calls per file")
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args(argv)
    cases, audio = Path(a.cases), Path(a.audio)
    if not cases.is_file():
        print(f"no cases file: {cases}", file=sys.stderr)
        return 2
    if not audio.is_dir():
        print(f"no audio folder: {audio}", file=sys.stderr)
        return 2
    vocab = Path(a.vocab).read_text(encoding="utf-8") if a.vocab else ""
    res = run(cases, audio, a.label, vocab, a.repeat)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out / f"wer_{a.label}_{Path(res['model']).stem}_{stamp}.json"
    path.write_text(json.dumps(res, indent=1), encoding="utf-8")
    o = res["overall"]
    print(f"[{a.label}] {res['model']} on {res['device']}: WER {o['wer']} over {o['ref_words']} words, "
          f"{o['files']} files, {o['errors']} errors; wall median {o['wall_ms_median']} ms, "
          f"p90 {o['wall_ms_p90']} ms")
    for s, v in res["by_speaker"].items():
        print(f"  {s}: WER {v['wer']} ({v['files']} files)")
    for s in res["skipped"]:
        print(f"  skipped {s}")
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
