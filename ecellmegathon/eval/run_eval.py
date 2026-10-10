"""
Extraction eval: run every case in eval/cases.jsonl through the real dialog
and score the model's (gated) tool calls against the hand-written gold.

  .venv/bin/python eval/run_eval.py --label LAPTOP                      # LLM=llamaserver in env
  .venv/bin/python eval/run_eval.py --label BOARD
  .venv/bin/python eval/run_eval.py --label ORACLE --backend oracle    # scorer self-check: must be 100%

--label is required and goes into every output: a number without its label
(BOARD / LAPTOP / MOCK / ORACLE) never leaves this script. Run each
configuration twice: temperature-0 replies drift between server launches.

Metrics
  tool accuracy      the right tool on every turn (update / submit / cancel / no_op)
  field accuracy     per gold field: enums exact; text by content words
                     (gold words found ≥ 0.8 and predicted words that are gold ≥ 0.5);
                     lists item by item
  extra fields       fields the model filled that the turn's gold doesn't have
                     (listed for a human to judge: gold can be incomplete)
  dropped by gate    values the gate removed
  completed          cases ending in submit that saved a report
  latency            prefill / decode / LLM wall per call; decode tokens
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.backends import LLMReply  # noqa: E402
from app.dialog import Dialog  # noqa: E402
from app.gate import _word_in, content_words  # noqa: E402

TEXT_RECALL, TEXT_PRECISION = 0.8, 0.5     # DESIGN CHOICE: text-field match, see docstring


def load_cases(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def gold_tool(turn: dict) -> str:
    return turn.get("gold_tool") or ("update_report" if turn.get("gold") else "no_op")


def text_match(pred: str, gold: str) -> bool:
    g, p = content_words(gold), content_words(pred)
    if not g or not p:
        return not g and not p
    rec = sum(_word_in(w, set(p)) for w in g) / len(g)
    prec = sum(_word_in(w, set(g)) for w in p) / len(p)
    return rec >= TEXT_RECALL and prec >= TEXT_PRECISION


def field_match(key: str, pred, gold) -> bool:
    if pred is None:
        return False
    if key in ("t", "s"):
        return pred == gold
    if isinstance(gold, list):
        pred = pred if isinstance(pred, list) else [pred]
        return (all(any(text_match(p, g) for p in pred) for g in gold)
                and all(any(text_match(p, g) for g in gold) for p in pred))
    return isinstance(pred, str) and text_match(pred, gold)


def gated_args(result: dict) -> dict:
    """The model's args minus what the gate dropped: what reached the form."""
    args = dict((result.get("tool_call") or {}).get("args") or {})
    for d in result.get("dropped", []):
        v = args.get(d["field"])
        if isinstance(v, list):
            args[d["field"]] = [x for x in v if x != d["value"]]
            if not args[d["field"]]:
                del args[d["field"]]
        elif v == d["value"]:
            del args[d["field"]]
    return args


class Oracle:
    """Answers each turn with its gold tool call. Scorer self-check only."""
    lane, model_file = "oracle", "oracle"

    def __init__(self, cases):
        self.queue = [(gold_tool(t), t.get("gold", {})) for c in cases for t in c["turns"]]

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None):
        tool, gold = self.queue.pop(0)
        call = {"tool": tool, **({"args": gold} if tool == "update_report" else {})}
        return LLMReply(text=json.dumps(call), backend="oracle", model="oracle")


def run(cases: list[dict], backend, label: str) -> dict:
    turns_out, calls = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            d = Dialog(backend, report_dir=Path(tmp))
            for i, turn in enumerate(case["turns"]):
                r = d.turn(turn["say"])
                if r["tool_call"] is not None:
                    calls.append({k: r["timings_ms"][k] for k in ("llm_prefill", "llm_decode", "llm_wall")}
                                 | {"decode_tokens": r["llm"]["decode_tokens"],
                                    "prefill_tokens": r["llm"]["prefill_tokens"]})
                pred_tool = (r["tool_call"] or {}).get("tool")
                pred = gated_args(r)
                gold = turn.get("gold", {})
                turns_out.append({
                    "case": case["id"], "turn": i, "say": turn["say"],
                    "gold_tool": gold_tool(turn), "pred_tool": pred_tool,
                    "fields": {k: {"gold": v, "pred": pred.get(k), "ok": field_match(k, pred.get(k), v)}
                               for k, v in gold.items()},
                    "extra": {k: v for k, v in pred.items() if k not in gold},
                    "dropped": r["dropped"], "error": r["error"], "state": r["state"]})
            turns_out[-1]["final_state"] = d.state
            turns_out[-1]["final_missing"] = [f["name"] for f in d.form.missing()]

    fields = [f for t in turns_out for f in t["fields"].items()]
    by_key: dict = {}
    for k, f in fields:
        by_key.setdefault(k, []).append(f["ok"])
    want_submit = [c["id"] for c in cases if gold_tool(c["turns"][-1]) == "submit_report"]
    done = [t["case"] for t in turns_out if t.get("final_state") == "submitted"]

    def stat(key):
        v = [c[key] for c in calls if c[key] is not None]
        return {"mean": round(statistics.mean(v), 1), "median": round(statistics.median(v), 1),
                "n": len(v)} if v else None

    return {
        "label": label, "laptop_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": getattr(backend, "model_file", "?"), "lane": getattr(backend, "lane", "?"),
        "n_cases": len(cases), "n_turns": len(turns_out), "n_llm_calls": len(calls),
        "tool_accuracy": round(sum(t["gold_tool"] == t["pred_tool"] for t in turns_out) / len(turns_out), 3),
        "field_accuracy": round(sum(f["ok"] for _, f in fields) / len(fields), 3) if fields else None,
        "field_accuracy_by_key": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_key.items())},
        "extra_fields": sum(len(t["extra"]) for t in turns_out),
        "dropped_by_gate": sum(len(t["dropped"]) for t in turns_out),
        "completed": f"{len(set(done) & set(want_submit))}/{len(want_submit)}",
        "latency_ms": {k: stat(k) for k in ("llm_prefill", "llm_decode", "llm_wall")},
        "tokens": {k: stat(k) for k in ("prefill_tokens", "decode_tokens")},
        "turns": turns_out,
    }


def print_summary(res: dict) -> None:
    print(f"\n[{res['label']}] model {res['model']} on {res['lane']} · {res['laptop_time']}")
    print(f"  cases {res['n_cases']} · turns {res['n_turns']} · LLM calls {res['n_llm_calls']}")
    print(f"  tool accuracy   {res['tool_accuracy']}")
    print(f"  field accuracy  {res['field_accuracy']}   by key {res['field_accuracy_by_key']}")
    print(f"  extra fields    {res['extra_fields']} (review by hand) · dropped by gate {res['dropped_by_gate']}")
    print(f"  completed       {res['completed']}")
    for k, v in res["latency_ms"].items():
        print(f"  {k:<14}  {v}")
    print(f"  tokens          {res['tokens']}")
    bad = [t for t in res["turns"] if t["gold_tool"] != t["pred_tool"]
           or not all(f["ok"] for f in t["fields"].values()) or t["extra"]]
    for t in bad[:25]:
        wrong = {k: f["pred"] for k, f in t["fields"].items() if not f["ok"]}
        print(f"   ✗ {t['case']}#{t['turn']} tool {t['pred_tool']} (want {t['gold_tool']}) "
              f"wrong {wrong} extra {t['extra']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["BOARD", "LAPTOP", "MOCK", "ORACLE"])
    ap.add_argument("--backend", choices=["env", "oracle"], default="env",
                    help="env: LLM / LLM_URL from the environment (see app/server.py)")
    ap.add_argument("--cases", default=str(ROOT / "eval" / "cases.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args()
    cases = load_cases(Path(a.cases))
    if a.backend == "oracle":
        backend = Oracle(cases)
    else:
        from app.server import make_backend
        backend = make_backend()
        if os.environ.get("LLM", "mock") == "mock" and a.label != "MOCK":
            raise SystemExit("LLM=mock: use --label MOCK")
    res = run(cases, backend, a.label)
    print_summary(res)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"eval_{a.label}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"  saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
