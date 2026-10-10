"""Guards for Phase 0a rules that the first round left untested (phase-reviewer findings)."""
from pathlib import Path

import pytest
import yaml

from bench.evaluator import LOW_CONF, t2_root_cause
from fieldmind.agent import world_model as wmod
from fieldmind.agent.l4_diagnose import Diagnostician
from fieldmind.agent.l5_verify import Verifier
from fieldmind.kb.stores import CaseLibrary
from fieldmind.runtime.llm_backend import MockBackend
from fieldmind.schemas import Fact

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "configs/base.yaml").read_text())


# ------------------------------------------------------------------ config
def test_committed_defaults():
    """Mock by default (board only on explicit --backend), answer cap 256, retirement OFF."""
    assert CFG["llm"]["backend"] == "mock"
    assert CFG["agent"]["max_tokens"] == 256
    assert CFG["agent"]["belief"]["retire_after_ticks"] is None


class Capture(MockBackend):
    def __init__(self):
        super().__init__()
        self.caps = []

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        self.caps.append((role, max_tokens))
        return super().generate(prompt, role, max_tokens, mock_hint)


def test_max_tokens_reaches_both_model_calls_from_config():
    cfg = {**CFG["agent"], "max_tokens": 77, "log_prompts": False}
    b = Capture()
    facts = [Fact("F1", "RATE", ["drum_level"], (0, 10), -2.8, "level falling", "WATCH")]
    retrieved = {"cases": [{"case_id": "RCA-01", "score": 0.2, "root_cause": "x", "title": "t",
                            "discriminating_evidence": "d"}], "candidates": ["valve"],
                 "notes": [], "records": {}, "experience": [], "operator_query": ""}
    Diagnostician(b, cfg).run(1, "F1 level falling", retrieved, facts, "none")
    Verifier(b, cfg).run(1, {"hypotheses": [{"rank": 1, "cause": "c", "confidence": 0.5,
                                             "supports": ["F1"], "discriminator": ""}]}, facts)
    assert b.caps == [("diagnostician", 77), ("verifier", 77)]


# --------------------------------------------------- belief update bounds
def case(sig, contra=None):
    return {"case_id": "X", "root_cause": "x", "signature": sig,
            "contradicting_signature": contra or {}}


FIVE = {t: ["DOWN", "FAST"] for t in ("drum_level", "drum_pressure", "bed_temp_avg", "steam_flow", "ms_temperature")}
FIVE_SIG = {(t, "DOWN", "FAST"): 1.0 for t in FIVE}


def test_per_tick_move_is_bounded_and_log_odds_saturate_at_the_clamp():
    wm = wmod.new_world_model("e", [])
    wmod.update_hypotheses(wm, [], FIVE_SIG, [case(FIVE)], tick=0)
    assert wm.hypotheses[0].log_odds == pytest.approx(1.2)            # 5 supports = 1.75, bounded to 1.2
    for t in range(1, 12):
        wmod.update_hypotheses(wm, [], FIVE_SIG, [case(FIVE)], tick=t)
    assert wm.hypotheses[0].log_odds == wmod.CLAMP == 4.0
    assert wm.hypotheses[0].confidence == pytest.approx(0.982, abs=1e-3)


def test_a_flat_observation_never_contradicts():
    """The > 0.5 threshold excludes FLAT (weight 0.35): even a listed FLAT triple that is observed is no hit."""
    c = case({}, {"bed_temp_avg": ["FLAT", "-"]})
    assert CaseLibrary.contradiction_hits(c, {("bed_temp_avg", "FLAT", "-"): 0.35}) == []
    assert CaseLibrary.contradiction_hits(c, {("bed_temp_avg", "FLAT", "-"): 1.0}) != []


def _fact(i, check, tags, sev="WATCH"):
    return Fact(i, check, tags, (0, 1), 0.0, "d", sev)


def test_balance_facts_are_cited_for_the_right_pseudo_tag_only():
    water = _fact("w", "BALANCE", ["feed_water_flow", "steam_flow"])
    energy = _fact("e", "BALANCE", ["drum_pressure"])
    wm = wmod.new_world_model("e", [])
    wmod.update_hypotheses(wm, [water, energy], {("water_balance", "DEFICIT", "MED"): 1.0},
                           [case({"water_balance|DEFICIT|MED": 1.0})], tick=0)
    assert wm.hypotheses[0].supports == ["w"]
    wm = wmod.new_world_model("e", [])
    wmod.update_hypotheses(wm, [water, energy], {("energy_balance", "HEAT_SHORT", "-"): 1.0},
                           [case({"energy_balance|HEAT_SHORT|-": 1.0})], tick=0)
    assert wm.hypotheses[0].supports == ["e"]


def test_info_facts_are_never_cited():
    info, watch = _fact("i", "RATE", ["drum_level"], "INFO"), _fact("w", "RATE", ["drum_level"], "WATCH")
    wm = wmod.new_world_model("e", [])
    wmod.update_hypotheses(wm, [info, watch], {("drum_level", "DOWN", "FAST"): 1.0},
                           [case({"drum_level": ["DOWN", "FAST"]})], tick=0)
    assert wm.hypotheses[0].supports == ["w"]


# ------------------------------------------------------ evaluator boundaries
def run_at(tick, conf=0.9, belief=None, ranks=("RCA-14",), target="RCA-14", onset=60):
    hyps = [{"case_ref": c, "confidence": conf, "discriminator": "d"} for c in ranks]
    brk = [{"case_ref": c, "log_odds": lo, "confidence": 0.5} for c, lo in (belief or [("RCA-14", 1.0)])]
    return {"ground_truth": {"root_cause_id": target, "fault_onset_t": onset},
            "assessments": [{"tick": tick, "hypotheses": hyps, "belief_ranking": brk}]}


def test_scoring_starts_at_onset_inclusive():
    assert t2_root_cause(run_at(2, onset=60))["n_scored"] == 1      # tick 2 * 30 s == onset: counts
    assert t2_root_cause(run_at(1, onset=60))["n_scored"] == 0      # before onset: not scored


def test_low_conf_is_inclusive_at_the_threshold():
    r = t2_root_cause(run_at(2, conf=LOW_CONF, target="RCA-06", ranks=("RCA-14",)))
    assert LOW_CONF == 0.5 and r["low_conf_rate"] == 1.0
    assert t2_root_cause(run_at(2, conf=0.5001, target="RCA-06", ranks=("RCA-14",)))["low_conf_rate"] == 0.0


def test_belief_top3_means_three():
    ranks = [("RCA-03", 3.0), ("RCA-04", 2.0), ("RCA-05", 1.0), ("RCA-14", 0.5)]
    assert t2_root_cause(run_at(2, belief=ranks))["belief_top3"] == 0.0     # 4th place does not count
    ranks[2], ranks[3] = ranks[3], ranks[2]
    assert t2_root_cause(run_at(2, belief=ranks))["belief_top3"] == 1.0     # 3rd place does


def test_tie_rate_needs_the_top_two_to_be_equal():
    tie = [("RCA-14", 2.0), ("RCA-03", 2.0)]
    assert t2_root_cause(run_at(2, belief=tie))["belief_top_tie_rate"] == 1.0
    assert t2_root_cause(run_at(2, belief=[("RCA-14", 2.0), ("RCA-03", 1.9)]))["belief_top_tie_rate"] == 0.0


def test_tie_fair_group_splits_credit_across_the_tied_top_set():
    # true group T = {RCA-14, RCA-18}. Top set tied: RCA-14 (in group), RCA-03 (not)
    r = t2_root_cause(run_at(2, belief=[("RCA-14", 2.0), ("RCA-03", 2.0), ("RCA-18", 0.0)]))
    assert r["belief_group"] == 1.0 and r["belief_group_tiefair"] == 0.5
    r = t2_root_cause(run_at(2, belief=[("RCA-03", 2.0), ("RCA-14", 2.0)]))
    assert r["belief_group"] == 0.0 and r["belief_group_tiefair"] == 0.5


# ------------------------------------------------------- telemetry wiring
def test_orchestrator_emits_a_sorted_belief_ranking():
    from bench.harness import Episode, SensorWindow, build_agent
    ep_dir = ROOT / "data/episodes/ep_A01_fcv_seize"
    if not ep_dir.exists():
        pytest.skip("data/episodes is generated and gitignored")
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    ep = Episode(ep_dir)
    orch, asset, *_ = build_agent(cfg, ep.notes, ep.records)
    win = SensorWindow(window_min=60.0, sample_period_s=cfg["checks"]["sample_period_s"])
    wm = wmod.new_world_model(ep.id, asset.equipment)
    got = []
    for k in range(90):
        for r in ep.samples_between(k * 30, (k + 1) * 30):
            win.append(r["t"], r)
        if win.ready(min_minutes=5.0):
            got.append(orch.tick(wm, win, k, "x", now_s=(k + 1) * 30).belief_ranking)
    nonempty = [b for b in got if b]
    assert nonempty, "belief_ranking was never populated"
    for b in nonempty:
        assert [x["log_odds"] for x in b] == sorted((x["log_odds"] for x in b), reverse=True)
