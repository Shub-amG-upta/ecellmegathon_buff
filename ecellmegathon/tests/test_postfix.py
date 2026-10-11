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
    assert postfix.drop_parts({"e": ["forklift mast"]}, {"e": ["forklift"]}, "the forklift mast is bent") == {}
    assert postfix.drop_parts({"e": ["forklift mast"]}, {"e": ["forklift"]}, "check the forklift mast") == \
        {"e": ["forklift mast"]}                                       # no damage word: kept
    assert postfix.drop_parts({"e": ["charger"]}, {"e": ["forklift"]}, "the charger is broken") == {"e": ["charger"]}
    assert postfix.drop_parts({"p": ["Ravi"]}, {"e": ["forklift"]}, "it is bent") == {"p": ["Ravi"]}
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


def test_list_fixes():
    assert postfix.snap_lists({"p": ["staff"]}, "one of our staff slipped") == {"p": ["one of our staff"]}
    assert postfix.snap_lists({"p": ["mechanic"]}, "One of the mechanics got his finger caught") == \
        {"p": ["One of the mechanics"]}
    assert postfix.snap_lists({"a": ["CPR"]}, "Two colleagues gave CPR") == {"a": ["gave CPR"]}
    assert postfix.snap_lists({"e": ["forklift"]}, "the forklift hit it") == {"e": ["the forklift"]}
    assert postfix.snap_lists({"p": ["Ravi"]}, "someone else") == {"p": ["Ravi"]}          # not in the turn
    assert postfix.keep_responses({"a": ["stopped the conveyor", "on the way to hospital"]}) == \
        {"a": ["stopped the conveyor"]}
    assert postfix.keep_responses({"a": ["One cook has bad burns"], "t": "fire"}) == {"t": "fire"}
    assert postfix.fill_actions({}, "Maintenance has already isolated the area.", {}) == {"a": ["isolated the area"]}
    assert postfix.fill_actions({}, "he hit the door and has started bleeding", {}) == {}  # not a response
    assert postfix.fill_actions({"a": ["x"]}, "has isolated it", {}) == {"a": ["x"]}         # never replaces
    assert postfix.fill_actions({}, "has isolated it", {"a": ["called 108"]}) == {}          # nor adds to the form's


def test_every_gold_action_is_a_response():
    for c in CASES:
        for t in c["turns"]:
            for a in t["gold"]["args"].get("a", []):
                assert postfix._RESPONSE.search(a), (c["id"], t["i"], a)


def test_listfix_switch_off(monkeypatch):
    monkeypatch.setenv("LISTFIX", "off")
    assert postfix.apply({"a": ["One cook has bad burns"]}, "One cook has bad burns", {}) == \
        {"a": ["One cook has bad burns"]}


def test_eval_scores_the_action_the_app_takes():
    from run_eval import effective_tool
    assert effective_tool({"tool": "update_report", "args": {"e": ["x"]}}, {}) == "no_op"   # nothing survived
    assert effective_tool({"tool": "update_report"}, {"e": ["x"]}) == "update_report"
    assert effective_tool({"tool": "submit_report"}, {}) == "submit_report"
    assert effective_tool({"tool": None}, {}) is None


def test_switch_off(monkeypatch):
    monkeypatch.setenv("POSTFIX", "off")
    args = {"e": ["meter room"]}
    assert postfix.apply(dict(args), "in the meter room", {}) == args


def test_every_hinglish_gold_action_is_a_response():
    """eval/hinglish: keep_responses must not drop Hinglish actions ("Electrician ko bula liya")."""
    hi = ROOT / "eval" / "hinglish" / "cases.jsonl"
    for ln in hi.read_text().splitlines():
        c = json.loads(ln)
        for t in c["turns"]:
            for a in t["gold"]["args"].get("a", []):
                assert postfix._RESPONSE.search(a), (c["id"], t["i"], a)


# ---- PEOPLEFIX ----------------------------------------------------------------------------------------------

def _all_gold(paths):
    for path in paths:
        for ln in (ROOT / path).read_text(encoding="utf-8").splitlines():
            c = json.loads(ln)
            for t in c["turns"]:
                yield c["id"], t


def test_every_gold_person_and_witness_is_kept_as_it_is():
    for cid, t in _all_gold(["eval/nlu/cases.jsonl", "eval/nlu15/cases.jsonl", "eval/hinglish/cases.jsonl"]):
        for k in ("p", "w"):
            for v in t["gold"]["args"].get(k, []):
                assert postfix.person_value(v) == v, (cid, t["i"], k, v)


def test_people_and_action_rules():
    assert postfix.people_only({"p": ["the manager", "he wasn't wearing goggles"], "w": ["Anil"]}) == \
        {"p": ["the manager"], "w": ["Anil"]}
    assert postfix.people_only({"p": ["Three workers are badly injured"]}) == {"p": ["Three workers"]}
    assert postfix.people_only({"p": ["we gave him water", "nearly slipped", "fitter's legs"]}) == {}
    assert postfix.fill_person({}, "Our lineman was fixing the old cables when he got a shock", {}) == \
        {"p": ["Our lineman"]}
    assert postfix.fill_person({}, "Solvent got into the operator's eyes", {}) == {"p": ["the operator"]}
    assert postfix.fill_person({}, "Seven people were inside, a guy was reversing", {}) == {}     # not labelled
    assert postfix.fill_person({}, "Our lineman fell", {"t": "fall_height"}) == {}               # first turn only
    assert postfix.complete_actions({"a": ["informed the police"]},
                                    "we have informed the police and our security team") == \
        {"a": ["informed the police and our security team"]}
    assert postfix.complete_actions({"a": ["flushed his eyes"]},
                                    "we have flushed his eyes for fifteen minutes and EHS has been informed") == \
        {"a": ["flushed his eyes for fifteen minutes"]}
    assert postfix.complete_actions({"a": ["fanned him"]}, "fanned him till the ambulance came") == {"a": ["fanned him"]}
    assert postfix.fill_more_actions({}, "we hit the emergency stop and reversed the rollers to free her", {}) == \
        {"a": ["hit the emergency stop", "reversed the rollers to free her"]}
    assert postfix.fill_more_actions({}, "He hit his right arm on a metal cabinet", {}) == {}
    assert postfix.fill_more_actions({}, "the forklift reversed into one of our packers", {}) == {}


def test_peoplefix_switch_off(monkeypatch):
    monkeypatch.setenv("PEOPLEFIX", "off")
    assert postfix.apply({"p": ["he wasn't wearing goggles"]}, "he wasn't wearing goggles", {"t": "chemical"}) == \
        {"p": ["he wasn't wearing goggles"]}
