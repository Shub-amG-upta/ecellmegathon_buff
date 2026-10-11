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
    assert answer("submit", None, "collecting", TYPES) == {"tool": "submit_report"}     # code then refuses
    assert answer("ok then cancel this one", None, "collecting", TYPES) == {"tool": "cancel_report"}
    assert answer("um... uh...", None, "collecting", TYPES) == {"tool": "no_op"}
    assert answer("don't cancel it, the time is wrong", None, "review", TYPES) is None
    assert answer("near gate 3", "l", "collecting", TYPES)["args"] == {"l": "near gate 3"}
    assert answer("the store room on ground floor", "l", "collecting", TYPES)["args"] == {"l": "store room on ground floor"}


def test_what_goes_to_the_llm():
    for text, asked, state in [
        ("not high, medium", "s", "collecting"),          # negation
        ("medium, someone could slip", "s", "collecting"),  # extra content
        ("high and low", "s", "collecting"),              # two values
        ("around 3, no wait, closer to 3:30", "d", "collecting"),  # correction
        ("at 3 PM in the lab", "d", "collecting"),        # a place too
        ("electrical problem", "t", "collecting"),        # not a bare type name
        ("yes but the time is wrong", None, "review"),
        ("medium", "l", "collecting"),                    # severity word, but location was asked
        ("near the lab at 3 PM", "l", "collecting"),      # a time too
        ("not the lab, the workshop", "l", "collecting"),  # correction
        ("in the lab, it's getting worse", "l", "collecting"),  # more than the place
        ("the second floor, I think", "l", "collecting"),  # hedged
        ("meter room", None, "collecting"),               # location wasn't asked
        ("", "s", "collecting"),
    ]:
        assert answer(text, asked, state, TYPES) is None, text


def test_stt_slips_on_one_word_answers():
    """QIDK 2026-10-11: whisper heard "Sub-Make" for submit and "My" for a severity."""
    from app.fastpath import answer
    assert answer("Sub-Make", None, "review", []) == {"tool": "submit_report"}
    assert answer("summit", None, "review", []) == {"tool": "submit_report"}
    assert answer("Smith", None, "review", []) is None                       # doesn't start like submit
    assert answer("Sub-Make", None, "collecting", []) is None                # only on the review screen
    assert answer("critic", "s", "collecting", [])["args"] == {"s": "critical"}
    assert answer("My", "s", "collecting", []) is None                       # too short to guess


def test_severity_guard():
    from app import gate
    assert gate.stated_severity_only({"s": "high"}, "My") == {}
    assert gate.stated_severity_only({"s": "high", "l": "lab"}, "it's serious, in the lab") == {"s": "high", "l": "lab"}
    assert gate.stated_severity_only({"s": "medium"}, "medium hai") == {"s": "medium"}
