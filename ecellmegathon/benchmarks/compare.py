"""
Compare benchmark runs side by side: every eval_BOARD_*.json under the given folders (default: every
folder in benchmarks/), with the settings from its .meta.json.

  .venv/Scripts/python benchmarks/compare.py
  .venv/Scripts/python benchmarks/compare.py benchmarks/2026-10-11_board_SM8750P --md out.md

A setting that isn't recorded shows as "?", never a guess. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _s in (sys.stdout, sys.stderr):   # the Windows console cannot show every character
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
FIELDS = ("t", "s", "l", "d", "a", "e", "p", "w")


def load(folders: list[Path]) -> list[dict]:
    runs = []
    for folder in folders:
        for f in sorted(Path(folder).glob("eval_BOARD_*.json")):
            if f.name.endswith(".meta.json"):
                continue
            res = json.loads(f.read_text(encoding="utf-8"))
            mf = f.with_name(f.stem + ".meta.json")
            meta = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {}
            runs.append({"file": f.name, "folder": Path(folder).name, "res": res, "meta": meta})
    runs.sort(key=lambda r: (r["meta"].get("started") or r["res"].get("laptop_time") or ""))
    return runs


def pct(v) -> str:
    return "-" if v is None else f"{100 * v:.1f}%"


def num(v, unit="") -> str:
    return "-" if v is None else f"{v:,.0f}{unit}"


def row(r: dict) -> dict:
    res, m = r["res"], r["meta"]
    tf, fr = res.get("teacher_forced") or {}, res.get("free_running") or {}
    lat = tf.get("latency_ms") or {}
    med = lambda k: (lat.get(k) or {}).get("median")  # noqa: E731
    fp = m.get("fast_path", res.get("fast_path"))
    temp = m.get("temperature") or {}
    cool = m.get("cool_down") or {}
    return {
        "run": f"{m.get('tag', '?')} #{m.get('run', '?')}",
        "commit": (m.get("git") or {}).get("commit", "?"),
        "ctx": m.get("ctx", "?"),
        "fast path": "?" if fp is None else ("on" if fp else "off"),
        "tool": pct(tf.get("tool_accuracy")),
        "field (turn)": pct(tf.get("field_accuracy")),
        "field (dialog)": pct(fr.get("final_field_accuracy")),
        "ended right": fr.get("ended_right", "-"),
        "exact": fr.get("dialogs_exact", "-"),
        "extras": tf.get("turns_with_extra", "-"),
        # With the fast path off every turn goes to the LLM, so the count is the number of turns scored.
        "LLM calls": tf["llm_calls"] if "llm_calls" in tf else (len(res.get("turns") or []) if fp is False else "?"),
        "by code": tf.get("answered_by_code", "-"),
        "LLM ms": num(med("wall_ms")),
        "prefill ms": num(med("prefill_ms")),
        "decode ms": num(med("decode_ms")),
        "NPU C start->peak": f"{num(cool.get('npu_c'))}->{num(temp.get('peak_npu_c'))}",
        "crash": "-" if not m else ("yes" if m.get("board_log_crash_lines") or not m.get("server_alive_after", True)
                                    else "no"),
    }


def table(runs: list[dict]) -> str:
    if not runs:
        return "(no eval_BOARD_*.json found)"
    rows = [row(r) for r in runs]
    cols = list(rows[0])
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    out += ["", "Per field, teacher-forced (correct / gold):", "",
            "| run | " + " | ".join(FIELDS) + " |", "|" + "---|" * (len(FIELDS) + 1)]
    for r, rr in zip(runs, rows):
        bf = (r["res"].get("teacher_forced") or {}).get("by_field") or {}
        out.append(f"| {rr['run']} | " + " | ".join(bf.get(k, "-") for k in FIELDS) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="compare benchmark runs")
    ap.add_argument("folders", nargs="*")
    ap.add_argument("--md", help="also write the table to this markdown file")
    a = ap.parse_args()
    folders = [Path(f) for f in a.folders] or sorted(p for p in (ROOT / "benchmarks").iterdir() if p.is_dir())
    md = table(load(folders))
    print(md)
    if a.md:
        Path(a.md).write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
