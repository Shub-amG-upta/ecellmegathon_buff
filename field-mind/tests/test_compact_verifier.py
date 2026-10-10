"""Phase 2, commit 6: the compact verifier. Pass/fail per shown claim by line
number, top 3 claims, no confidence or discriminator shown, "not judged" never
a pass, expansion by the gate and the single agent's Verifier.apply unchanged.
Outcome-based: assertions are on the built prompt, the folded claims, recorded
backend calls, or emitted runs."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.agent.l5_verify import Verifier
from fieldmind.agent.world_model import new_world_model
from fieldmind.multi import compact
from fieldmind.multi.agents.gate import GateMemoryAgent
from fieldmind.multi.agents.verifier import VerifierAgent
from fieldmind.multi.blackboard import Blackboard
from fieldmind.multi.jobs import Result
from fieldmind.multi.lanes import SimLane
from fieldmind.multi.scheduler import Scheduler
from fieldmind.runtime.llm_backend import LLMBackend, LLMReply, MockBackend
from fieldmind.schemas import AgentEnvelope, Assessment, Fact

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
TEMPLATES = {"body": compact.load_prompt("ver_compact.txt"),
             "rules_full": compact.load_prompt("ver_rules_full.txt"),
             "rules_compact": compact.load_prompt("ver_rules_compact.txt")}
VS = {s: False for s in compact.ALL_SECTIONS} | {"ver_schema": True, "case_order": "score"}
LONG = ("Feed water control valve actuator stem seizure from deposit build-up. "
        "Aggravated by a part-open blow-down valve.")


def _fact(i, sev="WATCH", tick=84):
    return Fact(id=f"F{i}", check="RATE", tags=["drum_level"], window=(tick, tick),
                value=-1.0, detail=f"fact {i} at t{tick}", severity=sev)


FACTS = [_fact(1, "ALARM"), _fact(2), _fact(3, "INFO")]


def _claims():
    return {"headline": "h", "unexplained": [], "hypotheses": [
        {"rank": 1, "cause": LONG, "confidence": 0.62, "supports": ["F1", "F3"],
         "case_ref": "RCA-01", "discriminator": "DISCRIMINATOR-TEXT"},
        {"rank": 2, "cause": "cause two", "confidence": 0.41, "supports": [],
         "case_ref": "RCA-16", "discriminator": "", "carried": True},
        {"rank": 3, "cause": "cause three", "confidence": 0.33, "supports": ["t84.F2"],
         "case_ref": "RCA-11", "discriminator": ""},
        {"rank": 4, "cause": "cause four", "confidence": 0.2, "supports": ["F2"],
         "case_ref": "RCA-07", "discriminator": ""}]}


def _build(sw=VS, claims=None, facts=FACTS, tick=84):
    return compact.build_verification(sw, TEMPLATES, facts=facts,
                                      claims=claims or _claims(), evidence_tick=tick)


# ---------------------------------------------------------------- switches
def test_verifier_sections_need_ver_schema():
    for s in ("ver_rules", "ver_claims"):
        with pytest.raises(ValueError):
            compact.compact_switches({s: True})
    assert compact.compact_switches({"ver_schema": True})["ver_schema"]


# ------------------------------------------------------------------ prompt
def test_prompt_shows_top_three_claims_with_cited_lines_and_no_confidence():
    p, lm, hint = _build()
    claims = p[p.index("CLAIMS:"):p.index("Answer format")]
    assert "1. RCA-01: " + LONG + " [cites 1, 3]" in claims
    assert "2. RCA-16: cause two [cites none]" in claims
    assert "3. RCA-11: cause three [cites 2]" in claims          # stamped id of this tick
    assert "RCA-07" not in p and "cause four" not in p            # beyond rank 3
    assert "0.62" not in p and "DISCRIMINATOR-TEXT" not in p
    assert lm["claims"] == [LONG, "cause two", "cause three"] and hint["n_claims"] == 3
    assert '"p" (pass) or "f" (fail)' in p          # defined even with ver_rules off


def test_facts_are_every_fact_of_the_tick_in_l1_order():
    p, lm, _ = _build()
    facts = p[p.index("FACTS:"):p.index("CLAIMS:")]
    assert "1. [RATE/ALARM] fact 1" in facts and "3. [RATE/INFO] fact 3" in facts
    assert lm["facts"] == ["F1", "F2", "F3"]


def test_unknown_citation_is_shown_as_a_question_mark():
    c = _claims()
    c["hypotheses"][0]["supports"] = ["F1", "line7"]
    p, _, _ = _build(claims=c)
    assert "[cites 1, ?]" in p


def test_ver_claims_shortens_the_cause_and_ver_rules_only_the_rules():
    long_p, _, _ = _build()
    short_p, _, _ = _build(VS | {"ver_claims": True})
    assert "1. RCA-01: Feed water control valve actuator stem seizure from deposit " \
           "build-up. [cites 1, 3]" in short_p and "Aggravated" not in short_p
    r_p, _, _ = _build(VS | {"ver_rules": True})
    cut = lambda p: p[p.index("FACTS:"):]
    assert cut(r_p) == cut(long_p) and r_p != long_p


# --------------------------------------------------------------- expansion
def test_failed_claim_is_capped_and_a_missing_verdict_is_not_judged():
    _, lm, _ = _build()
    payload, info = compact.expand_ver_answer({"v": [[1, "f"], [3, "p"]], "c": 2}, lm)
    assert info["not_judged"] == [2] and info["failed"] == [LONG]
    assert payload["agree"] is False and payload["revised_confidence"] is None
    assert payload["strongest_contradiction"] == "t84.F2 fact 2 at t84"
    out = Verifier.apply(_claims(), payload)
    h = {x["case_ref"]: x for x in out["hypotheses"]}
    assert h["RCA-01"]["confidence"] == 0.35 and h["RCA-01"]["verifier"] == "failed check"
    # not judged: untouched, and NOT recorded as a pass
    assert h["RCA-16"]["confidence"] == 0.41 and "verifier" not in h["RCA-16"]
    assert [c["claim"] for c in payload["checks"]] == [LONG, "cause three"]
    assert "verifier contradiction: t84.F2 fact 2 at t84" in out["unexplained"]


def test_out_of_range_lines_are_counted_and_a_self_contradiction_is_not_judged():
    _, lm, _ = _build()
    payload, info = compact.expand_ver_answer(
        {"v": [[1, "p"], [1, "f"], [7, "f"], [2, "p"], [2, "p"], [3, "p"]], "c": 9}, lm)
    assert info["bad_claim_lines"] == 1 and info["bad_fact_line"]
    assert info["conflicting"] == [1] and info["not_judged"] == [1]
    assert [c["claim"] for c in payload["checks"]] == ["cause two", "cause three"]
    assert payload["agree"] is None and payload["strongest_contradiction"] is None


@pytest.mark.parametrize("answer,agree", [
    ({"v": []}, None),                                    # judged nothing
    ({"v": [[1, "p"]]}, None),                            # partial
    ({"v": [[1, "p"], [2, "p"], [3, "p"]]}, True),
    ({"v": [[1, "p"], [2, "f"]]}, False),                 # a failure wins
])
def test_empty_or_partial_verdict_never_reads_as_agreement(answer, agree):
    _, lm, _ = _build()
    payload, _ = compact.expand_ver_answer(answer, lm)
    assert payload["agree"] is agree


def test_late_citation_shows_its_tick_and_only_an_unknown_id_a_question_mark():
    c = _claims()
    c["hypotheses"][0]["supports"] = ["t83.F1", "F2", "t84.F3", "t84.F9", "line7"]
    p, _, _ = _build(claims=c)
    assert "[cites t83, 2, 3, ?, ?]" in p


def test_prompt_over_the_limit_is_flagged_not_cut():
    p, _, _ = _build()
    assert not compact.over_limit(p, 30, CFG["multi"]["compact_guard_cpt"])
    assert compact.over_limit(p, 30, 0.5) and not compact.over_limit(p, 30, None)


# -------------------------------------------------------------- gate fold
def _fold(answer, status="ok"):
    ver = Verifier(MockBackend(), {"max_tokens": 256})
    gate = GateMemoryAgent(None, ver, cfg={})
    bb = Blackboard(new_world_model("t", []), audit=True)
    _, lm, _ = _build()
    s = Scheduler([SimLane("cpu", MockBackend(), 126, 42)],
                  {"mode": "lockstep", "deadlines_s": {"P3": 60}})
    job = s.new_job("verifier", 84, 3, 30, 0.0)
    job.line_map = lm
    env = AgentEnvelope(agent="verifier", tick=84, status=status, payload=answer)
    res = Result(job=job, envelope=env, evidence_tick=84, lane="cpu",
                 queue_wait_ms=0.0, start_s=0.0, finish_s=1.0)
    asmt = Assessment(tick=84, timestamp="", state="ALARM", headline="h", facts=[],
                      triage="INVESTIGATE")
    with bb.step("gate"):
        out = gate.apply_verdict(bb, asmt, _claims(), res, 84)
    return out, asmt, gate, ver


def test_gate_folds_line_verdicts_and_counts_a_disagreement():
    out, asmt, gate, ver = _fold({"v": [[1, "f"], [2, "p"], [3, "p"]], "c": None})
    assert out["hypotheses"][0]["confidence"] == 0.35 and ver.disagreements == 1
    assert asmt.envelopes[0]["payload"]["agree"] is False
    assert gate.compact_log[0]["agent"] == "verifier" and gate.ver_incomplete == 0
    out, _, gate, ver = _fold({"v": [[1, "p"], [2, "p"], [3, "p"]], "c": None})
    assert out["hypotheses"][0]["confidence"] == 0.62 and ver.disagreements == 0
    out, _, gate, ver = _fold({"v": []})
    assert ver.disagreements == 0 and gate.ver_incomplete == 1


def test_failed_call_is_not_applied():
    out, _, _, ver = _fold({"error": "timeout"}, status="timeout")
    assert out["hypotheses"][0]["confidence"] == 0.62 and ver.disagreements == 0


class Scripted(LLMBackend):
    name = "scripted-ver"

    def __init__(self, text):
        self.text = text
        self.calls = []

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        self.calls.append({"role": role, "max_tokens": max_tokens})
        return LLMReply(text=self.text, prefill_tokens=10, decode_tokens=5)


@pytest.mark.parametrize("bad", ['{"v":[[1,"pass"]]}', '{"v":[["1","p"]]}',
                                 '{"c":2}', '{"v":[[1,"p"]],"c":"2"}', 'no json'])
def test_malformed_verdict_is_invalid_with_no_repair_call(bad):
    b = Scripted(bad)
    ver = Verifier(b, {"max_tokens": 256})
    mcfg = dict(CFG["multi"], compact={"ver_schema": True})
    agent = VerifierAgent(ver, CFG["agent"], mcfg)
    bb = Blackboard(new_world_model("t", []), audit=True)
    with bb.step("sensor"):
        bb.mutable("facts", "sensor").add(84, FACTS)
    s = Scheduler([SimLane("cpu", b, 126, 42)], {"mode": "lockstep", "deadlines_s": {"P3": 60}})
    job = agent.make_job(bb, s, 84, _claims(), 0.0)
    assert job.max_answer_tokens == 30
    env = job.work()
    assert env.status == "invalid_schema" and len(b.calls) == 1
    assert b.calls[0]["max_tokens"] == 30 and ver.calls == 1


# ---------------------------------------------------------------- episodes
DEV = ROOT / "data/episodes_dev"
needs_dev = pytest.mark.skipif(not (DEV / "dev_A03_bfp_suction").exists(),
                               reason="dev episodes not generated")


def _cfg(on=()):
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["compact"].update({s: s in on for s in compact.ALL_SECTIONS})
    return cfg


def _run(tmp_path, name, cfg, arch="multi"):
    log = tmp_path / f"{name}.jsonl"
    run = run_episode(Episode(DEV / "dev_A03_bfp_suction"), cfg, arch=arch,
                      record_prompts=str(log))
    return run, [json.loads(l) for l in log.read_text().splitlines()]


@needs_dev
def test_episode_verifier_off_sends_the_single_agents_verifier_prompts(tmp_path):
    _, m = _run(tmp_path, "m", _cfg())
    _, s = _run(tmp_path, "s", _cfg(), arch="single")
    pick = lambda calls: [(c["max_tokens"], c["prompt"]) for c in calls if c["role"] == "verifier"]
    assert pick(m) == pick(s) and len(pick(m)) > 0


@needs_dev
def test_episode_compact_verifier_keeps_decisions_and_caps_at_30(tmp_path):
    a, _ = _run(tmp_path, "a", _cfg())
    b, calls = _run(tmp_path, "b", _cfg(compact.VER_SECTIONS))
    ver = [c for c in calls if c["role"] == "verifier"]
    assert ver and all(c["max_tokens"] == 30 for c in ver)
    assert all("conf=" not in c["prompt"] and "discriminator=" not in c["prompt"] for c in ver)
    pick = lambda r: [(x["tick"], x["state"], x.get("escalate"), x.get("confidence"),
                       [(h["case_ref"], h["confidence"], h.get("verifier"))
                        for h in x.get("hypotheses", [])]) for x in r["assessments"]]
    assert pick(a) == pick(b) and a["ver_calls"] == b["ver_calls"] > 0
