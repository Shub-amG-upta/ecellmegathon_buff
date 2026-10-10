"""Evidence-tick stamping: jobs and results carry the tick whose facts went into
the prompt, fact ids are tick-stamped on the board, and the gate resolves a
citation against the facts of the EVIDENCE tick, not the current one."""

from pathlib import Path

import pytest

from fieldmind.agent.l6_gate import ActionCatalogue, Gate
from fieldmind.agent.world_model import new_world_model
from fieldmind.multi.agents.gate import GateMemoryAgent
from fieldmind.multi.blackboard import Blackboard, FactsBook
from fieldmind.multi.jobs import Result
from fieldmind.multi.lanes import SimLane
from fieldmind.multi.scheduler import Scheduler
from fieldmind.runtime.llm_backend import MockBackend
from fieldmind.schemas import AgentEnvelope, Assessment, Fact

ROOT = Path(__file__).resolve().parent.parent
CFG = {"mode": "lockstep", "deadlines_s": {"P1": 10, "P2": 30, "P3": 60, "P4": None}}


def _fact(i, tick):
    return Fact(id=f"F{i}", check="RATE", tags=["drum_level"], window=(tick, tick),
                value=-1.0, detail=f"fact {i} at t{tick}", severity="WATCH")


def _board():
    bb = Blackboard(new_world_model("t", []), audit=True)
    with bb.step("sensor"):
        book = bb.mutable("facts", "sensor")
        book.add(84, [_fact(1, 84), _fact(2, 84), _fact(3, 84)])
        book.add(85, [_fact(1, 85)])                  # F3 does not exist at 85
    return bb


def _gate():
    gate = Gate(ActionCatalogue(ROOT / "data/kb/action_catalogue.json"),
                {"max_actions": 3})
    return GateMemoryAgent(gate, ver=None, cfg={"min_faithfulness": 0.5})


def _result(evidence_tick, cited):
    env = AgentEnvelope(agent="diagnostician", tick=evidence_tick, status="ok",
                        payload={"hypotheses": [{"cause": "c1", "confidence": 0.6,
                                                 "supports": cited, "case_ref": "RCA-01"}]},
                        cited_facts=cited)
    s = Scheduler([SimLane("npu", MockBackend(), 909, 13.9)], CFG)
    job = s.new_job("diagnostician", evidence_tick, 2, 60, 0.0)
    return Result(job=job, envelope=env, evidence_tick=evidence_tick, lane="npu",
                  queue_wait_ms=0.0, start_s=0.0, finish_s=1.0)


def _claims():
    return {"headline": "h", "unexplained": [],
            "hypotheses": [{"rank": 1, "cause": "c1", "confidence": 0.7,
                            "supports": [], "case_ref": "RCA-01", "discriminator": ""}]}


def test_stamped_ids_carry_their_tick():
    b = FactsBook()
    b.add(84, [_fact(1, 84), _fact(2, 84)])
    assert b.stamped_ids(84) == ["t84.F1", "t84.F2"]
    assert b.resolve("t84.F2").detail == "fact 2 at t84"
    assert b.resolve("t85.F1") is None


def test_result_carries_the_jobs_evidence_tick():
    s = Scheduler([SimLane("npu", MockBackend(), 909, 13.9)], CFG)
    s.submit(s.new_job("diagnostician", 84, 2, 60, 0.0, work=lambda: "env"))
    (r,) = s.run_lockstep(0.0)
    assert r.evidence_tick == 84 and r.job.evidence_tick == 84
    assert r.telemetry()["evidence_tick"] == 84


def test_citation_checked_against_evidence_tick_not_current_tick():
    bb, g = _board(), _gate()
    asmt = Assessment(tick=85, timestamp="", state="DEVIATION", headline="h")
    with bb.step("gate"):
        claims = g.fold_diagnosis(bb, asmt, _claims(), _result(84, ["F3"]), now_tick=85)
    # F3 existed at the evidence tick (84): faithful, merged, nothing flagged,
    # and published STAMPED, because tick 85 has its own F-numbering
    assert claims["hypotheses"][0]["supports"] == ["t84.F3"]
    assert not any("non-existent" in u for u in claims["unexplained"])


def test_late_answer_cannot_explain_a_current_tick_fact_with_the_same_local_id():
    # tick 85 has an ALARM F1; a tick-84 answer citing ITS F1 must not count
    # as explaining it, so the ALARM stays in `unexplained`.
    bb = Blackboard(new_world_model("t", []), audit=True)
    alarm = Fact(id="F1", check="LIMIT", tags=["drum_level"], window=(85, 85),
                 value=15.0, detail="drum level low alarm at t85", severity="ALARM")
    with bb.step("sensor"):
        book = bb.mutable("facts", "sensor")
        book.add(84, [_fact(1, 84)])
        book.add(85, [alarm])
    g = _gate()
    asmt = Assessment(tick=85, timestamp="", state="ALARM", headline="h")
    with bb.step("gate"):
        claims = g.fold_diagnosis(bb, asmt, _claims(), _result(84, ["F1"]), now_tick=85)
    _, _, unexplained = g.gate.approve(claims["hypotheses"], [alarm], {}, "ALARM")
    assert "drum level low alarm at t85" in unexplained


def test_same_tick_answer_keeps_local_ids():
    # lockstep: evidence tick == current tick, ids unchanged (prompt identity)
    bb, g = _board(), _gate()
    asmt = Assessment(tick=84, timestamp="", state="DEVIATION", headline="h")
    with bb.step("gate"):
        claims = g.fold_diagnosis(bb, asmt, _claims(), _result(84, ["F3"]), now_tick=84)
    assert claims["hypotheses"][0]["supports"] == ["F3"]


def test_citation_absent_at_evidence_tick_is_rejected():
    bb, g = _board(), _gate()
    asmt = Assessment(tick=85, timestamp="", state="DEVIATION", headline="h")
    with bb.step("gate"):
        claims = g.fold_diagnosis(bb, asmt, _claims(), _result(85, ["F3"]), now_tick=85)
    assert claims["hypotheses"][0]["supports"] == []
    assert any("non-existent facts ['F3']" in u for u in claims["unexplained"])


def test_answer_older_than_one_tick_is_dropped_and_logged():
    bb, g = _board(), _gate()
    asmt = Assessment(tick=86, timestamp="", state="DEVIATION", headline="h")
    r = _result(84, ["F3"])
    with bb.step("gate"):
        claims = g.fold_diagnosis(bb, asmt, _claims(), r, now_tick=86)
    assert r.stale and claims == _claims()
    assert g.stale_dropped == [{"job_id": r.job.job_id, "agent": "diagnostician",
                                "evidence_tick": 84, "now_tick": 86}]
    assert len(asmt.envelopes) == 1                    # the call is still logged


def test_answer_one_tick_old_is_accepted():
    bb, g = _board(), _gate()
    asmt = Assessment(tick=85, timestamp="", state="DEVIATION", headline="h")
    r = _result(84, ["F1"])
    with bb.step("gate"):
        g.fold_diagnosis(bb, asmt, _claims(), r, now_tick=85)
    assert not r.stale and g.stale_dropped == []


@pytest.mark.skipif(not (ROOT / "data/episodes_dev/dev_A01_fcv_seize").exists(),
                    reason="dev episodes not generated")
def test_episode_every_result_stamped_with_its_tick_and_decisions_match():
    import yaml

    from bench.compare_runs import compare_runs
    from bench.harness import Episode, run_episode

    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["compact"] = {}                   # Phase 1 path: parity with single
    ep = Episode(ROOT / "data/episodes_dev/dev_A01_fcv_seize")
    single = run_episode(ep, cfg)
    multi = run_episode(ep, cfg, arch="multi")
    n = 0
    for a in multi["assessments"]:
        for r in a["multi"]["results"]:
            assert r["evidence_tick"] == a["tick"]
            n += 1
    assert n == multi["diag_calls"] + multi["ver_calls"] > 0
    assert compare_runs([single], [multi]) == []
