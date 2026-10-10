"""
The speech eval subset: ~40 dev turns where speech-to-text is most likely to
break extraction. Writes eval/speech_subset.jsonl, the recording list (BUILD_PLAN §4.3).

  .venv/bin/python eval/speech_subset.py

Picked by rule, not by hand, in this order (each turn once, each text once):
  demo     every turn of ps-01 (the problem statement's example)
  number   a digit or a spoken clock time ("Room 2", "Lab 4", "3:30", "quarter past two")
  name     a witness or a named person ("Mahesh", "Priya from the lab")
  accent   Indian English, fillers, informal or self-corrected speech (case tags)
  flip     one word changes the meaning ("not high, medium", "actually it was Lab 4"): if STT drops it,
           the field comes out wrong
  short    a short answer (≤ 4 words) that fills a field or a tool ("it's medium", "submit")
  long     a long narration (the densest first turns)
Dev and demo only: the locked test split is never recorded for tuning.
The first NOISY turns are also recorded in a noisy room.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "nlu" / "cases.jsonl"
OUT = HERE / "speech_subset.jsonl"

# DESIGN CHOICE: how many turns each rule may add, and how many are re-recorded in noise.
QUOTA = {"demo": 3, "number": 12, "name": 8, "accent": 6, "flip": 6, "short": 7, "long": 4}
NOISY = 10
ACCENT_TAGS = {"indian_english", "fillers", "informal", "self_correction", "indian_number"}
CLOCK_WORDS = re.compile(r"\b(o'?clock|quarter|half past)\b", re.I)
FLIP_WORDS = re.compile(r"\b(not|actually|no wait|sorry|correction|instead)\b", re.I)


def reasons(case: dict, turn: dict) -> set[str]:
    text, args = turn["user"], turn["gold"]["args"]
    r = set()
    if case["id"] == "ps-01":
        r.add("demo")
    if re.search(r"\d", text) or CLOCK_WORDS.search(text):
        r.add("number")
    if args.get("w") or any(re.search(r"\b[A-Z][a-z]+\b", p[1:]) for p in args.get("p", [])):
        r.add("name")
    if turn["i"] == 0 and ACCENT_TAGS & set(case["tags"]):
        r.add("accent")
    if FLIP_WORDS.search(text) and (args or turn["gold"]["tool"] != "no_op"):
        r.add("flip")
    if len(text.split()) <= 4 and (args or turn["gold"]["tool"] != "no_op"):
        r.add("short")
    if turn["i"] == 0 and len(text.split()) >= 30:
        r.add("long")
    return r


def build() -> list[dict]:
    cases = [json.loads(ln) for ln in CASES.read_text().splitlines() if ln.strip()]
    pool = [(c, t, reasons(c, t)) for c in cases if c["split"] in ("dev", "demo") for t in c["turns"]]
    picked, seen_text = [], set()
    for rule in QUOTA:
        n = 0
        for c, t, why in pool:
            key = (c["id"], t["i"])
            norm = t["user"].lower().strip(" .!")
            if rule not in why or n >= QUOTA[rule] or norm in seen_text or key in {p["_key"] for p in picked}:
                continue
            picked.append({"_key": key, "case": c["id"], "turn": t["i"] + 1, "say": t["user"],
                           "why": sorted(why)})
            seen_text.add(norm)
            n += 1
    for k, p in enumerate(picked):
        del p["_key"]
        p["noisy"] = k < NOISY
    return picked


if __name__ == "__main__":
    rows = build()
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    counts = {}
    for r in rows:
        for w in r["why"]:
            counts[w] = counts.get(w, 0) + 1
    print(f"wrote {len(rows)} turns to {OUT} ({sum(r['noisy'] for r in rows)} also recorded noisy)")
    print("turns per reason (a turn can have several):", counts)
