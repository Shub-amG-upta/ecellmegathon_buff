"""Failure-path parity between --arch single and --arch multi.

The mock backend never fails, so the dev gate only covers the happy path. This
backend is scripted by call index (identical call order in both arches is
itself gate clause 2) to produce every failure the gate has to handle:
an invalid answer repaired, an invalid answer after repair (invalid_schema ->
degraded marker), a timeout, citations of facts that do not exist (low
faithfulness -> deterministic ranking kept), a model-only cause, a verifier
that fails a claim, one that revises confidence, and one that errors.

The test asserts the paths actually occurred, then that both arches emit
identical decision fields.
"""

import json
from pathlib import Path

import pytest
import yaml

from fieldmind.runtime.llm_backend import LLMBackend, LLMReply, register_backend

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ["dev_A02_fcv_seize_fast", "dev_C02_feeder_trip",
            "dev_D03_high_cv_severe"]   # D03: URGENT ticks with >6 facts


class ScriptedBackend(LLMBackend):
    name = "scripted"

    def __init__(self, **_):
        self.n = {"diagnostician": 0, "verifier": 0}
        self.last_mode = None

    def _reply(self, text, status="ok"):
        return LLMReply(text=text, status=status, backend="scripted",
                        model="scripted-0", prefill_tokens=100, decode_tokens=20)

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        hint = mock_hint or {}
        if role == "diagnostician" and prompt.startswith("Your previous output"):
            # repair call: works after mode 1, stays broken after mode 2
            return self._diag(0 if self.last_mode == 1 else 2, prompt, hint)
        k = self.n.get(role, 0)
        self.n[role] = k + 1
        if role == "diagnostician":
            self.last_mode = k % 7
            return self._diag(k, prompt, hint)
        return self._ver(k, hint)

    def _diag(self, k, prompt, hint):
        cases = hint.get("cases", [])
        fids = [f["id"] for f in hint.get("facts", [])]
        def answer(cites, cause=None):
            hyps = [{"cause": cause or c.get("root_cause", "?"), "confidence": 0.6,
                     "supports": cites, "case_ref": None if cause else c.get("case_id"),
                     "discriminator": "scripted"} for c in cases[:2]] or \
                   [{"cause": cause or "unknown", "confidence": 0.4, "supports": cites}]
            return json.dumps({"headline": "scripted", "hypotheses": hyps, "unexplained": ["u"]})
        mode = k % 7
        if mode == 0:
            return self._reply(answer(fids[:2]))
        if mode == 1:                                  # broken, then the repair works
            return self._reply("not json at all")
        if mode == 2:                                  # broken twice -> invalid_schema
            return self._reply('{"hypotheses": "nope"}')
        if mode == 3:
            return self._reply("", status="timeout")
        if mode == 4:                                  # cites facts that do not exist
            return self._reply(answer(["F98", "F99"]))
        if mode == 5:                                  # a cause no case carries
            return self._reply(answer(fids[:1], cause="scripted model-only cause"))
        return self._reply(answer(list(reversed(fids))[:3]))

    def _ver(self, k, hint):
        claims = hint.get("claims", {}).get("hypotheses", [])
        top = claims[0]["cause"] if claims else ""
        mode = k % 4
        if mode == 0:
            v = {"checks": [{"claim": top, "verdict": "fail"}],
                 "strongest_contradiction": "F1", "revised_confidence": None, "agree": False}
        elif mode == 1:
            v = {"checks": [], "strongest_contradiction": None,
                 "revised_confidence": 0.42, "agree": True}
        elif mode == 2:
            return self._reply("", status="error")
        else:
            v = {"checks": [], "strongest_contradiction": None,
                 "revised_confidence": None, "agree": True}
        return self._reply(json.dumps(v))


register_backend("scripted", ScriptedBackend)


@pytest.mark.skipif(not all((ROOT / "data/episodes_dev" / e).exists() for e in EPISODES),
                    reason="dev episodes not generated")
def test_failure_paths_identical_single_vs_multi():
    from bench.compare_runs import compare_runs
    from bench.harness import Episode, run_episode

    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "scripted"
    cfg["agent"]["verifier"] = "always"            # exercise every verifier path
    cfg["multi"]["compact"] = {}                   # Phase 1 path: parity with single
    cfg["agent"]["log_prompts"] = True             # envelopes carry the prompt text,
                                                   # so a prompt-only change is compared
    singles, multis = [], []
    for e in EPISODES:
        ep = Episode(ROOT / "data/episodes_dev" / e)
        singles.append(run_episode(ep, cfg))
        multis.append(run_episode(ep, cfg, arch="multi"))

    # the paths really happened (otherwise parity proves nothing)
    asmts = [a for r in singles for a in r["assessments"]]
    envs = [x for a in asmts for x in a["envelopes"]]
    status = {x["status"] for x in envs}
    assert {"ok", "invalid_schema", "timeout", "error"} <= status
    assert any(len(x["calls"]) == 2 and x["status"] == "ok" for x in envs)   # repaired
    assert any(a["degraded_mode"] for a in asmts)
    assert any("non-existent" in u for a in asmts for u in a["unexplained"])
    hyps = [h for a in asmts for h in a["hypotheses"]]
    assert any(h.get("verifier") == "failed check" for h in hyps)
    assert any(h.get("verifier") == "confidence revised" for h in hyps)
    assert any(h.get("model_only") for h in hyps)

    assert compare_runs(singles, multis) == []
