"""app/gate.py: nothing reaches the form unless the user said it."""
from app.gate import check, severity_floor, support

T = ("There was a water leak near Conference Room 2 around 10 AM. "
     "Maintenance has already isolated the area.")


def test_the_problem_statement_example_passes_untouched():
    args = {"t": "water_leak", "l": "near Conference Room 2", "d": "around 10 AM",
            "a": ["area isolated by maintenance"]}
    kept, dropped = check(args, T)
    assert kept == args and dropped == []


def test_invented_values_are_dropped_and_logged():
    args = {"t": "water_leak", "l": "Server Room B", "w": ["John Smith"],
            "a": ["area isolated", "called the fire brigade"]}
    kept, dropped = check(args, T)
    assert kept == {"t": "water_leak", "a": ["area isolated"]}
    assert {d["field"] for d in dropped} == {"l", "w", "a"}
    assert any(d["value"] == "called the fire brigade" for d in dropped)


def test_room_numbers_must_match_exactly():
    assert support("Conference Room 2", T) == 1.0
    kept, dropped = check({"l": "Conference Room 3", "d": "around 11 AM"}, T)
    assert kept == {} and len(dropped) == 2                 # 2 of 3 words would pass otherwise


def test_small_spelling_differences_still_count():
    assert support("area isolate", T) == 1.0
    assert support("the", T) == 0.0                         # no content words → unsupported


def test_severity_floor_reports_but_never_decides():
    assert severity_floor("fire", "low") == "high"
    assert severity_floor("fire", "critical") is None
    assert severity_floor("chemical", "low") == "medium"
    assert severity_floor("gas_leak", "medium") == "high"
    assert severity_floor("water_leak", "low") is None
    assert severity_floor("fire", None) is None


# ---- severity fallback ------------------------------------------------------------------------
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from app.gate import fill_severity, severity_from_words  # noqa: E402

_CASES = [json.loads(ln) for ln in
          (Path(__file__).resolve().parent.parent / "eval" / "nlu" / "cases.jsonl").read_text(encoding="utf-8")
          .splitlines() if ln.strip()]
_BY_ID = {c["id"]: c for c in _CASES}
_DEV_TURNS = [(c["id"], t) for c in _CASES if c["split"] in ("dev", "demo") for t in c["turns"]]


def _say(case_id: str, i: int) -> str:
    return _BY_ID[case_id]["turns"][i]["user"]


def _variants(text: str) -> list[str]:
    """As typed, lowercase, and raw STT style (lowercase, no punctuation)."""
    return [text, text.lower(), re.sub(r"[^\w\s']", "", text).lower()]


@pytest.mark.parametrize("text,want", [
    (_say("el-01", 0), "high"),          # "a serious incident"
    (_say("sl-01", 0), "high"),          # "emergency room" ... "I'd say severity high"
    (_say("fk-01", 0), "critical"),      # "This is critical."
    (_say("ch-01", 0), "medium"),        # "Medium severity."
    (_say("cv-01", 0), "critical"),      # "... Critical."
    (_say("fi-04", 0), "critical"),      # "... it's critical."
    (_say("gs-01", 0), None),            # "bad burns", "nine others are minor": injuries, not severity
    (_say("cl-01", 0), None),            # "emergency ward" is a place
    ("not high, medium", "medium"),
    ("very serious, critical", "critical"),
    ("low, not serious, just irritation", "low"),
    ("we pressed the emergency stop and called 108", None),
    ("high, he has a fracture, he's admitted", "high"),
    ("actually make it critical", "critical"),
], ids=["el-01", "sl-01", "fk-01", "ch-01", "cv-01", "fi-04", "gs-01", "cl-01", "not-high-medium",
        "very-serious-critical", "low-not-serious", "emergency-stop", "high-fracture", "make-it-critical"])
def test_severity_from_words(text, want):
    for v in _variants(text):
        assert severity_from_words(v) == want, v


@pytest.mark.parametrize("text", [
    "the box fell from the high shelf", "sparks from the high voltage panel", "he tripped over a low wall",
    "he is in critical condition", "minor cuts on his hand", "bad injuries to both legs",
    "he was taken to the emergency ward", "the pressure was high", "nine others are minor",
    "", "the water level was low",
], ids=["high-shelf", "high-voltage", "low-wall", "critical-condition", "minor-cuts", "bad-injuries",
        "emergency-ward", "pressure-was-high", "others-are-minor", "empty", "level-was-low"])
def test_level_words_that_are_not_a_severity_statement(text):
    for v in _variants(text):
        assert severity_from_words(v) is None, v


@pytest.mark.parametrize("case_id,turn", [(cid, t) for cid, t in _DEV_TURNS],
                         ids=[f"{cid}-t{t['i']}" for cid, t in _DEV_TURNS])
def test_never_disagrees_with_gold_on_any_dev_turn(case_id, turn):
    """Over every dev/demo turn (never the test split): never a level other than gold `s`; where gold
    has no `s`, nothing, unless the form already has a severity (then fill_severity does nothing)."""
    gold = turn["gold"]["args"].get("s")
    for v in _variants(turn["user"]):
        got = severity_from_words(v)
        if gold is not None:
            assert got in (None, gold), (v, got, gold)
        else:
            assert got is None or turn["form_before"].get("s"), (v, got)


def test_fill_only_when_blank():
    text = "Kitchen fire, six staff with burns, it's critical."
    assert fill_severity({"t": "fire", "s": "low"}, text, False) == {"t": "fire", "s": "low"}   # model gave s
    assert fill_severity({"t": "fire"}, text, True) == {"t": "fire"}                           # form has one
    assert fill_severity({"t": "fire"}, text, False) == {"t": "fire", "s": "critical"}         # filled
    assert fill_severity({"t": "fire"}, "Kitchen fire, six staff with burns.", False) == {"t": "fire"}
    kept = {"t": "fire"}
    fill_severity(kept, text, False)
    assert kept == {"t": "fire"}                                                               # not mutated
