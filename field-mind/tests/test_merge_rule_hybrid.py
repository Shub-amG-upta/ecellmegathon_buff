"""merge_rule: hybrid. Belief weak (best log-odds < 0) or tied (margin < 0.35):
the model's ranking of belief's live, non-flat cases first; clear leader: nudge;
flat cases never nudged. Assertions on the published hypotheses and the run."""

from pathlib import Path

import pytest
import yaml

from fieldmind.multi import merge_rules as mr
from tests.test_merge_rules import H, M, refs

FLAT = frozenset({"F1", "F2"})
ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
needs_dev = pytest.mark.skipif(not (DEV / "dev_D01_high_cv_coal").exists(),
                               reason="dev episodes not generated")


# ---------------------------------------------------------------- regimes
@pytest.mark.parametrize("los,want", [
    ([2.0, 1.0], "clear"), ([2.0, 1.65], "clear"),          # margin 0.35 exactly: clear
    ([2.0, 1.6501], "tie"), ([2.0, 2.0], "tie"),
    ([-0.1, -2.0], "weak"), ([-0.1, -0.1], "weak"),         # weak wins over tie
    ([0.0, -2.0], "clear"), ([3.0], "clear"), ([], "clear")])
def test_regime_boundaries(los, want):
    assert mr.regime([H(f"C{i}", x) for i, x in enumerate(los)]) == want


# ---------------------------------------------------------------- the rule
def test_clear_leader_is_exactly_nudge():
    live = [H("A", 3.0), H("B", 1.0), H("C", 0.5)]
    offs = {"B": 0.35}
    got, info = mr.hybrid(live, M("C", "B"), offs, FLAT)
    want, _ = mr.nudge(live, offs)
    assert [(h["case_ref"], h["confidence"]) for h in got] == \
           [(h["case_ref"], h["confidence"]) for h in want]
    assert info["regime"] == "clear" and not info["model_led"]


@pytest.mark.parametrize("live", [
    [H("A", 2.0), H("B", 1.9), H("C", 0.5)],                    # tie
    [H("A", -0.2), H("B", -0.9), H("C", -1.5)]])                # weak
def test_tie_or_weak_the_models_ranking_leads(live):
    got, info = mr.hybrid(live, M("C", "B"), {}, FLAT)
    assert refs(got) == ["C", "B", "A"] and info["model_led"]
    assert got[0].get("model_first") and not got[2].get("model_first")


def test_flat_cases_never_lead_and_unlisted_cases_are_never_published():
    live = [H("F1", 2.0), H("A", 1.9), H("B", 0.5)]             # tie, belief's leader is flat
    got, info = mr.hybrid(live, M("F2", "X", "F1", "B"), {}, FLAT)
    assert refs(got)[0] == "B"                                  # first usable model case
    assert "X" not in refs(got) and info["model_flat_ignored"] == ["F2", "F1"]
    got, info = mr.hybrid(live, M("F1", "X"), {}, FLAT)         # nothing usable: nudge order
    assert refs(got) == ["F1", "A", "B"] and not info["model_led"]


def test_nothing_is_written_to_belief():
    live = [H("A", 2.0), H("B", 1.9)]
    before = [(h.case_ref, h.log_odds, h.confidence) for h in live]
    mr.hybrid(live, M("B"), {"B": 1.2}, FLAT)
    assert [(h.case_ref, h.log_odds, h.confidence) for h in live] == before


def test_flat_ids_come_from_the_case_data():
    lib = [{"case_id": "P", "signature": {"bed_temp_avg": ["FLAT", "-"]}},
           {"case_id": "Q", "signature": {"bed_temp_avg": ["UP", "FAST"]}},
           {"case_id": "R", "signature": {"steam_flow": ["FLAT", "-"],
                                         "energy_balance|HEAT_SHORT|-": 1.0}}]
    assert mr.flat_case_ids(lib) == {"P"}                       # a balance triple moves


# ---------------------------------------------------------------- episodes
def _run(rule):
    from bench.harness import Episode, run_episode
    from run_demo import _deep_merge
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    _deep_merge(cfg, yaml.safe_load((ROOT / "configs/accuracy.yaml").read_text()))
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["merge_rule"] = rule
    return run_episode(Episode(DEV / "dev_D01_high_cv_coal"), cfg, arch="multi")


@needs_dev
def test_episode_logs_every_tick_replays_exactly_and_never_nudges_a_flat_case():
    from bench.replay_multi import FLAT as LIB_FLAT, published, replay
    run = _run("hybrid")
    regs = set()
    for a in run["assessments"]:
        logs = [c for c in a["multi"]["compact"] if c.get("agent") == "rule"]
        if a["triage"] == "QUIET":
            assert logs == []
            continue
        (log,) = logs
        assert log["rule"] == "hybrid" and log["why"]
        regs.add(log["regime"])
        assert not set(log["offsets"]) & LIB_FLAT
        assert max(log["offsets"].values(), default=0) <= mr.CAP
        if not log["model_led"]:
            assert not any(h.get("model_first") for h in a["hypotheses"])
    assert {"tie", "clear"} <= regs
    assert all(x["hyps"] == y["hyps"] for x, y in zip(replay(run, "hybrid"), published(run)))


@needs_dev
def test_hybrid_leaves_belief_identical_to_belief_only():
    a = _run("belief_only")["assessments"]
    b = _run("hybrid")["assessments"]
    assert [x["belief_ranking"] for x in a] == [x["belief_ranking"] for x in b]


def test_a_case_belief_does_not_hold_is_skipped_not_replaced():
    live = [H("A", 2.0), H("B", 1.9), H("C", 0.5)]              # tie, non-flat leader
    got, info = mr.hybrid(live, M("X", "C"), {}, FLAT)
    assert refs(got) == ["C", "A", "B"] and info["model_led"]
