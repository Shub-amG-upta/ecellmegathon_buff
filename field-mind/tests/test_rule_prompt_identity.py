"""Accuracy-fix risk 1: the merge rule must not feed back into what the model
is asked. Runs the same episodes under every merge rule on the mock (with a
verifier that fails claims, so verdicts differ too) and asserts, tick for
tick, that every diagnostician and text-reader prompt is identical and the
call-or-reuse decision is identical. The verifier's prompt shows the
PUBLISHED claims, so it is expected to differ; that is asserted as well, so
the exception is documented by a test, not by prose."""

from pathlib import Path

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.runtime.llm_backend import MockBackend, register_backend

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
EPISODES = ("dev_B01_tube_leak", "dev_D01_high_cv_coal", "dev_C01_wet_coal")
RULES = ("model", "belief_only", "tiebreak", "nudge")


class FailVerMock(MockBackend):
    name = "mock_failver2"

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        r = super().generate(prompt, role=role, max_tokens=max_tokens, mock_hint=mock_hint)
        if role == "verifier":
            n = (mock_hint or {}).get("n_claims", 0)
            r.text = '{"v":[' + ",".join(f'[{i},"{"f" if i == 1 else "p"}"]'
                                          for i in range(1, n + 1)) + '],"c":1}'
        return r


register_backend("mock_failver2", FailVerMock)


def _cfg(rule):
    from run_demo import _deep_merge
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    _deep_merge(cfg, yaml.safe_load((ROOT / "configs/accuracy.yaml").read_text()))
    cfg["llm"]["backend"] = "mock_failver2"
    cfg["agent"]["log_prompts"] = True
    cfg["agent"]["verifier"] = "always"            # verifier on every eligible tick
    cfg["multi"]["merge_rule"] = rule
    return cfg


@pytest.fixture(scope="module")
def runs():
    if not all((DEV / e).exists() for e in EPISODES):
        pytest.skip("dev episodes not generated")
    return {rule: [run_episode(Episode(DEV / e), _cfg(rule), arch="multi") for e in EPISODES]
            for rule in RULES}


def _model_inputs(run):
    """Per tick: every diagnostician and text-reader prompt, in call order, and
    each side's call-or-reuse record."""
    out = []
    for a in run["assessments"]:
        diag = [(e["agent"], e["prompt"]) for e in a["envelopes"] if e["agent"] == "diagnostician"]
        text = [t["envelope"]["prompt"] for t in a["multi"]["text"]]
        calls = [(c.get("side"), c.get("cache"), tuple(c.get("case_ids", ())), tuple(c.get("fact_ids", ())))
                 for c in a["multi"]["compact"] if c.get("agent") == "diagnostician"]
        reuse = [(tuple(c["order"]), tuple(c["reused"])) for c in a["multi"]["compact"]
                 if c.get("agent") == "merge"]
        out.append((a["tick"], diag, text, calls, reuse))
    return out


def _published(run):
    return [[h["case_ref"] for h in a["hypotheses"]] for a in run["assessments"]]


def test_the_rules_really_publish_different_rankings(runs):
    base = [_published(r) for r in runs["model"]]
    for rule in RULES[1:]:
        assert [_published(r) for r in runs[rule]] != base, rule


@pytest.mark.parametrize("rule", RULES[1:])
def test_diagnostician_and_text_reader_prompts_identical_tick_for_tick(runs, rule):
    for a, b in zip(runs["model"], runs[rule]):
        ia, ib = _model_inputs(a), _model_inputs(b)
        assert len(ia) == len(ib)
        n = 0
        for x, y in zip(ia, ib):
            assert x == y, (a["episode_id"], rule, x[0])
            n += len(x[1]) + len(x[2])
        assert n > 20                                   # it compared real prompts


def test_verifier_prompts_do_depend_on_the_published_ranking(runs):
    def ver(r):
        return [[e["prompt"] for e in a["envelopes"] if e["agent"] == "verifier"]
                for a in r["assessments"]]
    assert any(ver(a) != ver(b) for a, b in zip(runs["model"], runs["tiebreak"]))
