"""Accuracy-fix: a verifier verdict may only cap confidences. It must never
drop, replace or demote a published hypothesis under any merge rule, or the
cross-rule replay of group top-1 would not be exact. The verifier is forced to
FAIL every claim (and, separately, only the first claim: failing all of them
cannot reveal a demotion, since every claim would move together) on every
eligible tick; the published ORDER must equal the order with a verifier that
never runs, tick for tick."""

from pathlib import Path

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.runtime.llm_backend import MockBackend, register_backend

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
EPISODES = ("dev_B01_tube_leak", "dev_D01_high_cv_coal")


class FailAllMock(MockBackend):
    name = "mock_failall"
    fail = staticmethod(lambda i: True)

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        r = super().generate(prompt, role=role, max_tokens=max_tokens, mock_hint=mock_hint)
        if role == "verifier":
            n = (mock_hint or {}).get("n_claims", 0)
            r.text = '{"v":[' + ",".join(f'[{i},"{"f" if self.fail(i) else "p"}"]'
                                          for i in range(1, n + 1)) + '],"c":1}'
        return r


class FailFirstMock(FailAllMock):
    name = "mock_failfirst"
    fail = staticmethod(lambda i: i == 1)


register_backend("mock_failall", FailAllMock)
register_backend("mock_failfirst", FailFirstMock)


def _run(rule, verifier, ep, backend="mock_failall"):
    from run_demo import _deep_merge
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    _deep_merge(cfg, yaml.safe_load((ROOT / "configs/accuracy.yaml").read_text()))
    cfg["llm"]["backend"] = backend
    cfg["agent"]["verifier"] = verifier
    cfg["multi"]["merge_rule"] = rule
    return run_episode(Episode(DEV / ep), cfg, arch="multi")


@pytest.mark.skipif(not all((DEV / e).exists() for e in EPISODES), reason="dev episodes not generated")
@pytest.mark.parametrize("backend", ["mock_failall", "mock_failfirst"])
@pytest.mark.parametrize("rule", ["tiebreak", "nudge"])
def test_failing_every_claim_changes_no_published_order(rule, backend):
    capped = 0
    for ep in EPISODES:
        fail, off = _run(rule, "always", ep, backend), _run(rule, "never", ep, backend)
        for a, b in zip(fail["assessments"], off["assessments"]):
            assert [h["case_ref"] for h in a["hypotheses"]] == \
                   [h["case_ref"] for h in b["hypotheses"]], (ep, rule, a["tick"])
            for x, y in zip(a["hypotheses"], b["hypotheses"]):
                if x.get("verifier") == "failed check":
                    capped += 1
                    assert x["confidence"] == min(y["confidence"], 0.35)
        assert fail["ver_calls"] > 0 and off["ver_calls"] == 0
    assert capped > 50                       # the verdicts really were applied
