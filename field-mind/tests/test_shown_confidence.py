"""Fix (c): shown confidence from the rank-1 vs rank-2 margin, DISPLAY ONLY.
Formula cases are in reports/phase0a_session1b_measurement.md, Step 5."""
from pathlib import Path

import pytest
import yaml

from fieldmind.agent import world_model as wmod
from fieldmind.schemas import Hypothesis

ROOT = Path(__file__).resolve().parent.parent


def H(case, lo):
    return Hypothesis(cause=f"cause-{case}", case_ref=case, log_odds=lo,
                      confidence=round(wmod._sigmoid(lo), 3))


def shown(live, order=None):
    order = order or live
    hyps = [{"cause": h.cause, "case_ref": h.case_ref, "confidence": h.confidence}
            for h in order]
    return wmod.shown_confidences(hyps, live)


def test_single_live_hypothesis_is_unchanged():
    for lo in (-1.0, 0.9, 1.998, 3.092, 4.0):
        h = H("A", lo)
        assert shown([h]) == [h.confidence]


def test_margin_over_rival_lowers_but_never_raises():
    a, b = H("A", 4.0), H("B", 1.0)
    assert shown([a, b])[0] == 0.953                       # sigmoid(3), below 0.982
    # negative rival: sigmoid(l1 - l2) >= c1, so the shown value stays c1
    a, b = H("A", 1.0), H("B", -2.0)
    assert shown([a, b])[0] == a.confidence
    for lo1, lo2 in ((4, 3.9), (0.4, 0.3), (2.0, -1.0), (-1.0, -3.0)):
        a, b = H("A", lo1), H("B", lo2)
        assert shown([a, b])[0] <= a.confidence


def test_non_leader_is_at_most_half():
    a, b = H("A", 3.0), H("B", 2.0)
    assert shown([a, b])[1] <= 0.5


def test_two_way_tie_at_the_clamp_shows_half():
    a, b = H("A", 4.0), H("B", 4.0)
    assert shown([a, b]) == [0.5, 0.5]


def test_k_way_tie_shows_half_not_one_over_k():
    trio = [H("RCA-09", 4.0), H("RCA-10", 4.0), H("RCA-15", 4.0)]
    assert shown(trio) == [0.5, 0.5, 0.5]


def test_tie_below_half_keeps_its_own_lower_confidence():
    a, b = H("A", -1.0), H("B", -1.0)
    assert shown([a, b]) == [a.confidence, b.confidence]      # 0.269, not 0.5


def test_both_at_the_floor_stay_at_their_own_confidence():
    a, b = H("A", -4.0), H("B", -4.0)
    assert shown([a, b]) == [0.018, 0.018]                    # not 0.5


def test_model_only_hypothesis_keeps_its_confidence():
    a = H("A", 2.0)
    out = wmod.shown_confidences(
        [{"cause": "novel idea", "case_ref": None, "confidence": 0.4}], [a])
    assert out == [0.4]


def test_only_the_hypotheses_passed_in_are_rivals():
    # shown_confidences judges against `live` only; excluding retired ones is the
    # caller's job (the orchestrator passes `not h.retired`)
    a, b = H("A", 2.0), H("B", -1.0)
    assert shown([a, b])[0] == a.confidence


# ---------------------------------------------------------------- replay
EP = ROOT / "data/episodes_dev/dev_C02_feeder_trip"


@pytest.fixture(scope="module")
def replay():
    if not EP.exists():
        pytest.skip("data/episodes_dev is generated and gitignored")
    from bench.harness import Episode, SensorWindow, build_agent
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    ep = Episode(EP)
    orch, asset, *_ = build_agent(cfg, ep.notes, ep.records)
    win = SensorWindow(window_min=60.0, sample_period_s=cfg["checks"]["sample_period_s"])
    wm = wmod.new_world_model(ep.id, asset.equipment)
    out = []
    for k in range(int(ep.duration_s // 30)):
        for r in ep.samples_between(k * 30, (k + 1) * 30):
            win.append(r["t"], r)
        if win.ready(min_minutes=5.0):
            out.append(orch.tick(wm, win, k, "x", now_s=(k + 1) * 30).to_dict())
    return out


def test_verifier_does_not_fire_on_a_tie_the_display_shows_as_half(replay):
    """The verifier band is [0.35, 0.75] on the DECISION confidence. A tie at the
    clamp has decision 0.982 (outside the band) and shown 0.5 (inside it): if
    the trigger ever read the shown value the verifier would fire on exactly
    these ticks."""
    ties = [a for a in replay
            if a["triage"] in ("INVESTIGATE", "URGENT") and a["hypotheses"]
            and a["hypotheses"][0]["confidence"] > 0.75
            and a["hypotheses"][0]["confidence_shown"] == 0.5]
    assert len(ties) >= 10, "premise changed: no tied clamp ticks to test on"
    for a in ties:
        assert not any(e["agent"] == "verifier" for e in a["envelopes"]), a["tick"]


def test_shown_never_exceeds_decision_and_is_carried_on_every_hypothesis(replay):
    n = 0
    for a in replay:
        for h in a["hypotheses"]:
            assert "confidence_shown" in h
            assert h["confidence_shown"] <= h["confidence"] + 1e-9
            n += 1
        if a["hypotheses"]:
            assert a["confidence"] == a["hypotheses"][0]["confidence_shown"]
    assert n > 100


# ----------------------------------------------- decision paths (no data needed)
FIELDMIND = ROOT / "fieldmind"


def test_confidence_shown_is_referenced_only_by_the_producer_and_the_assembly_step():
    """Structural guard for 'nothing that decides reads the shown value': the verifier,
    diagnostician, retrieval, gate, memory, triage, schemas and runtime never mention it."""
    allowed = {"world_model.py", "orchestrator.py"}
    offenders = []
    for f in FIELDMIND.rglob("*.py"):
        text = f.read_text()
        if ("confidence_shown" in text or "shown_confidences" in text) and f.name not in allowed:
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, offenders


def test_orchestrator_computes_the_shown_value_only_after_verifier_and_gate():
    src = (FIELDMIND / "agent/orchestrator.py").read_text()
    first_use = min(i for i in (src.find("shown_confidences("), src.find("confidence_shown"))
                    if i != -1)
    for decision_call in ("self.ver.should_run(", "self.ver.apply(", "self.gate.approve("):
        assert decision_call in src
        assert first_use > src.rfind(decision_call), decision_call


def test_prompt_summary_carries_the_decision_confidence_not_the_shown_one():
    """_wm_summary feeds the Diagnostician prompt. Two tied hypotheses at the clamp
    have decision 0.98 and shown 0.5; the prompt must say 0.98."""
    from fieldmind.agent.orchestrator import Orchestrator
    wm = wmod.new_world_model("t", [])
    wm.hypotheses = [H("A", 4.0), H("B", 4.0)]
    text = Orchestrator._wm_summary(wm)
    assert "(0.98)" in text and "(0.50)" not in text and "(0.5)" not in text


def test_verifier_trigger_reads_the_value_it_is_given():
    """Should_run is a pure band test on its argument; the orchestrator test above
    pins WHICH value it is given."""
    from fieldmind.agent.l5_verify import Verifier
    v = Verifier.__new__(Verifier)
    v.cfg = {"verifier": "conditional", "verifier_band": [0.35, 0.75]}
    assert v.should_run("INVESTIGATE", 0.982) is False
    assert v.should_run("INVESTIGATE", 0.5) is True
