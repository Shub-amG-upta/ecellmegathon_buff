"""Phase 3, commit 2: two side diagnosticians. Outcome-based: assertions on the
jobs the agent builds, the claims the gate folds, and emitted runs."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.agent.l6_gate import ActionCatalogue, Gate
from fieldmind.agent.world_model import new_world_model
from fieldmind.multi import compact, sides
from fieldmind.multi.agents.diagnostician import DiagnosticianAgent, SIDE_FACT_LINES
from fieldmind.multi.agents.gate import GateMemoryAgent
from fieldmind.multi.blackboard import Blackboard
from fieldmind.multi.jobs import Result
from fieldmind.multi.lanes import SimLane
from fieldmind.multi.scheduler import Scheduler
from fieldmind.runtime.llm_backend import MockBackend
from fieldmind.schemas import AgentEnvelope, Assessment, Fact, Finding

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
LIB = {c["case_id"]: c for c in
       json.loads((ROOT / "data/kb/case_library.json").read_text())["cases"]}
LETTERS, _ = compact.group_letters(json.loads((ROOT / "data/kb/case_groups.json").read_text()),
                                   list(LIB))


def _fact(i, tags, sev="WATCH", check="RATE", tick=84):
    return Fact(id=f"F{i}", check=check, tags=tags, window=(tick, tick), value=-1.0,
                detail=f"fact {i}: detail", severity=sev)


def _mcfg(split=True):
    on = {s: True for s in compact.SECTIONS}
    return dict(CFG["multi"], split=split, compact=dict(CFG["multi"]["compact"], **on))


def _board(facts, tick=84, findings=(), notes=(), notefacts=None, recordfacts=(),
           earlier=None):
    bb = Blackboard(new_world_model("t", []), audit=True)
    with bb.step("sensor"):
        if earlier:
            bb.mutable("facts", "sensor").add(*earlier)
        bb.mutable("facts", "sensor").add(tick, facts)
        bb.mutable("findings", "sensor").extend(findings)
    with bb.step("retriever"):
        bb.write("retrieval", {"cases": [dict(LIB[c], score=s) for c, s in
                                         (("RCA-01", .6), ("RCA-07", .5), ("RCA-11", .4), ("RCA-03", .3))],
                               "experience": [], "notes": list(notes), "candidates": [],
                               "records": {}, "operator_query": ""}, "retriever")
        bb.write("recordfacts", list(recordfacts), "retriever")
    if notefacts:
        with bb.step("text_reader"):
            bb.write("notefacts", notefacts, "text_reader")
    return bb


def _agent(split=True):
    diag = SimpleNamespace(backend=MockBackend(), log_prompts=False, calls=0,
                           parse_failures=0, retries=0)
    return DiagnosticianAgent(diag, CFG["agent"], _mcfg(split), LETTERS)


def _sched():
    return Scheduler([SimLane("npu", MockBackend(), 909, 13.9), SimLane("cpu", MockBackend(), 126, 42)],
                     dict(CFG["multi"], placement="fixed"))


def test_split_needs_the_compact_schema():
    diag = SimpleNamespace(backend=MockBackend(), log_prompts=False)
    with pytest.raises(ValueError):
        DiagnosticianAgent(diag, CFG["agent"], dict(CFG["multi"], split=True), LETTERS)


def test_one_job_per_active_side_each_seeing_only_its_side():
    facts = [_fact(1, ["drum_level"], "ALARM"), _fact(2, ["bed_temp_avg"]),
             _fact(3, ["steam_flow"]), _fact(4, ["ms_temperature"], "INFO")]
    jobs = _agent().make_jobs(_board(facts), _sched(), 84, "INVESTIGATE", 0, 0.0)
    assert [(j.agent, j.side) for j in jobs] == [("diag_water", "water"), ("diag_heat", "heat")]
    w, h = (j.line_map for j in jobs)
    assert w["facts"] == ["F1", "F3"] and set(h["facts"]) == {"F2", "F3", "F4"}
    assert [c["case_id"] for c in w["cases"]] == ["RCA-01", "RCA-11"]      # RCA-07, 03 heat only
    assert [c["case_id"] for c in h["cases"]] == ["RCA-07", "RCA-11", "RCA-03"]
    assert w["side"] == "water" and h["info"]["side"] == "heat"
    assert all(j.max_answer_tokens == 60 for j in jobs)


def test_only_the_active_side_runs_and_both_run_when_neither_has_evidence():
    jobs = _agent().make_jobs(_board([_fact(1, ["bed_temp_avg"])]), _sched(), 84, "WATCH", 0, 0.0)
    assert [j.side for j in jobs] == ["heat"]
    jobs = _agent().make_jobs(_board([_fact(1, ["bed_temp_avg"], "INFO")]), _sched(), 84, "WATCH", 0, 0.0)
    assert [j.side for j in jobs] == ["water", "heat"]
    fnd = Finding(id="X", signature_key="LIMIT:drum_level", first_tick=1, last_tick=1,
                  severity="WATCH", detail="")
    jobs = _agent().make_jobs(_board([], findings=[fnd]), _sched(), 84, "WATCH", 0, 0.0)
    assert [j.side for j in jobs] == ["water"]


def test_side_prompt_names_its_side_the_other_side_and_caps_facts_at_eight():
    facts = [_fact(i, ["drum_level"]) for i in range(1, 12)] + [_fact(20, ["bed_temp_avg"], "ALARM")]
    agent, sched = _agent(), _sched()
    seen = {}
    agent.diag.backend = SimpleNamespace(generate=lambda prompt, **k: seen.setdefault("p", prompt) and
                                         MockBackend().generate(prompt, **k))
    w = agent.make_jobs(_board(facts), sched, 84, "WATCH", 0, 0.0)[0]
    assert w.side == "water" and len(w.line_map["facts"]) == SIDE_FACT_LINES
    assert len(w.line_map["info"]["facts_dropped_by_line_cap"]) == 3
    sched.current = w
    w.work()
    assert seen["p"].startswith("This prompt covers the WATER side only")
    assert "OTHER SIDE (from code): heat side: [RATE/ALARM] fact 20" in seen["p"]


def test_unsplit_agent_still_makes_one_phase_2_job():
    jobs = _agent(split=False).make_jobs(_board([_fact(1, ["drum_level"])]), _sched(), 84, "WATCH", 0, 0.0)
    assert [(j.agent, j.side) for j in jobs] == [("diagnostician", "all")]


# ------------------------------------------------------------ combining
def test_combine_keeps_side_order_and_names_a_shared_case_once():
    w = {"hypotheses": [{"case_ref": "RCA-11", "cause": "c11", "supports": ["F1"]},
                        {"case_ref": "RCA-01", "cause": "c1", "supports": ["F1"]}],
         "unexplained": ["u1"]}
    h = {"hypotheses": [{"case_ref": "RCA-07", "cause": "c7", "supports": ["F2"]},
                        {"case_ref": "RCA-11", "cause": "c11", "supports": ["F2", "F1"]}],
         "unexplained": ["u1", "u2"]}
    out = compact.combine_side_payloads([w, h])
    assert [x["case_ref"] for x in out["hypotheses"]] == ["RCA-11", "RCA-01", "RCA-07"]
    assert out["hypotheses"][0]["supports"] == ["F1", "F2"] and [x["rank"] for x in out["hypotheses"]] == [1, 2, 3]
    assert out["unexplained"] == ["u1", "u2"]
    assert w["hypotheses"][0]["supports"] == ["F1"]                  # inputs not mutated


def _result(side, tick, answer, sched, status="ok"):
    lm = {"kind": "diagnosis", "evidence_tick": tick, "side": side,
          "facts": ["F1", "F2"], "fact_detail": ["a", "b"],
          "cases": [{"case_id": c, "root_cause": LIB[c]["root_cause"],
                     "discriminating_evidence": ""} for c in ("RCA-01", "RCA-07")],
          "letters": ["A", "G"], "conf": [0.6, 0.3], "context": [], "retrieved_cases": [],
          "info": {"side": side}}
    job = sched.new_job(f"diag_{side}", tick, 2, 60, 0.0, side=side)
    job.line_map = lm
    env = AgentEnvelope(agent="diagnostician", tick=tick, status=status, payload=answer)
    return Result(job=job, envelope=env, evidence_tick=tick, lane="npu",
                  queue_wait_ms=0.0, start_s=0.0, finish_s=1.0)


def _fold(facts, results, findings=(), earlier=None):
    bb = _board(facts, findings=findings, earlier=earlier)
    gate = GateMemoryAgent(Gate(ActionCatalogue(ROOT / "data/kb/action_catalogue.json"),
                                {"max_actions": 3}), ver=None, cfg={"min_faithfulness": 0.5})
    asmt = Assessment(tick=84, timestamp="", state="ALARM", headline="h", facts=[], triage="WATCH")
    claims = {"headline": "h", "unexplained": [], "hypotheses": [
        {"rank": 1, "cause": LIB["RCA-11"]["root_cause"], "confidence": 0.7, "supports": [],
         "case_ref": "RCA-11", "discriminator": ""}]}
    with bb.step("gate"):
        out = gate.fold_sides(bb, asmt, claims, results, 84)
    return out, asmt, gate


def test_more_severe_side_goes_first():
    s = _sched()
    facts = [_fact(1, ["drum_level"], "WATCH"), _fact(2, ["bed_temp_avg"], "CRITICAL")]
    rw, rh = _result("water", 84, {"r": [[1, [1]]]}, s), _result("heat", 84, {"r": [[2, [2]]]}, s)
    out, asmt, gate = _fold(facts, [rw, rh])
    assert [h["case_ref"] for h in out["hypotheses"]][:2] == ["RCA-07", "RCA-01"]
    assert len(asmt.envelopes) == 2 and gate.compact_log[-1]["order"] == ["heat", "water"]
    out, _, gate = _fold([_fact(1, ["drum_level"], "ALARM"), _fact(2, ["bed_temp_avg"])],
                         [_result("water", 84, {"r": [[1, [1]]]}, s), _result("heat", 84, {"r": [[2, [2]]]}, s)])
    assert gate.compact_log[-1]["order"] == ["water", "heat"]


def test_a_side_with_invented_citations_is_dropped_alone():
    s = _sched()
    facts = [_fact(1, ["drum_level"], "ALARM"), _fact(2, ["bed_temp_avg"])]
    rw = _result("water", 84, {"r": [[1, [7]]]}, s)            # line 7 does not exist
    rh = _result("heat", 84, {"r": [[2, [2]]]}, s)
    out, _, gate = _fold(facts, [rw, rh])
    assert out["hypotheses"][0]["case_ref"] == "RCA-07"
    assert "RCA-01" not in [h["case_ref"] for h in out["hypotheses"]]
    assert any(u.startswith("water diagnostician cited non-existent facts ['line7']")
               for u in out["unexplained"])
    assert gate.compact_log[-1]["order"] == ["heat"]


def test_a_failed_side_marks_degraded_and_the_other_side_still_counts():
    s = _sched()
    facts = [_fact(1, ["drum_level"], "ALARM"), _fact(2, ["bed_temp_avg"])]
    rw = _result("water", 84, {"error": "timeout"}, s, status="timeout")
    rh = _result("heat", 84, {"r": [[2, [2]]]}, s)
    out, asmt, _ = _fold(facts, [rw, rh])
    assert asmt.degraded_mode == "llm_timeout" and out["hypotheses"][0]["case_ref"] == "RCA-07"


def test_two_sides_that_each_pass_are_not_failed_together():
    # each side is at faithfulness 0.5 (F1 real, one invented line); the union
    # {F1, line7, line9} would score 0.33 if it were checked again
    s = _sched()
    facts = [_fact(1, ["steam_flow"], "ALARM"), _fact(2, ["bed_temp_avg"])]
    out, _, gate = _fold(facts, [_result("water", 84, {"r": [[1, [1, 7]]]}, s),
                                 _result("heat", 84, {"r": [[2, [1, 9]]]}, s)])
    refs = [h["case_ref"] for h in out["hypotheses"]]
    assert refs[:2] == ["RCA-01", "RCA-07"] and gate.compact_log[-1]["order"] == ["water", "heat"]
    assert not any("non-existent" in u for u in out["unexplained"])


def test_equal_severity_puts_water_first():
    s = _sched()
    facts = [_fact(1, ["drum_level"], "ALARM"), _fact(2, ["bed_temp_avg"], "ALARM")]
    out, _, gate = _fold(facts, [_result("heat", 84, {"r": [[2, [2]]]}, s),
                                 _result("water", 84, {"r": [[1, [1]]]}, s)])
    assert gate.compact_log[-1]["order"] == ["water", "heat"]
    assert [h["case_ref"] for h in out["hypotheses"]][:2] == ["RCA-01", "RCA-07"]


def test_side_answers_from_tick_83_folded_at_84_carry_t83_ids():
    s = _sched()
    f83 = [_fact(1, ["drum_level"], "ALARM", tick=83), _fact(2, ["bed_temp_avg"], tick=83)]
    out, _, _ = _fold([_fact(1, ["drum_level"], "INFO")],
                      [_result("water", 83, {"r": [[1, [1]]]}, s),
                       _result("heat", 83, {"r": [[2, [2]]]}, s)], earlier=(83, f83))
    sup = {h["case_ref"]: h["supports"] for h in out["hypotheses"]}
    assert sup["RCA-01"] == ["t83.F1"] and sup["RCA-07"] == ["t83.F2"]


def test_note_facts_go_by_their_subjects_raw_notes_by_their_tags_records_to_both():
    facts = [_fact(1, ["drum_level"], "ALARM"), _fact(2, ["bed_temp_avg"], "ALARM")]
    note = {"id": "n1", "author": "eng", "text": "bed running hot", "tags": ["drum_level"]}
    nf = {"n1": {"id": "N1", "note_id": "n1", "status": "ok", "kind": "OBS",
                 "pairs": [["bed_temp_avg", "HIGH"]], "t": 0.0, "reliability": 0.9}}
    rec = [{"id": "R1", "text": "coal lab report: GCV 4040"}]
    bb = _board(facts, notes=[note], notefacts=nf, recordfacts=rec)
    w, h = _agent().make_jobs(bb, _sched(), 84, "INVESTIGATE", 0, 0.0)
    # note-facts on: the note-fact names a heat tag, so heat only, whatever the raw tags
    assert w.line_map["context"] == ["R1"] and h.line_map["context"] == ["N1", "R1"]
    # raw notes (notes section off): by the raw note's tags
    diag = SimpleNamespace(backend=MockBackend(), log_prompts=False, calls=0, parse_failures=0, retries=0)
    mcfg = dict(CFG["multi"], split=True, compact=dict(CFG["multi"]["compact"], schema=True))
    w, h = DiagnosticianAgent(diag, CFG["agent"], mcfg, LETTERS).make_jobs(
        bb, _sched(), 84, "INVESTIGATE", 0, 0.0)
    assert w.line_map["context"][0] == "n1" and "n1" not in h.line_map["context"]


def test_no_side_accepted_keeps_belief_ranking():
    s = _sched()
    out, _, gate = _fold([_fact(1, ["drum_level"])], [_result("water", 84, {"error": "x"}, s, "error")])
    assert [h["case_ref"] for h in out["hypotheses"]] == ["RCA-11"]
    assert not [c for c in gate.compact_log if c.get("agent") == "merge"]


# ---------------------------------------------------------------- episodes
DEV = ROOT / "data/episodes_dev"
needs_dev = pytest.mark.skipif(not (DEV / "dev_A03_bfp_suction").exists(),
                               reason="dev episodes not generated")


def _cfg(split):
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["compact"].update({s: True for s in compact.ALL_SECTIONS})
    cfg["multi"]["split"] = split
    return cfg


@needs_dev
def test_episode_split_leaves_the_deterministic_layer_and_places_sides_on_their_lanes():
    ep = Episode(DEV / "dev_C01_wet_coal")
    a = run_episode(ep, _cfg(False), arch="multi")
    b = run_episode(ep, _cfg(True), arch="multi")
    keep = ("tick", "state", "triage", "facts", "belief_ranking", "belief_supports")
    assert [{k: x.get(k) for k in keep} for x in a["assessments"]] == \
           [{k: x.get(k) for k in keep} for x in b["assessments"]]
    lanes = {l["lane"]: l["n_jobs"] for l in b["multi"]["lanes"]}
    jobs = [r for x in b["assessments"] for r in (x.get("multi") or {}).get("results", [])
            if r["agent"].startswith("diag_")]
    assert jobs and all(r["lane"] == {"diag_water": "npu", "diag_heat": "cpu"}[r["agent"]] for r in jobs)
    assert b["multi"]["split"] is True and lanes["cpu"] > 0
    # every tick with side answers was folded ONCE through the side merge
    ticks = [x for x in b["assessments"]
             if any(r["agent"].startswith("diag_") for r in (x.get("multi") or {}).get("results", []))]
    merges = [x for x in ticks
              if [c for c in x["multi"]["compact"] if c.get("agent") == "merge"]]
    assert ticks and len(merges) == len(ticks)
