"""eval/run_eval.py: the scorer gives 100% to the gold answers and less to wrong ones."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from run_eval import Oracle, field_match, load_cases, run  # noqa: E402

CASES = load_cases(ROOT / "eval" / "cases.jsonl")


def test_gold_answers_score_100_and_complete_every_case():
    res = run(CASES, Oracle(CASES), "ORACLE")
    assert res["tool_accuracy"] == 1.0 and res["field_accuracy"] == 1.0
    assert res["dropped_by_gate"] == 0 and res["extra_fields"] == 0
    done, want = res["completed"].split("/")
    assert done == want and int(want) == sum(
        c["turns"][-1].get("gold_tool") == "submit_report" for c in CASES)


def test_wrong_answers_score_lower():
    class Wrong(Oracle):
        def generate(self, *a, **kw):
            r = super().generate(*a, **kw)
            call = json.loads(r.text)
            if call.get("args", {}).get("t") == "water_leak":
                call["args"]["t"] = "electrical"             # wrong enum
            if "s" in call.get("args", {}):
                call["args"]["w"] = ["Mr Nobody"]            # invented witness → gate drops it
            r.text = json.dumps(call)
            return r
    res = run(CASES, Wrong(CASES), "ORACLE")
    assert res["field_accuracy"] < 1.0 and res["dropped_by_gate"] > 0


def test_text_and_list_matching():
    assert field_match("l", "near Conference Room 2", "Conference Room 2")
    assert not field_match("l", "Conference Room 3", "Conference Room 2")
    assert not field_match("l", "the big hall on the ground floor next to reception", "lobby")
    assert field_match("w", ["Priya", "Karthik"], ["Karthik", "Priya"])
    assert not field_match("w", ["Priya"], ["Priya", "Karthik"])           # missing one
    assert not field_match("w", ["Priya", "Karthik", "Ravi"], ["Priya", "Karthik"])  # extra one
    assert field_match("s", "low", "low") and not field_match("s", None, "low")
