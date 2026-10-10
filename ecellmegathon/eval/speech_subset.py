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
The first NOISY turns picked are also recorded in a noisy room. The list is written
case by case, and eval/RECORDING_SCRIPT.md (the reading script) is generated from it.
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
    # read case by case: cases in the order they were first picked, turns in order
    rank = {}
    for p in picked:
        rank.setdefault(p["case"], len(rank))
    return sorted(picked, key=lambda p: (rank[p["case"]], p["turn"]))


def script_md(rows: list[dict]) -> str:
    """The reading script, case by case (eval/RECORDING_SCRIPT.md)."""
    import sys
    sys.path.insert(0, str(HERE.parent))
    from app.dialog import GREETING, _TYPE_SAID
    from app.form import load_schema
    asks = {f["key"]: f["ask"] for f in load_schema()["fields"] if f.get("key")}
    cases = {c["id"]: c for c in map(json.loads, CASES.read_text().splitlines())}
    order = list(dict.fromkeys(r["case"] for r in rows))
    noisy = [r for r in rows if r["noisy"]]
    out = ["# Recording script", "",
           f"**{len(rows)} lines in {len(order)} cases.** Read every line once in a quiet room. The "
           f"{len(noisy)} lines marked 🔊 are read again in a noisy room (list at the end).",
           "How to set up, read and share: `eval/RECORDING.md`. Record with `eval/record_lines.sh <your name>`: it "
           "shows the same lines in the same order and names the files for you.", "",
           "Each case is one conversation with the assistant. Lines in *italics* are what the assistant said "
           "before; you only say the **bold** lines. Some cases skip turns: only the turns we need are recorded.",
           "", "Speaker: ________   Quiet pass ☐   Noisy pass ☐", ""]
    for n, cid in enumerate(order, 1):
        c = cases[cid]
        final = c["gold_final"]
        title = _TYPE_SAID.get(final.get("t", ""), "Incident")
        where = f" · {final['l']}" if final.get("l") else ""
        out += ["---", "", f"## Case {n} · {title}{where}", ""]
        for r in (r for r in rows if r["case"] == cid):
            t = c["turns"][r["turn"] - 1]
            if t["asked"]:
                before = asks[t["asked"]]
            elif r["turn"] == 1:
                before = GREETING
            else:
                before = "(reads the report back) Say submit, or tell me what to change."
            out += [f"**Turn {r['turn']}** · file `{cid}_t{r['turn']}_<you>.wav`" + (" · 🔊 noisy too" if r["noisy"] else ""),
                    "", f"> 🤖 *{before}*", ">", f"> 🗣️ **{r['say']}**", ""]
    out += ["---", "", "## Noisy pass: these lines again, in a noisy room", "",
            "Run `eval/record_lines.sh <your name> --noisy`. Same laptop, same distance.", ""]
    for r in noisy:
        out.append(f"- Case {order.index(r['case']) + 1}, turn {r['turn']}: **{r['say']}**")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    rows = build()
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    counts = {}
    for r in rows:
        for w in r["why"]:
            counts[w] = counts.get(w, 0) + 1
    (HERE / "RECORDING_SCRIPT.md").write_text(script_md(rows))
    print(f"wrote {len(rows)} turns to {OUT} ({sum(r['noisy'] for r in rows)} also recorded noisy)"
          f" and eval/RECORDING_SCRIPT.md")
    print("turns per reason (a turn can have several):", counts)
