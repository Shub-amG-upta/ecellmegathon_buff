"""Accuracy-fix step 0: the offline replay must reproduce what the gate
published, tick for tick, from the saved run alone; board runs save every
prompt and reply. Outcome-based: replay output against the emitted run."""

import json
from pathlib import Path

import pytest
import yaml

from bench.harness import Episode, run_episode
from bench.replay_multi import published, replay
from fieldmind.runtime.llm_backend import MockBackend, register_backend


class FailingVerifierMock(MockBackend):
    """The mock, except the compact verifier fails claim 2 (the mock's own
    verifier always agrees, so it never changes a published ranking)."""
    name = "mock_failver"

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        r = super().generate(prompt, role=role, max_tokens=max_tokens, mock_hint=mock_hint)
        if role == "verifier":
            n = (mock_hint or {}).get("n_claims", 0)
            r.text = '{"v":[' + ",".join(f'[{i},"{"f" if i == 2 else "p"}"]'
                                          for i in range(1, n + 1)) + '],"c":1}'
        return r


register_backend("mock_failver", FailingVerifierMock)

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
needs_dev = pytest.mark.skipif(not (DEV / "dev_B01_tube_leak").exists(),
                               reason="dev episodes not generated")


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
    return a


def _cfg():
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    _merge(cfg, yaml.safe_load((ROOT / "configs/accuracy.yaml").read_text()))
    cfg["llm"]["backend"] = "mock"
    cfg["agent"]["log_prompts"] = True
    cfg["multi"]["merge_rule"] = "model"       # step 0: the unchanged gate (baseline 3)
    return cfg


@pytest.fixture(scope="module")
def runs():
    if not (DEV / "dev_B01_tube_leak").exists():
        pytest.skip("dev episodes not generated")
    # B01: water side, verifier fires; D01: heat side, ties among clamped cases
    out = [run_episode(Episode(DEV / e), _cfg(), arch="multi")
           for e in ("dev_B01_tube_leak", "dev_D01_high_cv_coal")]
    cfg = _cfg()
    cfg["llm"]["backend"] = "mock_failver"
    out.append(run_episode(Episode(DEV / "dev_B01_tube_leak"), cfg, arch="multi"))
    return out


def test_replay_of_the_current_rule_reproduces_the_published_ranking(runs):
    for r in runs:
        rep, pub = replay(r, "current"), published(r)
        assert len(rep) == len(pub)
        diffs = [x["tick"] for x, y in zip(rep, pub) if x["hyps"] != y["hyps"]]
        assert diffs == [], (r["episode_id"], diffs[:5])
        assert sum(1 for x in pub if x["hyps"]) > 50          # it compared something


def test_the_runs_exercise_reuse_the_verifier_and_belief_ties(runs):
    recs = [c for r in runs for a in r["assessments"] for c in a["multi"]["compact"]]
    assert any(c.get("agent") == "merge" and c["reused"] for c in recs)
    assert any(c.get("agent") == "verifier" for c in recs)
    hyps = [h for a in runs[2]["assessments"] for h in a["hypotheses"]]
    assert any(h.get("verifier") == "failed check" for h in hyps)   # a verdict changed output
    tied = 0
    for r in runs:
        for a in r["assessments"]:
            conf = sorted((b["confidence"] for b in a.get("belief_ranking") or []),
                          reverse=True)[:4]
            tied += len(conf) != len(set(conf))
    assert tied > 0


def test_belief_rule_publishes_belief_order_only(runs):
    for r in runs:
        for x, a in zip(replay(r, "belief"), r["assessments"]):
            if x["hyps"]:
                assert all(f == () for _, _, f in x["hyps"])       # no model flags
                assert len(x["hyps"]) <= 3


def test_belief_order_is_live_hypotheses_in_insertion_order(runs):
    for r in runs:
        for a in r["assessments"]:
            if a["triage"] == "QUIET":
                assert a["belief_order"] == []
            else:
                assert sorted(a["belief_order"]) == sorted(b["case_ref"] for b in a["belief_ranking"])


def test_board_runs_save_every_prompt_and_raw_reply(runs):
    envs = [e for r in runs for a in r["assessments"] for e in a["envelopes"]]
    assert envs and all(e["prompt"] and e["raw_reply"] for e in envs)
    text = [t["envelope"] for r in runs for a in r["assessments"] for t in a["multi"]["text"]]
    assert text and all(t["prompt"] and t["raw_reply"] for t in text)


def test_board_runner_passes_log_prompts_by_default():
    s = (ROOT / "scripts/benchmark_multi_lockstep_all.sh").read_text()
    assert "LOG_PROMPTS=${LOG_PROMPTS:-1}" in s and '"${PROMPT_FLAG[@]}"' in s
    assert "PROMPT_FLAG=(--log-prompts)" in s


def test_accuracy_overlay_runs_one_model_on_both_lanes():
    lanes = yaml.safe_load((ROOT / "configs/accuracy.yaml").read_text())["multi"]["lanes"]
    assert lanes["npu"]["model_file"] == lanes["cpu"]["model_file"] \
        == "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf"
