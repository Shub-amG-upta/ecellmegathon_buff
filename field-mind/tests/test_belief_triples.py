"""Belief update compares (tag, direction, band) TRIPLES, like retrieval (known bug 6)."""
from pathlib import Path

import pytest
import yaml

from fieldmind.agent import orchestrator as om
from fieldmind.agent import world_model as wmod
from fieldmind.kb.stores import CaseLibrary
from fieldmind.schemas import Fact

ROOT = Path(__file__).resolve().parent.parent
LIB = CaseLibrary(ROOT / "data/kb/case_library.json")
RCA01 = next(c for c in LIB.cases if c["case_id"] == "RCA-01")


def sig(bed):
    """RCA-01's own six-tag signature, with the bed triple chosen by the test."""
    s = {("drum_level", "DOWN", "FAST"): 1.0, ("feed_water_flow", "FLAT", "-"): 0.35,
         ("steam_flow", "FLAT", "-"): 0.35, ("drum_pressure", "FLAT", "-"): 0.35,
         ("water_balance", "DEFICIT", "MED"): 1.0}
    s[("bed_temp_avg",) + bed] = 0.35 if bed[0] == "FLAT" else 1.0
    return s


# a bed-tagged non-INFO fact, so a charge shows up as cited contradiction ids
BED_FACT = Fact(id="t.F1", check="PATTERN", tags=["drum_level", "bed_temp_avg"],
                window=(0, 1), value=0.0, detail="heat side untouched", severity="WATCH")


def step(bed, start=0.0):
    wm = wmod.new_world_model("ep_unit", [])
    wmod.update_hypotheses(wm, [BED_FACT], sig(bed), [RCA01], tick=0)
    h = wm.hypotheses[0]
    return h


def test_flat_bed_is_not_a_contradiction_of_rca01():
    h = step(("FLAT", "-"))
    assert h.contradicts == []
    assert h.supports == ["t.F1"]          # the flat bed is cited as SUPPORT


def test_bed_down_med_contradicts_rca01():
    h = step(("DOWN", "MED"))
    assert h.contradicts == ["t.F1"]
    assert h.log_odds < step(("FLAT", "-")).log_odds


def test_bed_up_med_is_not_the_contradicting_triple():
    assert step(("UP", "MED")).contradicts == []


def test_belief_and_retrieval_share_one_contradiction_function(monkeypatch):
    """Patch the one function: BOTH layers must react. Two implementations
    would let only one of them move."""
    flat = sig(("FLAT", "-"))
    base_score = LIB.match(flat, k=20)
    base_rec = next(c for c in base_score if c["case_id"] == "RCA-01")
    base_h = step(("FLAT", "-"))
    assert base_h.contradicts == []

    monkeypatch.setattr(CaseLibrary, "contradiction_hits",
                        staticmethod(lambda case, signature: [("bed_temp_avg", "FLAT", "-")]))
    hit = next(c for c in LIB.match(flat, k=20) if c["case_id"] == "RCA-01")
    assert hit["score"] < base_rec["score"]          # retrieval reacted
    assert step(("FLAT", "-")).contradicts == ["t.F1"]  # belief reacted


# ------------------------------------------------------------ ep_A01 replay
EP = ROOT / "data/episodes/ep_A01_fcv_seize"


def replay_a01(upto):
    from bench.harness import Episode, SensorWindow, build_agent
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    ep = Episode(EP)
    orch, asset, *_ = build_agent(cfg, ep.notes, ep.records)
    win = SensorWindow(window_min=60.0, sample_period_s=cfg["checks"]["sample_period_s"])
    wm = wmod.new_world_model(ep.id, asset.equipment)
    seen = {}
    real = wmod.update_hypotheses

    def spy(wm_, facts, signature, cands, tick, **kw):
        real(wm_, facts, signature, cands, tick, **kw)
        if any(c["case_id"] == "RCA-01" for c in cands):
            h = next(x for x in wm_.hypotheses if x.case_ref == "RCA-01")
            seen[tick] = {"signature": dict(signature), "contradicts": list(h.contradicts)}

    om.wmod.update_hypotheses = spy
    try:
        for k in range(upto):
            for r in ep.samples_between(k * 30, (k + 1) * 30):
                win.append(r["t"], r)
            if win.ready(min_minutes=5.0):
                orch.tick(wm, win, k, "x", now_s=(k + 1) * 30)
    finally:
        om.wmod.update_hypotheses = real
    return seen


@pytest.fixture(scope="module")
def a01():
    if not EP.exists():
        pytest.skip("data/episodes is generated and gitignored")
    return replay_a01(90)


def test_a01_flat_bed_ticks_do_not_charge_rca01(a01):
    """The CLAUDE.md bug-6 text said tick 76; on the current episodes RCA-01 is
    not even retrieved there. The ticks where it is retrieved with a FLAT bed
    (and the old tag-only rule charged it -0.90) are 56 and 83."""
    for t in (56, 83):
        assert t in a01, f"RCA-01 not retrieved at tick {t}: premise changed"
        assert ("bed_temp_avg", "FLAT", "-") in a01[t]["signature"]
        assert a01[t]["contradicts"] == [], t


def test_a01_rca01_charged_only_when_a_contradicting_triple_is_present(a01):
    triples = CaseLibrary._to_triples(RCA01["contradicting_signature"])
    charged = [t for t, r in a01.items() if r["contradicts"]]
    for t in charged:
        assert any(r > 0.5 and k in triples for k, r in a01[t]["signature"].items()), t
    for t, r in a01.items():
        if not any(w > 0.5 and k in triples for k, w in r["signature"].items()):
            assert r["contradicts"] == [], t


# ------------------------------------------------------- weight scaling (A2)
def _one_triple_case(direction, band):
    return {"case_id": "X", "root_cause": "x", "signature": {"drum_level": [direction, band]},
            "contradicting_signature": {}}


def _delta(case, signature):
    wm = wmod.new_world_model("ep_unit", [])
    wmod.update_hypotheses(wm, [], signature, [case], tick=0)
    return wm.hypotheses[0].log_odds


def test_flat_evidence_moves_belief_less_than_movement():
    flat, moving = _one_triple_case("FLAT", "-"), _one_triple_case("DOWN", "FAST")
    seen_flat = _delta(flat, {("drum_level", "FLAT", "-"): 0.35})
    seen_move = _delta(moving, {("drum_level", "DOWN", "FAST"): 1.0})
    assert 0 < seen_flat < seen_move
    assert seen_flat == pytest.approx(0.35 * seen_move)       # FLAT weight is 0.35
    # ... and a MISSING flat expectation costs less than a missing movement one
    miss_flat = _delta(flat, {("drum_level", "UP", "FAST"): 1.0})
    miss_move = _delta(moving, {("drum_level", "UP", "FAST"): 1.0})
    assert miss_move < miss_flat < 0


def test_contradiction_charge_is_not_scaled():
    case = {"case_id": "X", "root_cause": "x", "signature": {},
            "contradicting_signature": {"bed_temp_avg": ["DOWN", "MED"]}}
    assert _delta(case, {("bed_temp_avg", "DOWN", "MED"): 1.0}) == pytest.approx(wmod.STEP_CONTRA)
