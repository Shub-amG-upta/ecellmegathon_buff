"""Accuracy-fix decision 1: the merge rules (fieldmind/multi/merge_rules.py).
Unit tests on the pure functions, plus the gate's per-tick log on an episode.
Assertions are on the published hypotheses, never on the rule's arithmetic."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from fieldmind.agent.orchestrator import initial_claims
from fieldmind.multi import merge_rules as mr


def H(ref, lo, conf=None, ins=0):
    import math
    return SimpleNamespace(case_ref=ref, cause=f"cause {ref}", log_odds=lo,
                           confidence=round(conf if conf is not None else 1 / (1 + math.exp(-lo)), 3),
                           supports=[f"F{ins}"], discriminator=f"d {ref}", retired=False)


def M(*refs):
    return [{"case_ref": r, "supports": ["F9"]} for r in refs]


def refs(hyps):
    return [h["case_ref"] for h in hyps]


# ---------------------------------------------------------------- belief_only
def test_belief_only_is_exactly_initial_claims_of_rank_hypotheses():
    live = [H("A", 0.1, ins=1), H("B", 2.0, ins=2), H("C", 0.1, ins=3), H("D", 1.0, ins=4)]
    ranked = sorted(live, key=lambda h: -h.confidence)[:3]       # rank_hypotheses
    got, info = mr.belief_only(live)
    assert got == initial_claims("x", ranked)["hypotheses"]
    assert refs(got) == ["B", "D", "A"]                           # A before C: insertion order
    assert info["differs_from_belief"] is False


# ------------------------------------------------------------------- tiebreak
def test_tiebreak_reorders_only_within_the_band():
    live = [H("A", 3.0), H("B", 2.8), H("C", 2.5), H("D", 0.0)]   # band >= 2.65: A, B
    got, info = mr.tiebreak(live, M("D", "C", "B"))
    assert refs(got) == ["B", "A", "C"]
    assert info["leaders"] == ["A", "B"] and info["model_in_band"] == ["B"]
    assert info["model_outside_band"] == ["D", "C"] and info["differs_from_belief"]


@pytest.mark.parametrize("model", [M("D"), M("C"), M("D", "C"), M("C", "D", "B")])
def test_tiebreak_never_lifts_a_case_from_outside_the_band(model):
    live = [H("A", 3.0), H("B", 2.8), H("C", 2.5), H("D", 0.0)]
    got, _ = mr.tiebreak(live, model)
    assert set(refs(got)[:2]) == {"A", "B"}                       # leaders stay on top
    assert refs(got)[2] == "C"                                     # C keeps its belief place


def test_tiebreak_band_edge_is_inclusive_at_0_35():
    live = [H("A", 1.0), H("B", 0.65), H("C", 0.6499)]
    got, info = mr.tiebreak(live, M("C", "B"))
    assert info["leaders"] == ["A", "B"]
    assert refs(got) == ["B", "A", "C"]


def test_tiebreak_takes_cases_beyond_belief_top3_when_they_tie_the_leader():
    live = [H("A", 4.0, ins=1), H("B", 4.0, ins=2), H("C", 4.0, ins=3), H("D", 4.0, ins=4)]
    got, _ = mr.tiebreak(live, M("D"))
    assert refs(got) == ["D", "A", "B"]


def test_tiebreak_keeps_belief_confidence_and_model_citations_only_for_its_cases():
    live = [H("A", 1.0), H("B", 0.9)]
    got, _ = mr.tiebreak(live, M("B"))
    assert [h["confidence"] for h in got] == [live[1].confidence, live[0].confidence]
    assert got[0]["supports"] == ["F9"] and got[0].get("tiebreak")
    assert got[1]["supports"] == list(live[0].supports) and not got[1].get("tiebreak")


def test_tiebreak_without_an_answer_is_belief_order():
    live = [H("A", 0.2), H("B", 1.0), H("C", 0.5)]
    assert refs(mr.tiebreak(live, [])[0]) == refs(mr.belief_only(live)[0])


# ---------------------------------------------------------------------- nudge
def test_nudge_rank1_and_rank2_steps_once_per_case_per_tick():
    off = mr.add_nudges({}, M("A", "B", "C"))
    assert off == {"A": 0.35, "B": 0.175}
    assert mr.add_nudges({}, M("A", "A")) == {"A": 0.35}


def test_nudge_respects_the_per_episode_cap():
    off = {}
    for _ in range(10):
        off = mr.add_nudges(off, M("A", "B"))
    assert off == {"A": 1.2, "B": 1.2}
    live = [H("Z", 3.0), H("A", 1.79)]
    got, _ = mr.nudge(live, off)
    assert refs(got) == ["Z", "A"]           # 1.79 + 1.2 = 2.99 < 3.0: the cap holds
    got, _ = mr.nudge([H("Z", 3.0), H("A", 1.81)], off)
    assert refs(got) == ["A", "Z"]


def test_nudge_orders_by_offset_log_odds_and_reports_confidence_of_the_sum():
    live = [H("A", 1.0), H("B", 0.8)]
    got, info = mr.nudge(live, {"B": 0.35})
    assert refs(got) == ["B", "A"] and info["differs_from_belief"]
    assert got[0]["confidence"] == round(1 / (1 + 2.718281828459045 ** -1.15), 3)
    assert got[0].get("nudged") and not got[1].get("nudged")


def test_nudge_with_no_offsets_is_belief_order():
    live = [H("A", 0.2), H("B", 1.0), H("C", 0.5)]
    assert refs(mr.nudge(live, {})[0]) == refs(mr.belief_only(live)[0])


# ------------------------------------------------------------------- episodes
ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"


def _run(rule, ep="dev_D01_high_cv_coal"):
    from bench.harness import Episode, run_episode
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    from run_demo import _deep_merge
    _deep_merge(cfg, yaml.safe_load((ROOT / "configs/accuracy.yaml").read_text()))
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["merge_rule"] = rule
    return run_episode(Episode(DEV / ep), cfg, arch="multi")


@pytest.mark.skipif(not (DEV / "dev_D01_high_cv_coal").exists(), reason="dev episodes not generated")
@pytest.mark.parametrize("rule", ["belief_only", "tiebreak", "nudge"])
def test_every_non_quiet_tick_logs_the_rule_and_whether_it_differs_from_belief(rule):
    from bench.replay_multi import published, replay
    run = _run(rule)
    for a in run["assessments"]:
        logs = [c for c in a["multi"]["compact"] if c.get("agent") == "rule"]
        if a["triage"] == "QUIET":
            assert logs == []
            continue
        (log,) = logs
        assert log["rule"] == rule and log["why"]
        top = [h["case_ref"] for h in a["hypotheses"][:3]]
        b = sorted(a["belief_ranking"], key=lambda x: -x["confidence"])
        order = {c: i for i, c in enumerate(a["belief_order"])}
        b = sorted(b, key=lambda x: (-x["confidence"], order[x["case_ref"]]))
        assert log["differs_from_belief"] == (top != [x["case_ref"] for x in b[:3]])
    # and the replay of the same rule reproduces the run exactly
    assert all(x["hyps"] == y["hyps"] for x, y in zip(replay(run, rule), published(run)))


@pytest.mark.skipif(not (DEV / "dev_D01_high_cv_coal").exists(), reason="dev episodes not generated")
def test_nudge_offsets_live_in_their_own_section_and_belief_is_untouched():
    a = _run("belief_only")["assessments"]
    b = _run("nudge")["assessments"]
    assert [x["belief_ranking"] for x in a] == [x["belief_ranking"] for x in b]
    offs = [c["offsets"] for x in b for c in x["multi"]["compact"] if c.get("agent") == "rule"]
    assert offs[-1] and max(offs[-1].values()) <= mr.CAP


def test_unknown_rule_and_rule_without_split_are_refused():
    from bench.harness import build_multi_agent
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["merge_rule"] = "majority"
    with pytest.raises(ValueError):
        build_multi_agent(cfg, [])
    cfg["multi"]["merge_rule"] = "tiebreak"
    cfg["multi"]["split"] = False
    with pytest.raises(ValueError):
        build_multi_agent(cfg, [])
