"""app/fastpath.py: answers only unambiguous turns, and is never wrong on the eval set."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from app.fastpath import answer  # noqa: E402
from app.form import load_schema  # noqa: E402
from run_eval import field_match  # noqa: E402

TYPES = next(f for f in load_schema()["fields"] if f["key"] == "t")["values"]


def test_never_wrong_on_any_eval_turn_and_covers_a_large_share():
    hit = total = 0
    for line in (ROOT / "eval" / "nlu" / "cases.jsonl").read_text().splitlines():
        c = json.loads(line)
        for t in c["turns"]:
            total += 1
            state = "review" if all(k in t["form_before"] for k in "tlds") else "collecting"
            a = answer(t["user"], t["asked"], state, TYPES)
            if a is None:
                continue
            hit += 1
            g = t["gold"]
            assert a["tool"] == g["tool"], (c["id"], t["i"], t["user"], a)
            assert set(a.get("args", {})) <= set(g["args"]), (c["id"], t["i"], a)
            for k, v in g["args"].items():
                assert field_match(k, a.get("args", {}).get(k), v, c.get("accept", {}).get(k)), (c["id"], t["i"])
    assert hit / total > 0.4


def test_what_qualifies():
    assert answer("it's medium", "s", "collecting", TYPES) == {"tool": "update_report", "args": {"s": "medium"}}
    assert answer("I'd say high please.", "s", "collecting", TYPES)["args"] == {"s": "high"}
    assert answer("water leak", "t", "collecting", TYPES)["args"] == {"t": "water_leak"}
    assert answer("quarter past two", "d", "collecting", TYPES)["args"] == {"d": "quarter past two"}
    assert answer("around 10 AM.", "d", "collecting", TYPES)["args"] == {"d": "around 10 AM"}
    assert answer("yes submit it", None, "review", TYPES) == {"tool": "submit_report"}
    assert answer("cancel this report", "l", "collecting", TYPES) == {"tool": "cancel_report"}


def test_what_goes_to_the_llm():
    for text, asked, state in [
        ("not high, medium", "s", "collecting"),          # negation
        ("medium, someone could slip", "s", "collecting"),  # extra content
        ("high and low", "s", "collecting"),              # two values
        ("around 3, no wait, closer to 3:30", "d", "collecting"),  # correction
        ("at 3 PM in the lab", "d", "collecting"),        # a place too
        ("electrical problem", "t", "collecting"),        # not a bare type name
        ("submit", None, "collecting"),                   # submit while incomplete: the LLM + code decide
        ("yes but the time is wrong", None, "review"),
        ("medium", "l", "collecting"),                    # severity word, but location was asked
        ("", "s", "collecting"),
    ]:
        assert answer(text, asked, state, TYPES) is None, text
