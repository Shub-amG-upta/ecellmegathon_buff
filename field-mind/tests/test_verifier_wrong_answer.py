"""Phase 3, commit 4: the known-wrong-answer verifier tool. Outcome-based:
assertions on the rewritten answers and on the verdict counts of real runs."""

import copy
import json
from pathlib import Path

import pytest
import yaml

from bench.harness import Episode
from bench.verifier_wrong_answer import WrongRankOne, rank1_verdicts, run_one, true_group
from fieldmind.multi.compact import ALL_SECTIONS
from fieldmind.runtime.llm_backend import LLMBackend, LLMReply, MockBackend

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"


class Fixed(LLMBackend):
    name = "fixed"

    def __init__(self, text):
        self.text = text

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        return LLMReply(text=self.text, prefill_tokens=1, decode_tokens=1)


def test_compact_answer_gets_a_shown_case_outside_the_true_group_at_rank_one():
    w = WrongRankOne(Fixed('{"g":"A","r":[[1,[2]],[2,[1]]],"sep":1,"n":[],"x":[]}'),
                     {"RCA-01", "RCA-16"})
    hint = {"compact": True, "case_lines": ["RCA-01", "RCA-16", "RCA-07"]}
    out = json.loads(w.generate("p", role="diagnostician", mock_hint=hint).text)
    assert out["r"] == [[3, [2]], [1, [2]], [2, [1]]] and w.log == [{"forced": "RCA-07"}]


def test_full_answer_gets_the_wrong_case_first_with_the_models_rank_one_citations():
    ans = {"hypotheses": [{"case_ref": "RCA-01", "cause": "c1", "confidence": 0.6, "supports": ["F1"]},
                          {"case_ref": "RCA-07", "cause": "c7", "confidence": 0.3, "supports": ["F2"]}]}
    w = WrongRankOne(Fixed(json.dumps(ans)), {"RCA-01", "RCA-16"})
    hint = {"cases": [{"case_id": "RCA-01", "root_cause": "c1"},
                      {"case_id": "RCA-07", "root_cause": "c7"}]}
    out = json.loads(w.generate("p", role="diagnostician", mock_hint=hint).text)
    assert [h["case_ref"] for h in out["hypotheses"]] == ["RCA-07", "RCA-01"]
    assert out["hypotheses"][0]["supports"] == ["F1"]


@pytest.mark.parametrize("bad", ['{"r":[[1]]}', '{"r":[5,[1,[2]]]}', '{"r":[[1,[2]],3]}',
                                 '{"r":[[9,[1]]]}', '{"r":"x"}'])
def test_malformed_or_out_of_range_answer_passes_through_untouched(bad):
    w = WrongRankOne(Fixed(bad), {"RCA-01"})
    hint = {"compact": True, "case_lines": ["RCA-01", "RCA-07"]}
    assert w.generate("p", role="diagnostician", mock_hint=hint).text == bad
    assert w.log == [{"forced": None, "why": "malformed answer, not rewritten"}]


def test_no_wrong_case_shown_and_other_roles_are_left_alone():
    text = '{"r":[[1,[1]]]}'
    w = WrongRankOne(Fixed(text), {"RCA-01"})
    assert w.generate("p", role="diagnostician",
                      mock_hint={"compact": True, "case_lines": ["RCA-01"]}).text == text
    assert w.log == [{"forced": None, "why": "no shown case outside the true group"}]
    assert w.generate("p", role="verifier", mock_hint={"compact": True}).text == text
    assert len(w.log) == 1


def test_true_group_uses_the_look_alike_groups():
    assert true_group({"root_cause_id": "RCA-01"}) == {"RCA-01", "RCA-16"}
    assert true_group({"root_cause_id": "NONE"}) is None


def test_rank_one_verdicts_count_fail_pass_not_judged_failed_calls_and_post_onset():
    def asmt(tick, v1, v2="pass", status="ok"):
        checks = [{"claim": "c2", "verdict": v2}] + ([] if v1 is None else [{"claim": "c1", "verdict": v1}])
        return {"tick": tick,
                "hypotheses": [{"case_ref": "RCA-07", "cause": "c1"}, {"case_ref": "RCA-01", "cause": "c2"}],
                "envelopes": [{"agent": "verifier", "status": status, "payload": {"checks": checks}}]}
    run = {"assessments": [asmt(1, "fail", v2="pass"), asmt(5, "pass", v2="fail"), asmt(6, None),
                           asmt(7, "fail", status="timeout"),
                           {"tick": 8, "hypotheses": [{"case_ref": "x", "cause": "c"}], "envelopes": []}]}
    v = rank1_verdicts(run, onset_s=150.0, tick_s=30.0)
    # the RANK-1 claim (c1) is read, never the rank-2 one
    assert (v["verifier_ran"], v["failed"], v["passed"], v["not_judged"]) == (3, 1, 1, 1)
    assert v["verifier_call_failed"] == 1
    assert v["post_onset"] == {"verifier_ran": 2, "failed": 0, "passed": 1, "not_judged": 1}


def _cfg():
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["agent"]["verifier"] = "always"
    cfg["multi"]["compact"].update({s: True for s in ALL_SECTIONS})
    cfg["multi"]["split"] = True
    return cfg


@pytest.mark.skipif(not (DEV / "dev_A03_bfp_suction").exists(), reason="dev episodes not generated")
def test_mock_verifier_detects_nothing_and_a_failing_verifier_is_counted(monkeypatch):
    ep = Episode(DEV / "dev_A03_bfp_suction")
    v = run_one(ep, _cfg(), force=True)
    assert v["forced_answers"] > 0 and v["verifier_ran"] > 0
    assert v["failed"] == 0 and v["fail_rate"] == 0.0            # the mock always agrees
    # the post-onset window is read from the episode's own onset (fault_onset_t)
    assert 0 < v["post_onset"]["verifier_ran"] <= v["verifier_ran"]
    c = run_one(ep, _cfg(), force=False)                          # control: nothing rewritten
    assert c["forced_answers"] == 0 and c["verifier_ran"] > 0
    # a verifier that fails ONLY claim 1 (rank 1): every rank-1 claim is detected
    only = lambda k: staticmethod(lambda hint: {
        "v": [[i, "f" if i == k else "p"] for i in range(1, hint.get("n_claims", 0) + 1)], "c": None})
    monkeypatch.setattr(MockBackend, "_mock_verification", only(1))
    v = run_one(ep, _cfg(), force=True)
    assert v["verifier_ran"] > 0 and v["failed"] == v["verifier_ran"] and v["fail_rate"] == 1.0
    # a verifier that fails ONLY claim 2: the rank-1 claim passed, detection 0
    monkeypatch.setattr(MockBackend, "_mock_verification", only(2))
    v = run_one(ep, _cfg(), force=True)
    assert v["verifier_ran"] > 0 and v["failed"] == 0
