"""Phase 3, commit 1: the plant-side definitions. Outcome-based: assertions on
the sides each fact, finding, case, triple and note-fact is given, using the
real case library and real dev facts."""

import json
from pathlib import Path

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.multi import sides
from fieldmind.schemas import Fact, Finding

ROOT = Path(__file__).resolve().parent.parent
LIB = {c["case_id"]: c for c in
       json.loads((ROOT / "data/kb/case_library.json").read_text())["cases"]}


def _fact(i, check, tags, sev="WATCH"):
    return Fact(id=f"F{i}", check=check, tags=tags, window=(1, 1), value=0.0,
                detail=f"d{i}", severity=sev)


def _fnd(key, resolved=False):
    return Finding(id="X", signature_key=key, first_tick=1, last_tick=1,
                   severity="WATCH", detail="", resolved=resolved)


def test_fact_sides_follow_tags_and_split_balances():
    assert sides.fact_sides(_fact(1, "LIMIT", ["drum_level"])) == {"water"}
    assert sides.fact_sides(_fact(2, "RATE", ["bed_temp_avg"])) == {"heat"}
    assert sides.fact_sides(_fact(3, "RATE", ["steam_flow"])) == {"water", "heat"}
    assert sides.fact_sides(_fact(4, "BALANCE", ["feed_water_flow", "steam_flow", "drum_level"])) == {"water"}
    assert sides.fact_sides(_fact(5, "BALANCE", ["drum_pressure", "steam_flow"])) == {"heat"}
    assert sides.fact_sides(_fact(6, "BALANCE", ["bed_temp_avg", "steam_flow", "drum_pressure"])) == {"heat"}
    # "water balance SUSPENDED - untrusted tag(s): drum_level" is water
    assert sides.fact_sides(_fact(7, "BALANCE", ["drum_level"])) == {"water"}


def test_pattern_facts_take_the_side_they_name_not_their_context_tags():
    # the tag lists l1_checks._patterns emits
    assert sides.fact_sides(_fact(1, "PATTERN", ["bed_temp_avg", "steam_flow", "drum_pressure"])) == {"heat"}
    assert sides.fact_sides(_fact(2, "PATTERN", ["drum_level", "bed_temp_avg"])) == {"water"}
    assert sides.fact_sides(_fact(3, "PATTERN", ["bed_temp_avg", "drum_pressure"], "INFO")) == {"heat"}
    assert sides.active_sides([_fact(2, "PATTERN", ["drum_level", "bed_temp_avg"])], []) == ["water"]
    assert sides.active_sides([], [_fnd("PATTERN:bed_temp_avg|drum_level")]) == ["water"]
    assert sides.active_sides([], [_fnd("PATTERN:bed_temp_avg|drum_pressure|steam_flow")]) == ["heat"]


def test_weighted_flat_triple_is_not_movement():
    assert sides.case_sides({"signature": {"drum_level|FLAT|-": 0.35,
                                           "bed_temp_avg|DOWN|MED": 1.0}}) == {"heat"}


def test_case_sides_from_moving_triples_all_flat_goes_to_both():
    got = {cid: sides.case_sides(c) for cid, c in LIB.items()}
    assert got["RCA-01"] == {"water"}
    # RCA-14 and RCA-18 move steam_flow (both sides), so they are shown to both
    assert {c for c, s in got.items() if s == {"heat"}} == {"RCA-03", "RCA-07", "RCA-13"}
    assert {c for c, s in got.items() if s == {"water", "heat"}} == {
        "RCA-04", "RCA-05", "RCA-11", "RCA-14", "RCA-16", "RCA-18", "RCA-09", "RCA-10", "RCA-15"}


def test_active_sides_from_non_info_facts_and_open_findings():
    assert sides.active_sides([_fact(1, "RATE", ["bed_temp_avg"], "INFO")], []) == []
    assert sides.active_sides([_fact(1, "RATE", ["bed_temp_avg"])], []) == ["heat"]
    assert sides.active_sides([], [_fnd("LIMIT:drum_level")]) == ["water"]
    assert sides.active_sides([], [_fnd("LIMIT:drum_level", resolved=True)]) == []
    assert sides.active_sides([], [_fnd("BALANCE:feed_water_flow|steam_flow")]) == ["water"]
    assert sides.active_sides([], [_fnd("BALANCE:bed_temp_avg|drum_pressure")]) == ["heat"]
    assert sides.active_sides([], [_fnd("BALANCE:drum_level")]) == ["water"]


def test_signature_for_side_keeps_side_tags_and_its_pseudo_tag():
    sig = {("drum_level", "DOWN", "FAST"): 1.0, ("bed_temp_avg", "FLAT", "-"): 0.35,
           ("steam_flow", "FLAT", "-"): 0.35, ("water_balance", "DEFICIT", "MED"): 1.0,
           ("energy_balance", "HEAT_SHORT", "-"): 1.0}
    assert set(sides.signature_for_side(sig, "water")) == {
        ("drum_level", "DOWN", "FAST"), ("steam_flow", "FLAT", "-"), ("water_balance", "DEFICIT", "MED")}
    assert set(sides.signature_for_side(sig, "heat")) == {
        ("bed_temp_avg", "FLAT", "-"), ("steam_flow", "FLAT", "-"), ("energy_balance", "HEAT_SHORT", "-")}


def test_note_fact_goes_to_the_sides_of_its_tags_else_both():
    assert sides.notefact_sides({"pairs": [["drum_level", "NORMAL"]]}) == {"water"}
    assert sides.notefact_sides({"pairs": [["bed_temp_avg", "HIGH"], ["COAL", "WET"]]}) == {"heat"}
    assert sides.notefact_sides({"pairs": [["FEED_VALVE", "STUCK"]]}) == {"water", "heat"}
    assert sides.notefact_sides({"pairs": []}) == {"water", "heat"}


def test_other_side_line_and_primary_side():
    facts = [_fact(1, "LIMIT", ["drum_level"], "ALARM"), _fact(2, "RATE", ["bed_temp_avg"], "WATCH")]
    assert sides.other_side_line(facts, "heat") == "water side: [LIMIT/ALARM] d1"
    two = [_fact(8, "RATE", ["feed_water_flow"], "WATCH"), _fact(9, "LIMIT", ["drum_level"], "CRITICAL")]
    assert sides.other_side_line(two, "heat") == "water side: [LIMIT/CRITICAL] d9"   # most severe
    assert sides.other_side_line([facts[0]], "water") == "heat side: steady"
    assert sides.primary_side(facts) == "water"
    facts.append(_fact(3, "LIMIT", ["ms_temperature"], "CRITICAL"))
    assert sides.primary_side(facts) == "heat"
    assert sides.primary_side([]) == "water"                    # tie -> water
    # an open finding counts; a resolved one does not
    assert sides.primary_side([], [_fnd("RATE:bed_temp_avg")]) == "heat"
    assert sides.primary_side([], [_fnd("RATE:bed_temp_avg", resolved=True)]) == "water"
    long = Fact(id="F5", check="BALANCE", tags=["feed_water_flow", "steam_flow", "drum_level"],
                window=(1, 1), value=7.8, severity="ALARM",
                detail="water balance residual +7.8 TPH (feed 61, steam 68): measured inflow exceeds ...")
    assert sides.other_side_line([long], "heat") == \
        "water side: [BALANCE/ALARM] water balance residual +7.8 TPH (feed 61, steam 68)"
    ids = [_fact(10, "LIMIT", ["drum_level"], "ALARM"), _fact(2, "RATE", ["drum_level"], "ALARM")]
    assert sides.other_side_line(ids, "heat").endswith("d2")   # F2 before F10


def _nf(i, pairs, status="ok"):
    return {"id": f"N{i}", "status": status, "pairs": pairs}


def test_fingerprint_changes_only_with_that_sides_evidence():
    sig = {("drum_level", "DOWN", "FAST"): 1.0, ("bed_temp_avg", "FLAT", "-"): 0.35}
    n1 = _nf(1, [["drum_level", "LOW"]])
    fw = sides.side_fingerprint(sig, [_fnd("LIMIT:drum_level")], [n1], "water")
    sig2 = {("drum_level", "DOWN", "FAST"): 1.0, ("bed_temp_avg", "DOWN", "SLOW"): 1.0}
    assert sides.side_fingerprint(sig2, [_fnd("LIMIT:drum_level")], [n1], "water") == fw
    assert sides.side_fingerprint(sig2, [], [n1], "heat") != sides.side_fingerprint(sig, [], [n1], "heat")
    assert sides.side_fingerprint(sig, [_fnd("LIMIT:drum_level")],
                                  [n1, _nf(2, [["FEED_VALVE", "STUCK"]])], "water") != fw   # new note
    assert sides.side_fingerprint(sig, [_fnd("LIMIT:drum_level")],
                                  [n1, _nf(3, [["bed_temp_avg", "HIGH"]])], "water") == fw  # heat note
    assert sides.side_fingerprint(sig, [_fnd("LIMIT:drum_level")],
                                  [n1, _nf(4, [["COAL", "WET"]], "rejected")], "water") == fw
    assert sides.side_fingerprint(sig, [], [n1], "water") != fw                         # closed
    assert sides.side_fingerprint(sig, [_fnd("LIMIT:drum_level", resolved=True)], [n1], "water") != fw
    assert sides.side_fingerprint(sig, [_fnd("LIMIT:drum_level"), _fnd("RATE:bed_temp_avg")],
                                  [n1], "water") == fw                                  # heat finding
    esc = Finding(id="X", signature_key="LIMIT:drum_level", first_tick=1, last_tick=1,
                  severity="CRITICAL", detail="")
    assert sides.side_fingerprint(sig, [esc], [n1], "water") != fw                      # escalation


DEV = ROOT / "data/episodes_dev"


def _dev_facts():
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    out = []
    for ep in ("dev_A01_fcv_seize", "dev_B03_tube_leak_slow", "dev_D02_low_primary_air"):
        run = run_episode(Episode(DEV / ep), cfg, arch="single")
        out += [Fact(**{k: (tuple(v) if k == "window" else v) for k, v in f.items()})
                for a in run["assessments"] for f in a["facts"]]
    return out


@pytest.mark.skipif(not (DEV / "dev_D02_low_primary_air").exists(), reason="dev episodes not generated")
def test_every_pattern_l1_emits_on_dev_is_in_the_side_table():
    facts = _dev_facts()
    patterns = {frozenset(f.tags) for f in facts if f.check == "PATTERN"}
    assert patterns and patterns <= set(sides.PATTERN_SIDE)
    # and a "water side only" pattern never makes the heat side active
    wso = [f for f in facts if f.check == "PATTERN" and "water side only" in f.detail]
    assert wso and all(sides.active_sides([f], []) == ["water"] for f in wso)
