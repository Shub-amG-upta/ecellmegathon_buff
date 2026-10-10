"""Phase 2, commit 5: the compact diagnostician. Per-section switches, the
numbered prompt, answer format B', the line map fixed at build time, expansion
by the gate, confidence option (ii), caps and the guard. Outcome-based:
assertions are on the built prompt, the expanded / folded claims, recorded
backend calls, or emitted runs."""

import copy
import json
from pathlib import Path

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.agent.l6_gate import ActionCatalogue, Gate
from fieldmind.agent.orchestrator import initial_claims
from fieldmind.agent.world_model import new_world_model
from fieldmind.multi import compact
from fieldmind.multi.agents.gate import GateMemoryAgent
from fieldmind.multi.blackboard import Blackboard
from fieldmind.multi.jobs import Result
from fieldmind.multi.lanes import SimLane
from fieldmind.multi.scheduler import Scheduler
from fieldmind.multi.agents._call import call_model
from fieldmind.runtime.llm_backend import LLMBackend, LLMReply, MockBackend
from fieldmind.schemas import AgentEnvelope, Assessment, Fact, Hypothesis

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
LIB = json.loads((ROOT / "data/kb/case_library.json").read_text())["cases"]
BY_ID = {c["case_id"]: c for c in LIB}
LETTERS, GROUPS = compact.group_letters(
    json.loads((ROOT / "data/kb/case_groups.json").read_text()),
    [c["case_id"] for c in LIB])
TEMPLATES = {"body": compact.load_prompt("diag_compact.txt"),
             "rules_full": compact.load_prompt("diag_rules_full.txt"),
             "rules_compact": compact.load_prompt("diag_rules_compact.txt")}
ALL = {s: True for s in compact.SECTIONS} | {"case_order": "score"}
SCHEMA = {s: s == "schema" for s in compact.SECTIONS} | {"case_order": "score"}


def _fact(i, sev="WATCH", tick=84):
    return Fact(id=f"F{i}", check="RATE", tags=["drum_level"], window=(tick, tick),
                value=-1.0, detail=f"fact {i} at t{tick}", severity=sev)


def _case(cid, score):
    return dict(BY_ID[cid], score=score)


def _retrieved(notes=(), records=None):
    return {"cases": [_case("RCA-01", 0.62), _case("RCA-16", 0.55), _case("RCA-11", 0.40),
                      _case("RCA-07", 0.20)],
            "experience": [], "notes": list(notes), "candidates": ["FEED_VALVE"],
            "records": records or {}, "operator_query": "why is level low?"}


def _belief(**conf):
    out = []
    for cid, c in conf.items():
        cid = cid.replace("_", "-")
        out.append(Hypothesis(cause=BY_ID[cid]["root_cause"], case_ref=cid,
                              confidence=abs(c), retired=c < 0))
    return out


def _build(sw, facts=None, retrieved=None, notefacts=None, recordfacts=None,
           belief=None, level_cap=12, tick=84, guard=None):
    return compact.build_diagnosis(
        sw, TEMPLATES, facts=facts if facts is not None else [_fact(1, "ALARM"), _fact(2)],
        level_cap=level_cap, retrieved=retrieved or _retrieved(),
        notefacts=notefacts or {}, recordfacts=recordfacts or [],
        belief=belief if belief is not None else _belief(RCA_01=0.62),
        untrusted=[], wm_text="WM-SUMMARY", now_s=3600.0, evidence_tick=tick,
        letters=LETTERS, cap=60, guard_cpt=guard)


def _section(prompt, start, end):
    return prompt[prompt.index(start):prompt.index(end)]


# ---------------------------------------------------------------- switches
def test_sections_other_than_schema_need_schema():
    for s in ("rules", "cases", "notes", "world"):
        with pytest.raises(ValueError):
            compact.compact_switches({s: True})
    assert compact.compact_switches({"schema": True})["schema"]
    with pytest.raises(ValueError):
        compact.compact_switches({"schema": True, "case_order": "random"})


@pytest.mark.parametrize("section,start,end", [
    ("rules", "", "FACTS:"),
    ("cases", "RETRIEVED CASES:", "NOTES AND RECORDS"),
    ("notes", "NOTES AND RECORDS", "WORLD MODEL:"),
    ("world", "WORLD MODEL:", "Answer format"),
])
def test_each_switch_changes_only_its_own_section(section, start, end):
    nf = {"n1": {"id": "N1", "note_id": "n1", "status": "ok", "kind": "OBS",
                 "pairs": [["FEED_VALVE", "STUCK"]], "t": 0.0, "reliability": 0.9}}
    kw = dict(retrieved=_retrieved(notes=[{"id": "n1", "author": "eng",
                                           "text": "fcv not responding"}],
                                   records={"coal_lab_report": "GCV 4040"}),
              notefacts=nf, recordfacts=[{"id": "R1", "text": "coal lab report: GCV 4040"}])
    base, _, _, _ = _build(SCHEMA, **kw)
    one, _, _, _ = _build(SCHEMA | {section: True}, **kw)
    assert base != one
    marks = ["FACTS:", "RETRIEVED CASES:", "NOTES AND RECORDS", "WORLD MODEL:",
             "Answer format"]
    cut = lambda p: [p[:p.index(marks[0])]] + [
        p[p.index(a):p.index(b)] for a, b in zip(marks, marks[1:])] + [p[p.index(marks[-1]):]]
    changed = [i for i, (x, y) in enumerate(zip(cut(base), cut(one))) if x != y]
    want = {"rules": 0, "cases": 2, "notes": 3, "world": 4}[section]
    assert changed == [want]


def test_schema_prompt_has_numbered_lines_no_fact_ids_and_no_candidate_causes():
    p, lm, _, _ = _build(SCHEMA)
    facts = _section(p, "FACTS:", "RETRIEVED CASES:")
    assert "1. [RATE/ALARM] fact 1" in facts and "F1" not in facts
    assert "CANDIDATE CAUSES" not in p and "FEED_VALVE" not in p
    assert "1. RCA-01 [group " in p and lm["facts"] == ["F1", "F2"]
    assert "OPERATOR QUESTION: why is level low?" in p           # rules off keeps it
    assert "OPERATOR QUESTION" not in _build(ALL)[0]              # rules on drops it


def test_compact_case_line_is_signature_and_first_sentence_of_the_check():
    p, _, _, _ = _build(ALL)
    line = [l for l in p.splitlines() if l.startswith("1. RCA-01")][0]
    assert "drum_level DOWN FAST" in line and "water_balance DEFICIT MED" in line
    assert line.endswith("check: Feed control valve demand saturated at 100% with feed "
                         "flow not responding.")
    assert BY_ID["RCA-01"]["title"] not in p and "cause=" not in line


# ------------------------------------------------------------------- caps
def test_at_most_nine_fact_lines_most_severe_first_and_the_drop_is_logged():
    facts = [_fact(i, "INFO") for i in range(1, 9)] + [_fact(9, "CRITICAL"),
             _fact(10, "ALARM"), _fact(11, "WATCH"), _fact(12, "INFO")]
    p, lm, _, info = _build(SCHEMA, facts=facts, level_cap=12)
    assert len(lm["facts"]) == 9 and lm["facts"][:3] == ["F9", "F10", "F11"]
    assert len(info["facts_dropped_by_line_cap"]) == 3
    assert "(3 lower-severity facts omitted" in p
    # the level cap still binds first (URGENT: 6)
    _, lm6, _, info6 = _build(SCHEMA, facts=facts, level_cap=6)
    assert len(lm6["facts"]) == 6 and info6["facts_dropped_by_line_cap"] == []


def test_notes_and_records_capped_at_four_newest_notes_first_and_drops_logged():
    notes = [{"id": f"n{i}", "author": "a", "text": f"t{i}"} for i in range(6)]
    nf = {f"n{i}": {"id": f"N{i + 1}", "note_id": f"n{i}", "status": "ok", "kind": "OBS",
                    "pairs": [["COAL", "WET"]], "t": 60.0 * i, "reliability": 0.5}
          for i in range(6)}
    nf["n9"] = dict(nf["n0"], id="N9", note_id="n9", t=9999.0)       # not retrieved
    nf["n5"] = {"id": None, "note_id": "n5", "status": "rejected", "why": "x"}
    rf = [{"id": f"R{i}", "text": f"rec {i}"} for i in range(1, 7)]
    p, lm, _, info = _build(ALL, retrieved=_retrieved(notes=notes), notefacts=nf,
                            recordfacts=rf)
    assert lm["context"] == ["N5", "N4", "N3", "N2", "R1", "R2", "R3", "R4"]
    assert info["notes_dropped"] == ["N1"] and info["records_dropped"] == ["R5", "R6"]
    assert "N9" not in p and "fcv" not in p


# ------------------------------------------------- expansion and the gate
def test_expansion_maps_case_and_fact_lines_through_the_line_map():
    _, lm, _, _ = _build(SCHEMA, belief=_belief(RCA_01=0.62, RCA_16=0.31))
    payload, info = compact.expand_diag_answer(
        {"g": "A", "r": [[2, [2]], [1, [1, 2]]], "sep": 2, "n": [], "x": [1]}, lm)
    h = payload["hypotheses"]
    assert [x["case_ref"] for x in h] == ["RCA-16", "RCA-01"]
    assert h[0]["cause"] == BY_ID["RCA-16"]["root_cause"]
    assert h[0]["discriminator"] == BY_ID["RCA-16"]["discriminating_evidence"]
    assert h[1]["supports"] == ["F1", "F2"]
    assert payload["unexplained"][0].startswith("t84.F1 not explained")
    assert info["sep"] == "RCA-16"


def test_confidence_comes_from_belief_capped_at_half_or_the_default():
    bel = _belief(RCA_01=0.62, RCA_16=0.31, RCA_11=-0.05)       # RCA-11 retired
    _, lm, _, _ = _build(SCHEMA, belief=bel)
    # 0.3: the single agent's merge default, amendment 2 option (ii)
    assert lm["conf"] == [0.62, 0.31, 0.3, 0.3]
    claims = initial_claims("h", [b for b in bel if b.case_ref == "RCA-16"])
    payload, _ = compact.expand_diag_answer({"r": [[1, [1]], [2, [1]], [3, [1]]]}, lm)
    from fieldmind.agent.orchestrator import merge
    out = {h["case_ref"]: h for h in merge(claims, payload)["hypotheses"]}
    assert out["RCA-01"]["model_only"] and out["RCA-01"]["confidence"] == 0.5
    assert out["RCA-16"]["confidence"] == 0.31 and not out["RCA-16"].get("model_only")
    assert out["RCA-11"]["confidence"] == 0.3


def test_compact_job_is_capped_at_60_answer_tokens_and_carries_its_line_map():
    from types import SimpleNamespace
    from fieldmind.multi.agents.diagnostician import DiagnosticianAgent
    bb = _board({84: [_fact(1, "ALARM"), _fact(2)]})
    with bb.step("retriever"):
        bb.write("retrieval", _retrieved(), "retriever")
    sched = Scheduler([SimLane("npu", MockBackend(), 909, 13.9)],
                      {"mode": "lockstep", "deadlines_s": {"P2": 30}})
    diag = SimpleNamespace(backend=MockBackend(), log_prompts=False, calls=0,
                           parse_failures=0, retries=0)
    mcfg = dict(CFG["multi"], compact=dict(CFG["multi"]["compact"], **{s: True for s in compact.SECTIONS}))
    agent = DiagnosticianAgent(diag, CFG["agent"], mcfg, LETTERS)
    job = agent.make_job(bb, sched, 84, "WATCH", 0, 0.0)
    assert job.max_answer_tokens == 60 != CFG["agent"]["max_tokens"]
    assert job.line_map["evidence_tick"] == 84 and job.line_map["facts"] == ["F1", "F2"]


def _board(ticks):
    bb = Blackboard(new_world_model("t", []), audit=True)
    with bb.step("sensor"):
        book = bb.mutable("facts", "sensor")
        for t, facts in ticks.items():
            book.add(t, facts)
    return bb


def _fold(bb, lm, answer, now_tick, claims=None):
    gate = GateMemoryAgent(Gate(ActionCatalogue(ROOT / "data/kb/action_catalogue.json"),
                                {"max_actions": 3}), ver=None,
                           cfg={"min_faithfulness": 0.5})
    env = AgentEnvelope(agent="diagnostician", tick=lm["evidence_tick"], status="ok",
                        payload=answer)
    s = Scheduler([SimLane("npu", MockBackend(), 909, 13.9)],
                  {"mode": "lockstep", "deadlines_s": {"P2": 30}})
    job = s.new_job("diagnostician", lm["evidence_tick"], 2, 60, 0.0)
    job.line_map = lm
    res = Result(job=job, envelope=env, evidence_tick=lm["evidence_tick"], lane="npu",
                 queue_wait_ms=0.0, start_s=0.0, finish_s=1.0)
    asmt = Assessment(tick=now_tick, timestamp="", state="DEVIATION", headline="h",
                      facts=[], triage="WATCH")
    claims = claims or {"headline": "h", "unexplained": [], "hypotheses": [
        {"rank": 1, "cause": BY_ID["RCA-11"]["root_cause"], "confidence": 0.7,
         "supports": [], "case_ref": "RCA-11", "discriminator": ""}]}
    with bb.step("gate"):
        out = gate.fold_diagnosis(bb, asmt, claims, res, now_tick)
    return out, asmt, gate


def test_group_letters_follow_the_look_alike_groups_then_singletons():
    assert LETTERS["RCA-01"] == LETTERS["RCA-16"] == "A"
    assert LETTERS["RCA-09"] == LETTERS["RCA-10"] == LETTERS["RCA-15"] == "B"
    assert LETTERS["RCA-14"] == LETTERS["RCA-18"] == "C"
    singles = [LETTERS[c] for c in ("RCA-03", "RCA-04", "RCA-05", "RCA-07", "RCA-11", "RCA-13")]
    assert singles == list("DEFGHI") and GROUPS["A"] == "RCA-01+RCA-16"


def test_copied_pieces_match_the_single_agent():
    """compact keeps two small copies (fieldmind/agent may not be edited on this
    branch): the record lines and the severity order. They must not drift."""
    from fieldmind.agent.l1_symbolize import evidence_packet
    from fieldmind.agent.l4_diagnose import Diagnostician
    rv = {"coal_lab_report": "GCV 4040", "maintenance_history": ["12 d ago: A"],
          "boiler_water_conductivity": "761 uS/cm", "alarm_log": ["x"]}
    d = Diagnostician(MockBackend(), {"max_tokens": 256})
    single = d.build_prompt("", {"records": rv}, "")
    block = single[single.index("PLANT RECORDS:\n") + 15:single.index("\n\nWORLD MODEL")]
    assert block.splitlines() == ["- " + l for l in compact._record_lines_full(rv)]
    facts = [_fact(1, "INFO"), _fact(2, "CRITICAL"), _fact(3, "WATCH"), _fact(4, "ALARM")]
    _, lm, _, _ = _build(SCHEMA, facts=facts)
    assert lm["facts"] == [l.split()[0] for l in evidence_packet(facts).splitlines()]


def test_late_answer_unexplained_text_carries_the_evidence_tick():
    f83 = [_fact(1, "ALARM", 83), _fact(2, "WATCH", 83)]
    bb = _board({83: f83, 84: [_fact(1, "INFO", 84)]})
    _, lm, _, _ = _build(SCHEMA, facts=f83, tick=83)
    out, _, _ = _fold(bb, lm, {"r": [[1, [1]]], "x": [2]}, now_tick=84)
    assert any(u.startswith("t83.F2 not explained") for u in out["unexplained"])
    assert not any(u.startswith("F2") for u in out["unexplained"])


def test_answer_for_a_tick_83_job_arriving_at_tick_84_maps_to_t83_ids():
    f83 = [_fact(1, "ALARM", 83), _fact(2, "WATCH", 83)]
    bb = _board({83: f83, 84: [_fact(1, "INFO", 84)]})       # F2 does not exist at 84
    _, lm, _, _ = _build(SCHEMA, facts=f83, tick=83)
    out, asmt, _ = _fold(bb, lm, {"r": [[1, [1, 2]]]}, now_tick=84)
    h = out["hypotheses"][0]
    assert h["case_ref"] == "RCA-01" and h["supports"] == ["t83.F1", "t83.F2"]
    # the envelope is recorded before stamping (Phase 1 gate), with its own tick
    assert asmt.envelopes[0]["tick"] == 83 and asmt.envelopes[0]["cited_facts"] == ["F1", "F2"]


def test_same_tick_answer_keeps_local_ids_like_phase_1():
    f = [_fact(1, "ALARM"), _fact(2)]
    _, lm, _, _ = _build(SCHEMA, facts=f, tick=84)
    out, asmt, gate = _fold(_board({84: f}), lm, {"r": [[2, [2]]]}, now_tick=84)
    assert out["hypotheses"][0]["supports"] == ["F2"]
    assert gate.compact_log[0]["answer"] == {"r": [[2, [2]]]}


def test_out_of_range_fact_line_is_an_invented_citation():
    f = [_fact(1, "ALARM"), _fact(2)]
    _, lm, _, _ = _build(SCHEMA, facts=f)
    out, asmt, gate = _fold(_board({84: f}), lm, {"r": [[1, [7]]]}, now_tick=84)
    assert asmt.envelopes[0]["cited_facts"] == ["line7"]
    # faithfulness 0 < 0.5: the deterministic ranking is kept, and it says why
    assert out["hypotheses"][0]["case_ref"] == "RCA-11"
    assert any("non-existent facts ['line7']" in u for u in out["unexplained"])
    assert gate.compact_log[0]["bad_fact_lines"] == 1


def test_out_of_range_or_repeated_case_line_is_dropped_and_counted():
    _, lm, _, _ = _build(SCHEMA)
    payload, info = compact.expand_diag_answer({"r": [[9, [1]], [1, [1]], [1, [2]]]}, lm)
    assert [h["case_ref"] for h in payload["hypotheses"]] == ["RCA-01"]
    assert info["bad_case_lines"] == 2


def test_empty_ranking_keeps_belief_and_says_no_listed_case_fits():
    f = [_fact(1, "ALARM")]
    _, lm, _, _ = _build(SCHEMA, facts=f)
    out, _, gate = _fold(_board({84: f}), lm, {"r": []}, now_tick=84)
    assert [h["case_ref"] for h in out["hypotheses"]] == ["RCA-11"]
    assert out["hypotheses"][0].get("carried") and compact.NO_CASE_FITS in out["unexplained"]
    assert gate.compact_log[0]["empty_ranking"]


# ------------------------------------------------------------ answer check
class Scripted(LLMBackend):
    name = "scripted-diag"

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens})
        return LLMReply(text=self.texts.pop(0), prefill_tokens=10, decode_tokens=5)


@pytest.mark.parametrize("bad", [
    '{"r":[[1,[1]],[2,[1]],[3,[1]],[4,[1]]]}',      # more than 3 cases
    '{"r":[["RCA-01",[1]]]}',                       # an id, not a line
    '{"r":[[1,["F1"]]]}',
    '{"r":[[1,[1]]],"n":["N1"]}',
    '{"r":[[1,[1]]],"sep":"1"}',
    '{"g":"A"}',                                    # no ranking
    '{"r":[[true,[1]]]}',
])
def test_malformed_answer_gets_one_repair_call_then_is_invalid(bad):
    b = Scripted([bad, bad])
    env, failed = call_model(b, "p", "diagnostician", 60, {}, "diagnostician", 84,
                             compact.check_diag_answer_shape, repair_template="{reason}{bad_output}")
    assert failed and env.status == "invalid_schema" and len(b.calls) == 2
    b = Scripted([bad, '{"r":[[1,[1]]]}'])
    env, failed = call_model(b, "p", "diagnostician", 60, {}, "diagnostician", 84,
                             compact.check_diag_answer_shape, repair_template="{reason}{bad_output}")
    assert failed and env.status == "ok" and env.payload == {"r": [[1, [1]]]}


# ------------------------------------------------------------------ guard
def test_guard_drops_records_then_notes_then_cases_never_below_two():
    notes = [{"id": "n1", "author": "a", "text": "t"}]
    nf = {"n1": {"id": "N1", "note_id": "n1", "status": "ok", "kind": "OBS",
                 "pairs": [["COAL", "WET"]], "t": 0.0, "reliability": 0.5}}
    rf = [{"id": "R1", "text": "rec 1"}, {"id": "R2", "text": "rec 2"}]
    kw = dict(retrieved=_retrieved(notes=notes), notefacts=nf, recordfacts=rf)
    _, lm, _, info = _build(ALL, guard=CFG["multi"]["compact_guard_cpt"], **kw)
    assert not info["guard_fired"] and len(lm["cases"]) == 4
    _, lm, _, info = _build(ALL, guard=0.5, **kw)               # forces it to fire
    assert info["guard_fired"]
    assert info["guard_dropped"] == ["R2", "R1", "N1", "RCA-07", "RCA-11"]
    assert len(lm["cases"]) == 2 and lm["context"] == []
    assert info["over_limit_after_guard"]          # ran out of lines to drop: flagged
    # an ablation prompt (a section left long) is flagged, never cut
    _, lm, _, info = _build(SCHEMA, guard=0.5, **kw)
    assert not info["guard_fired"] and info["over_limit_unguarded"] and len(lm["cases"]) == 4


# ---------------------------------------------------------------- episodes
DEV = ROOT / "data/episodes_dev"
needs_dev = pytest.mark.skipif(not (DEV / "dev_A01_fcv_seize").exists(),
                               reason="dev episodes not generated")


def _cfg(compact_on=(), order="score"):
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["compact"].update({s: s in compact_on for s in compact.SECTIONS},
                                   case_order=order)
    return cfg


def _calls(tmp_path, name, cfg, arch="multi", ep="dev_A01_fcv_seize"):
    log = tmp_path / f"{name}.jsonl"
    run = run_episode(Episode(DEV / ep), cfg, arch=arch, record_prompts=str(log))
    return run, [json.loads(l) for l in log.read_text().splitlines()]


def _decisions(run):
    keep = ("tick", "state", "triage", "actions", "escalate", "headline")
    return [{k: a.get(k) for k in keep} | {"h": [(h["case_ref"], h.get("supports"))
                                                  for h in a.get("hypotheses", [])]}
            for a in run["assessments"]]


@needs_dev
def test_episode_all_switches_off_sends_the_single_agents_prompts(tmp_path):
    _, multi = _calls(tmp_path, "m", _cfg())
    _, single = _calls(tmp_path, "s", _cfg(), arch="single")
    pick = lambda calls: [(c["role"], c["max_tokens"], c["prompt"]) for c in calls
                          if c["role"] != "text_reader"]
    assert pick(multi) == pick(single) and len(pick(multi)) > 0


@needs_dev
def test_episode_schema_on_every_diagnosis_call_capped_at_60(tmp_path):
    run, calls = _calls(tmp_path, "c", _cfg(compact.SECTIONS))
    diag = [c for c in calls if c["role"] == "diagnostician"]
    assert diag and all(c["max_tokens"] == 60 for c in diag)
    assert all("CANDIDATE CAUSES" not in c["prompt"] for c in diag)
    assert run["parse_failure_rate"] == 0.0


@needs_dev
def test_episode_shuffled_case_order_changes_prompts_not_the_mock_ranking(tmp_path):
    a, ca = _calls(tmp_path, "a", _cfg(["schema"]))
    b, cb = _calls(tmp_path, "b", _cfg(["schema"], order="shuffled"))
    pa = [c["prompt"] for c in ca if c["role"] == "diagnostician"]
    pb = [c["prompt"] for c in cb if c["role"] == "diagnostician"]
    assert pa != pb
    assert _decisions(a) == _decisions(b)
