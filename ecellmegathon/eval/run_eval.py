"""
Extraction eval on eval/nlu/cases.jsonl (65 dialogs, 250 turns; rules in eval/nlu/LABELING.md).

  .venv/bin/python eval/run_eval.py --label LAPTOP                     # LLM=llamaserver in env
  .venv/bin/python eval/run_eval.py --label BOARD --split dev
  .venv/bin/python eval/run_eval.py --label ORACLE --backend oracle   # scorer self-check: must be 100%

Two modes (both by default; --mode picks one):
  teacher-forced  each turn starts from its gold `form_before` and `asked`: one LLM call per turn, scored
                  against that turn's gold. Measures extraction and field mapping without errors cascading.
  free-running    each dialog runs through the real dialog loop from an empty form; the final form is
                  scored against `gold_final`. Measures the conversation.

--label is required and goes into every output: a number without its label
(BOARD / LAPTOP / MOCK / ORACLE) never leaves this script. The test split is
locked: it runs only with --split test, and only after the decision rules are
written down (PLAN.md, M4). Run each configuration twice: temperature-0 replies
drift between server launches.

Scoring (LABELING.md)
  tool            the right tool for the turn
  field           per gold field: enums exact or in accept; text by content words against gold or any
                  accept span (gold words found ≥ 0.8, predicted words in it ≥ 0.5); lists item by item
  extra           a value the gold doesn't have and no accept span covers (after the gate)
  dropped         values the gate removed
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import gate  # noqa: E402
from app.backends import LLMReply  # noqa: E402
from app.dialog import Dialog  # noqa: E402
from app.gate import _word_in, content_words  # noqa: E402
from app.prompt import user_message  # noqa: E402

CASES = ROOT / "eval" / "nlu" / "cases.jsonl"
TEXT_RECALL, TEXT_PRECISION = 0.8, 0.5     # DESIGN CHOICE: text-field match, see docstring
ENUMS = ("t", "s")


def load_cases(path: Path = CASES, split: str = "dev") -> list[dict]:
    cases = [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]
    if split == "all":
        return cases
    return [c for c in cases if c["split"] in ({"dev", "demo"} if split == "dev" else {split})]


# ---- matching -----------------------------------------------------------------
def text_match(pred: str, gold: str) -> bool:
    g, p = content_words(gold), content_words(pred)
    if not g or not p:
        return not g and not p
    rec = sum(_word_in(w, set(p)) for w in g) / len(g)
    prec = sum(_word_in(w, set(g)) for w in p) / len(p)
    return rec >= TEXT_RECALL and prec >= TEXT_PRECISION


def _ok_item(pred: str, gold_items: list[str], accept: list[str]) -> bool:
    return any(text_match(pred, g) for g in gold_items + accept)


def field_match(key: str, pred, gold, accept: list | None = None) -> bool:
    """One gold field. Lists: every gold item found (directly or via an accept
    span for it), and every predicted item is gold or accepted."""
    accept = accept or []
    if pred is None:
        return False
    if key in ENUMS:
        return pred == gold or pred in accept
    if isinstance(gold, list):
        pred = pred if isinstance(pred, list) else [pred]

        def found(g):              # said directly, or in an accepted wording of the same item
            return any(text_match(p, g) or any(text_match(p, a) and _covers(a, g) for a in accept)
                       for p in pred)
        return all(found(g) for g in gold) and all(_ok_item(p, gold, accept) for p in pred)
    return isinstance(pred, str) and (text_match(pred, gold) or any(text_match(pred, a) for a in accept))


def _covers(span: str, gold: str) -> bool:
    """An accept span stands in for a gold item when they share a content word
    (accept spans are alternative wordings of the same thing)."""
    return bool(set(content_words(span)) & set(content_words(gold)))


def extras(pred: dict, gold: dict, accept: dict) -> dict:
    """Predicted values that neither the gold nor an accept span covers."""
    out = {}
    for k, v in pred.items():
        if k in gold:
            if isinstance(v, list):
                bad = [x for x in v if not _ok_item(x, gold[k], accept.get(k, []))]
                if bad:
                    out[k] = bad
            continue
        if k in ENUMS:
            out[k] = v
        else:
            items = v if isinstance(v, list) else [v]
            bad = [x for x in items if not any(text_match(x, a) for a in accept.get(k, []))]
            if bad:
                out[k] = bad if isinstance(v, list) else bad[0]
    return out


def score_args(pred: dict, gold: dict, accept: dict) -> dict:
    return {"fields": {k: {"gold": v, "pred": pred.get(k),
                           "ok": field_match(k, pred.get(k), v, accept.get(k))}
                       for k, v in gold.items()},
            "extra": extras(pred, gold, accept)}


def gated(call: dict, transcript: str) -> tuple[dict, list]:
    return gate.check(dict(call.get("args") or {}), transcript)


# ---- backends -----------------------------------------------------------------
class Oracle:
    """Answers every call with the gold tool call of the turn in its prompt.
    Scorer self-check only: every metric must come out perfect."""
    lane, model_file = "oracle", "oracle"

    def __init__(self, cases):
        self.by_text = {t["user"]: t["gold"] for c in cases for t in c["turns"]}

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None):
        user = prompt.rsplit("\nUser: ", 1)[1]
        g = self.by_text[user]
        call = {"tool": g["tool"], **({"args": g["args"]} if g["args"] else {})}
        return LLMReply(text=json.dumps(call), backend="oracle", model="oracle")


# ---- the two modes ------------------------------------------------------------
def teacher_forced(cases, backend) -> list[dict]:
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        d = Dialog(backend, report_dir=Path(tmp))
        for c in cases:
            heard = []
            for t in c["turns"]:
                heard.append(t["user"])
                d.reset()
                d.form.apply(t["form_before"])
                reply = backend.generate(user_message(d.form.compact(), t["asked"], t["user"]),
                                         system=d.system, json_schema=d.json_schema,
                                         max_tokens=d.max_tokens)
                call, err = d._parse(reply.text) if reply.status == "ok" else ({"tool": None}, reply.error)
                kept, dropped = gated(call, " ".join(heard))
                gold = t["gold"]
                out.append({"case": c["id"], "turn": t["i"], "user": t["user"], "asked": t["asked"],
                            "gold_tool": gold["tool"], "pred_tool": call.get("tool"),
                            **score_args(kept if call.get("tool") == "update_report" else {},
                                         gold["args"], c.get("accept", {})),
                            "raw": call, "dropped": dropped, "error": err,
                            "llm": {"prefill_ms": reply.prefill_ms, "decode_ms": reply.decode_ms,
                                    "wall_ms": reply.latency_ms, "decode_tokens": reply.decode_tokens,
                                    "prefill_tokens": reply.prefill_tokens}})
    return out


def free_running(cases, backend) -> list[dict]:
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for c in cases:
            now = datetime.fromisoformat(c["ref_time"])
            d = Dialog(backend, report_dir=Path(tmp), clock=lambda now=now: now)
            for t in c["turns"]:
                d.turn(t["user"])
            final = d.form.compact()
            res = score_args(final, c["gold_final"], c.get("accept", {}))
            want = c["final_tool"]
            ended = {"submit_report": "submitted", "cancel_report": "cancelled"}.get(want)
            out.append({"case": c["id"], **res, "final_state": d.state, "want_state": ended,
                        "state_ok": d.state == ended if ended else True,
                        "missing": [f["name"] for f in d.form.missing()]})
    return out


# ---- summary ------------------------------------------------------------------
def _stat(vals):
    v = [x for x in vals if x is not None]
    return {"mean": round(statistics.mean(v), 1), "median": round(statistics.median(v), 1),
            "n": len(v)} if v else None


def _field_table(rows):
    by = {}
    for r in rows:
        for k, f in r["fields"].items():
            by.setdefault(k, []).append(f["ok"])
    return {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by.items())}


def summarise(tf, fr, label, backend, split) -> dict:
    res = {"label": label, "split": split, "laptop_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "model": getattr(backend, "model_file", "?"), "lane": getattr(backend, "lane", "?")}
    if tf:
        fields = [f["ok"] for r in tf for f in r["fields"].values()]
        res["teacher_forced"] = {
            "turns": len(tf),
            "tool_accuracy": round(sum(r["gold_tool"] == r["pred_tool"] for r in tf) / len(tf), 3),
            "field_accuracy": round(sum(fields) / len(fields), 3) if fields else None,
            "by_field": _field_table(tf),
            "turns_with_extra": sum(bool(r["extra"]) for r in tf),
            "dropped_by_gate": sum(len(r["dropped"]) for r in tf),
            "errors": sum(bool(r["error"]) for r in tf),
            "latency_ms": {k: _stat([r["llm"][k] for r in tf]) for k in ("prefill_ms", "decode_ms", "wall_ms")},
            "tokens": {k: _stat([r["llm"][k] for r in tf]) for k in ("prefill_tokens", "decode_tokens")},
        }
    if fr:
        fields = [f["ok"] for r in fr for f in r["fields"].values()]
        res["free_running"] = {
            "dialogs": len(fr),
            "final_field_accuracy": round(sum(fields) / len(fields), 3) if fields else None,
            "by_field": _field_table(fr),
            "dialogs_exact": sum(all(f["ok"] for f in r["fields"].values()) and not r["extra"]
                                 and r["state_ok"] for r in fr),
            "ended_right": f"{sum(r['state_ok'] for r in fr)}/{len(fr)}",
            "dialogs_with_extra": sum(bool(r["extra"]) for r in fr),
        }
    res["turns"] = tf
    res["dialogs"] = fr
    return res


def print_summary(res: dict) -> None:
    print(f"\n[{res['label']}] split {res['split']} · model {res['model']} on {res['lane']} · {res['laptop_time']}")
    if "teacher_forced" in res:
        t = res["teacher_forced"]
        print(f"  teacher-forced  {t['turns']} turns · tool {t['tool_accuracy']} · field {t['field_accuracy']}"
              f" · turns with extra {t['turns_with_extra']} · dropped by gate {t['dropped_by_gate']}"
              f" · errors {t['errors']}")
        print(f"                  by field {t['by_field']}")
        for k, v in t["latency_ms"].items():
            print(f"                  {k:<11} {v}")
        print(f"                  tokens {t['tokens']}")
        bad = [r for r in res["turns"] if r["gold_tool"] != r["pred_tool"]
               or not all(f["ok"] for f in r["fields"].values()) or r["extra"]]
        for r in bad[:20]:
            wrong = {k: f["pred"] for k, f in r["fields"].items() if not f["ok"]}
            print(f"   ✗ {r['case']}#{r['turn']} tool {r['pred_tool']} (want {r['gold_tool']}) "
                  f"wrong {wrong} extra {r['extra']}")
    if "free_running" in res:
        f = res["free_running"]
        print(f"  free-running    {f['dialogs']} dialogs · final field {f['final_field_accuracy']}"
              f" · exact {f['dialogs_exact']} · ended right {f['ended_right']}"
              f" · with extra {f['dialogs_with_extra']}")
        print(f"                  by field {f['by_field']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["BOARD", "LAPTOP", "MOCK", "ORACLE"])
    ap.add_argument("--backend", choices=["env", "oracle"], default="env",
                    help="env: LLM / LLM_URL from the environment (see app/server.py)")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev",
                    help="dev includes the demo case; test is locked until the M4 rules exist")
    ap.add_argument("--mode", choices=["both", "teacher", "free"], default="both")
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args()
    cases = load_cases(Path(a.cases), a.split)
    if a.backend == "oracle":
        backend = Oracle(cases)
    else:
        from app.server import make_backend
        backend = make_backend()
        if os.environ.get("LLM", "mock") == "mock" and a.label != "MOCK":
            raise SystemExit("LLM=mock: use --label MOCK")
    tf = teacher_forced(cases, backend) if a.mode in ("both", "teacher") else []
    fr = free_running(cases, backend) if a.mode in ("both", "free") else []
    res = summarise(tf, fr, a.label, backend, a.split)
    print_summary(res)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"eval_{a.label}_{a.split}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"  saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
