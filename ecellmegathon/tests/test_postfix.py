"""app/postfix.py: each fix does its job, and on the gold answers of every eval turn it changes nothing."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from app import postfix  # noqa: E402
from run_eval import field_match  # noqa: E402

CASES = [json.loads(ln) for ln in (ROOT / "eval" / "nlu" / "cases.jsonl").read_text().splitlines()]


def test_gold_answers_are_never_changed():
    for c in CASES:
        for t in c["turns"]:
            g = t["gold"]["args"]
            out = postfix.apply(dict(g), t["user"], t["form_before"])
            assert set(out) == set(g), (c["id"], t["i"], out)
            for k, v in g.items():
                assert field_match(k, out[k], v, c.get("accept", {}).get(k)), (c["id"], t["i"], k, out[k])


def test_each_fix():
    assert postfix.drop_repeats({"t": "fire", "w": ["Ravi", "Anil"]}, {"t": "fire", "w": ["ravi"]}) == {"w": ["Anil"]}
    assert postfix.veto({"e": ["meter room", "panel"], "p": ["ambulance", "the foreman"]}, None) == \
        {"e": ["panel"], "p": ["the foreman"]}
    assert postfix.witnesses({"p": ["Anil", "a delivery boy"]}, "Anil and I both saw it") == \
        {"p": ["a delivery boy"], "w": ["Anil"]}
    assert postfix.witness_names("Nobody else saw it") == [] and postfix.witness_names("Then I saw smoke") == []
    assert postfix.witness_names("Ravi, Sunita and the parking attendant saw it.") == \
        ["Ravi", "Sunita", "the parking attendant"]
    assert postfix.expand_location("ground floor", "incident in the meter room on the ground floor at 3 PM") == \
        "meter room on the ground floor"
    assert postfix.expand_location("conference hall", "fell in the conference hall on 2nd floor") == \
        "conference hall on 2nd floor"
    assert postfix.expand_location("lobby", "in the lobby") == "lobby"
    assert postfix.fill_time({}, "Last night around 8 PM a nursing aide tripped", False) == {"d": "Last night around 8 PM"}
    assert postfix.fill_time({}, "she's in hospital now", False) == {}            # a bare "now" is not a time
    assert postfix.fill_time({"d": "x"}, "at 3 PM", False) == {"d": "x"}           # never replaces the model's
    assert postfix.fill_time({}, "at 3 PM", True) == {}                            # nor the form's


def test_switch_off(monkeypatch):
    monkeypatch.setenv("POSTFIX", "off")
    args = {"e": ["meter room"]}
    assert postfix.apply(dict(args), "in the meter room", {}) == args
