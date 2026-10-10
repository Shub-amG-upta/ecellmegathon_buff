"""Phase 3, commit 3: call a side only when its evidence changed, and re-use its
last checked answer otherwise. Outcome-based: assertions on the jobs made, the
board's `side_answers`, the folded claims and emitted runs."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.agent.l6_gate import ActionCatalogue, Gate
from fieldmind.multi import compact, sides
from fieldmind.multi.agents.diagnostician import DiagnosticianAgent
from fieldmind.multi.agents.gate import GateMemoryAgent
from fieldmind.runtime.llm_backend import MockBackend
from fieldmind.schemas import Assessment, Finding
from tests.test_split import CFG, LETTERS, LIB, _board, _fact, _result, _sched

ROOT = Path(__file__).resolve().parent.parent
SIG = {("drum_level", "DOWN", "FAST"): 1.0, ("bed_temp_avg", "UP", "MED"): 1.0}


def _agent(on_change=True):
    diag = SimpleNamespace(backend=MockBackend(), log_prompts=False, calls=0,
                           parse_failures=0, retries=0)
    mcfg = dict(CFG["multi"], split=True, split_on_change=on_change,
                compact=dict(CFG["multi"]["compact"], **{s: True for s in compact.SECTIONS}))
    return DiagnosticianAgent(diag, CFG["agent"], mcfg, LETTERS)


def _rig(facts, sig=SIG, cache=None, findings=()):
    bb = _board(facts, findings=findings)
    with bb.step("sensor"):
        bb.write("signature", {"signature": dict(sig), "slopes": {}}, "sensor")
    if cache is not None:
        with bb.step("gate"):
            bb.write("side_answers", cache, "gate")
    return bb


def _gate():
    return GateMemoryAgent(Gate(ActionCatalogue(ROOT / "data/kb/action_catalogue.json"),
                                {"max_actions": 3}), ver=None, cfg={"min_faithfulness": 0.5})


FACTS = [_fact(1, ["drum_level"], "ALARM"), _fact(2, ["bed_temp_avg"])]


def test_on_change_needs_split():
    diag = SimpleNamespace(backend=MockBackend(), log_prompts=False)
    mcfg = dict(CFG["multi"], split=False, split_on_change=True,
                compact=dict(CFG["multi"]["compact"], schema=True))
    with pytest.raises(ValueError):
        DiagnosticianAgent(diag, CFG["agent"], mcfg, LETTERS)


def test_no_job_for_a_side_whose_evidence_is_unchanged_since_its_cached_answer():
    bb = _rig(FACTS)
    fp_w = DiagnosticianAgent.fingerprint(bb, "water")
    cache = {"water": {"fingerprint": fp_w, "payload": {"hypotheses": []}, "evidence_tick": 80}}
    jobs = _agent().make_jobs(_rig(FACTS, cache=cache), _sched(), 84, "WATCH", 0, 0.0)
    assert [j.side for j in jobs] == ["heat"]
    # the water signature moved: water is asked again
    moved = {("drum_level", "DOWN", "MED"): 1.0, ("bed_temp_avg", "UP", "MED"): 1.0}
    jobs = _agent().make_jobs(_rig(FACTS, sig=moved, cache=cache), _sched(), 84, "WATCH", 0, 0.0)
    assert [j.side for j in jobs] == ["water", "heat"]
    # the side's case list changed (retrieval returns different cases): asked again
    bb_cases = _rig(FACTS, cache=cache)
    with bb_cases.step("retriever"):
        r = dict(bb_cases.read("retrieval"))
        r["cases"] = [c for c in r["cases"] if c["case_id"] != "RCA-01"]
        bb_cases.write("retrieval", r, "retriever")
    jobs = _agent().make_jobs(bb_cases, _sched(), 84, "WATCH", 0, 0.0)
    assert [j.side for j in jobs] == ["water", "heat"]
    # switch off: every side to run is asked, cache or not
    jobs = _agent(False).make_jobs(_rig(FACTS, cache=cache), _sched(), 84, "WATCH", 0, 0.0)
    assert [j.side for j in jobs] == ["water", "heat"]
    assert jobs[0].line_map["fingerprint"] == fp_w


def _fold(bb, results, now=84):
    gate = _gate()
    asmt = Assessment(tick=now, timestamp="", state="ALARM", headline="h", facts=[], triage="WATCH")
    claims = {"headline": "h", "unexplained": [], "hypotheses": [
        {"rank": 1, "cause": LIB["RCA-11"]["root_cause"], "confidence": 0.7, "supports": [],
         "case_ref": "RCA-11", "discriminator": ""}]}
    with bb.step("gate"):
        out = gate.fold_sides(bb, asmt, claims, results, now, reuse=True)
    return out, gate


def _with_fp(result, bb):
    result.job.line_map["fingerprint"] = DiagnosticianAgent.fingerprint(bb, result.job.line_map["side"])
    return result


def test_accepted_answer_is_cached_stamped_and_reused_on_an_unchanged_tick():
    s = _sched()
    bb = _rig(FACTS)
    out, _ = _fold(bb, [_with_fp(_result("water", 84, {"r": [[1, [1]]]}, s), bb),
                        _with_fp(_result("heat", 84, {"r": [[2, [2]]]}, s), bb)])
    cache = bb.read("side_answers")
    assert set(cache) == {"water", "heat"} and cache["water"]["evidence_tick"] == 84
    assert cache["water"]["payload"]["hypotheses"][0]["supports"] == ["t84.F1"]
    # next tick: heat asked again, water unchanged -> water's cached answer is used
    bb2 = _rig(FACTS, cache=dict(cache))
    out, gate = _fold(bb2, [_with_fp(_result("heat", 84, {"r": [[2, [2]]]}, s), bb2)])
    sup = {h["case_ref"]: h["supports"] for h in out["hypotheses"]}
    assert sup["RCA-01"] == ["t84.F1"] and gate.compact_log[-1]["reused"] == ["water"]
    # the re-used answer keeps ITS evidence tick in the merge record
    cache80 = {"water": dict(cache["water"], evidence_tick=80)}
    bb3 = _rig(FACTS, cache=cache80)
    _, gate = _fold(bb3, [_with_fp(_result("heat", 84, {"r": [[2, [2]]]}, s), bb3)])
    rec = gate.compact_log[-1]
    assert dict(zip(rec["order"], rec["evidence_ticks"])) == {"water": 80, "heat": 84}


def test_cache_is_dropped_when_the_side_goes_quiet_or_its_evidence_moves():
    s = _sched()
    bb = _rig(FACTS)
    _fold(bb, [_with_fp(_result("water", 84, {"r": [[1, [1]]]}, s), bb),
               _with_fp(_result("heat", 84, {"r": [[2, [2]]]}, s), bb)])
    cache = dict(bb.read("side_answers"))
    # heat goes quiet (no heat evidence): heat's entry is dropped, water's kept
    bb2 = _rig([_fact(1, ["drum_level"], "ALARM")], cache=dict(cache))
    out, gate = _fold(bb2, [])
    assert set(bb2.read("side_answers")) == {"water"} and gate.compact_log[-1]["reused"] == ["water"]
    # water's signature moved and no call was made: the stale entry is not used
    moved = {("drum_level", "UP", "FAST"): 1.0, ("bed_temp_avg", "UP", "MED"): 1.0}
    bb3 = _rig(FACTS, sig=moved, cache=dict(cache))
    out, gate = _fold(bb3, [])
    assert "water" not in bb3.read("side_answers")
    assert [h["case_ref"] for h in out["hypotheses"]][0] == "RCA-07"   # heat reused only


def test_a_new_answer_that_fails_removes_the_old_one():
    s = _sched()
    bb = _rig(FACTS)
    _fold(bb, [_with_fp(_result("water", 84, {"r": [[1, [1]]]}, s), bb)])
    bb2 = _rig(FACTS, cache=dict(bb.read("side_answers")))
    _fold(bb2, [_with_fp(_result("water", 84, {"error": "x"}, s, status="error"), bb2)])
    assert "water" not in bb2.read("side_answers")


# ---------------------------------------------------------------- episodes
DEV = ROOT / "data/episodes_dev"


@pytest.mark.skipif(not (DEV / "dev_C01_wet_coal").exists(), reason="dev episodes not generated")
@pytest.mark.parametrize("episode", ["dev_C01_wet_coal", "dev_B01_tube_leak"])
def test_episode_on_change_calls_less_and_leaves_the_deterministic_layer(episode):
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["compact"].update({s: True for s in compact.ALL_SECTIONS})
    cfg["multi"]["split"] = True
    ep = Episode(DEV / episode)
    a = run_episode(ep, cfg, arch="multi")
    cfg["multi"]["split_on_change"] = True
    b = run_episode(ep, cfg, arch="multi")
    keep = ("tick", "state", "triage", "facts", "belief_ranking", "belief_supports")
    assert [{k: x.get(k) for k in keep} for x in a["assessments"]] == \
           [{k: x.get(k) for k in keep} for x in b["assessments"]]
    assert 0 < b["diag_calls"] < a["diag_calls"]
    res = lambda x: (x.get("multi") or {}).get("results", [])
    no_diag = {x["tick"] for x in b["assessments"]
               if x["triage"] != "QUIET" and not any(r["agent"].startswith("diag_") for r in res(x))}
    reused = {x["tick"] for x in b["assessments"]
              if any(c.get("reused") for c in (x.get("multi") or {}).get("compact", [])
                     if c.get("agent") == "merge")}
    assert no_diag and no_diag <= reused        # every tick without a call re-used an answer
    # llm_invoked: True exactly when some model (diagnosis or verifier) was called
    assert all(x["llm_invoked"] == bool(res(x)) for x in b["assessments"] if x["triage"] != "QUIET")
    if episode == "dev_B01_tube_leak":      # has ticks where only the verifier is called
        assert any(x["llm_invoked"] for x in b["assessments"] if x["tick"] in no_diag)
    # a published model hypothesis only names a case shown to some side THIS tick
    checked = 0
    for x in b["assessments"]:
        comp = (x.get("multi") or {}).get("compact", [])
        merge = [c for c in comp if c.get("agent") == "merge"]
        if not (merge and merge[0]["reused"]):
            continue
        shown = set(sum((c["case_ids"] for c in comp if c.get("agent") == "diagnostician"), []))
        shown |= set(sum(merge[0]["reused_shown"].values(), []))
        named = {h["case_ref"] for h in x["hypotheses"] if not h.get("carried")}
        assert named <= shown
        checked += 1
    assert checked


def test_q3_counts_a_stamped_citation_against_the_tick_it_names():
    from bench.evaluator import t3_faithfulness
    run = {"assessments": [
        {"tick": 80, "facts": [{"id": "F1"}], "hypotheses": []},
        {"tick": 84, "facts": [{"id": "F2"}], "hypotheses": [
            {"supports": ["t80.F1", "F2", "t80.F9", "F1", "line7"]}]}]}
    out = t3_faithfulness(run)
    # t80.F1 existed at 80; F2 exists now; t80.F9 did not exist; F1 is not a fact of 84
    assert out["n_citations"] == 5 and out["faithfulness"] == 0.4
