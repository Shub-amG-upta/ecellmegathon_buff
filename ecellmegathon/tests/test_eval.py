"""eval/run_eval.py on eval/nlu/cases.jsonl: the scorer gives the gold answers 100%
in both modes, scores wrong answers lower, and keeps the test split out by default."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from run_eval import Oracle, field_match, free_running, load_cases, summarise, teacher_forced  # noqa: E402

ALL = load_cases(split="all")


def test_gold_answers_score_100_in_both_modes():
    tf, fr = teacher_forced(ALL, Oracle(ALL)), free_running(ALL, Oracle(ALL))
    s = summarise(tf, fr, "ORACLE", Oracle(ALL), "all")
    t, f = s["teacher_forced"], s["free_running"]
    assert t["turns"] == 250 and t["tool_accuracy"] == 1.0 and t["field_accuracy"] == 1.0
    assert t["turns_with_extra"] == 0 and t["dropped_by_gate"] == 0   # the gate keeps every gold value
    assert f["dialogs"] == 65 and f["final_field_accuracy"] == 1.0 and f["dialogs_exact"] == 65


def test_wrong_answers_score_lower():
    class Wrong(Oracle):
        def generate(self, *a, **kw):
            r = super().generate(*a, **kw)
            call = json.loads(r.text)
            args = call.get("args", {})
            if args.get("t") == "fire":
                args["t"] = "electrical"                      # wrong enum
            if "s" in args:
                args["w"] = ["Mr Nobody"]                     # invented witness → dropped by the gate
            if "l" in args:
                args["l"] = args["l"] + " and the whole building"   # padded text → no longer matches
            r.text = json.dumps(call)
            return r
    tf = teacher_forced(ALL, Wrong(ALL))
    s = summarise(tf, [], "ORACLE", Wrong(ALL), "all")["teacher_forced"]
    assert s["field_accuracy"] < 1.0 and s["dropped_by_gate"] > 0
    assert s["by_field"]["t"] != "64/64" and s["by_field"]["l"] != "66/66"


def test_matching_rules():
    assert field_match("l", "near Conference Room 2", "near Conference Room 2")
    assert field_match("l", "Conference Room 2", "near Conference Room 2", ["Conference Room 2"])
    assert not field_match("l", "Conference Room 3", "near Conference Room 2")
    assert field_match("a", ["area isolated"], ["isolated the area"], ["area isolated"])
    assert field_match("w", ["Karthik", "Priya"], ["Priya", "Karthik"])
    assert not field_match("w", ["Priya"], ["Priya", "Karthik"])                 # one missing
    assert not field_match("w", ["Priya", "Karthik", "Ravi"], ["Priya", "Karthik"])  # one invented
    assert field_match("t", "fire", "electrical", ["fire"])                    # accepted alternative
    assert not field_match("s", None, "low")


def test_test_split_is_locked_by_default():
    dev = load_cases()
    assert {c["split"] for c in dev} == {"dev", "demo"}
    assert not any(c["split"] == "test" for c in dev)
    assert len(load_cases(split="test")) == 21
