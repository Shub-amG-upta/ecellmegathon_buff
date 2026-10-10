"""Phase 4 (demonstration subset): real-time mode and the two-model lanes.
Outcome-based: assertions on emitted runs (tick lags, lane spans, merge
records), on the runner's results, and on the lanes the config builds."""

import threading
import time
from pathlib import Path

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.agent.world_model import new_world_model
from fieldmind.multi.blackboard import Blackboard
from fieldmind.multi.lanes import SimLane
from fieldmind.multi.orchestrator import make_lanes
from fieldmind.multi.realtime import RealtimeRunner
from fieldmind.multi.scheduler import Scheduler
from fieldmind.runtime.llm_backend import LLMBackend, MockBackend

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
LLAMA = "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf"
GEMMA = "gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf"


def _cfg(fast=True):
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    if fast:
        from run_demo import _deep_merge
        _deep_merge(cfg, yaml.safe_load((ROOT / "configs/fast.yaml").read_text()))
    cfg["llm"]["backend"] = "mock"
    return cfg


# ------------------------------------------------------------ config and lanes
def test_fast_overlay_puts_both_diagnosticians_on_the_npu_and_keeps_base_values():
    cfg = _cfg()
    fp = cfg["multi"]["fixed_placement"]
    assert fp["diag_water"] == fp["diag_heat"] == "npu"
    assert fp["verifier"] == fp["text_reader"] == "cpu"
    assert cfg["multi"]["split"] and cfg["multi"]["split_on_change"]
    # merged, not replaced: the lane URLs and rates come from base.yaml
    assert cfg["multi"]["lanes"]["npu"]["url"].endswith(":8080")
    assert cfg["multi"]["lanes"]["cpu"]["prefill_tok_s"] > 0


def test_each_lane_gets_its_own_model_file_on_llamaserver():
    cfg = _cfg()
    cfg["llm"]["backend"] = "llamaserver"
    lanes = {l.name: l for l in make_lanes(cfg, None)}
    assert lanes["npu"].backend.model_file == LLAMA and lanes["npu"].backend.url.endswith(":8080")
    assert lanes["cpu"].backend.model_file == GEMMA and lanes["cpu"].backend.url.endswith(":8081")


# ------------------------------------------------------------ the runner
class Slow(LLMBackend):
    """The mock, made slower than a tick on purpose."""
    name = "slow-mock"

    def __init__(self, delay=None):
        self.inner = MockBackend()
        self.delay = delay or {"diagnostician": 0.15, "verifier": 0.2, "text_reader": 0.2}

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        time.sleep(self.delay.get(role, 0.1))
        return self.inner.generate(prompt, role=role, max_tokens=max_tokens, mock_hint=mock_hint)


def _runner(work_s=0.0):
    sched = Scheduler([SimLane("npu", MockBackend(), 909, 13.9), SimLane("cpu", MockBackend(), 126, 42)],
                      {"mode": "lockstep", "placement": "fixed", "deadlines_s": {"P2": 30, "P3": 60},
                       "fixed_placement": {"diag_water": "npu", "verifier": "cpu"}})
    return sched, RealtimeRunner(sched)


def test_runner_needs_fixed_placement():
    sched = Scheduler([SimLane("npu", MockBackend(), 909, 13.9)],
                      {"mode": "lockstep", "placement": "earliest_finish", "deadlines_s": {}})
    with pytest.raises(ValueError):
        RealtimeRunner(sched)


def test_newer_job_replaces_a_waiting_one_and_a_failing_call_returns_an_error_envelope():
    sched, rt = _runner()
    gate = threading.Event()
    block = sched.new_job("diag_water", 1, 2, 60, 0.0, work=lambda: (gate.wait(5), None)[1], side="water")
    old = sched.new_job("diag_water", 2, 2, 60, 0.0, work=lambda: "old", side="water")
    new = sched.new_job("diag_water", 3, 2, 60, 0.0, work=lambda: "new", side="water")
    boom = sched.new_job("verifier", 3, 3, 30, 0.0, work=lambda: 1 / 0, side="all")
    rt.submit(block)
    time.sleep(0.05)                       # block is running; the next two wait
    rt.submit(old)
    rt.submit(new)
    rt.submit(boom)
    gate.set()
    assert rt.wait_idle(5)
    got = {r.job.job_id: r for r in rt.drain()}
    rt.close()
    assert old.job_id not in got and got[new.job_id].envelope == "new"
    assert got[boom.job_id].envelope.status == "error" and got[boom.job_id].lane == "cpu"
    assert got[new.job_id].queue_wait_ms > 0


def test_a_step_on_a_worker_thread_is_not_audited_and_marks_no_writer():
    bb = Blackboard(new_world_model("t", []), audit=True)
    seen = {}

    def work():
        with bb.step("diagnostician"):
            seen["active"] = bb._active
    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert seen["active"] is None
    with bb.step("gate"):                  # the main thread still audits
        assert bb._active == "gate"
    # and nothing may write the board from a worker thread
    from fieldmind.multi.blackboard import WriterError
    err = {}

    def write():
        try:
            bb.write("diagnosis", {}, "gate")
        except WriterError as e:
            err["e"] = str(e)
    t = threading.Thread(target=write)
    t.start()
    t.join()
    assert "off the tick thread" in err.get("e", "")


# ------------------------------------------------------------ an episode
needs_dev = pytest.mark.skipif(not (DEV / "dev_B02_tube_leak_fast").exists(),
                               reason="dev episodes not generated")
TICK = 0.2       # diagnosis calls (0.15 s) fit in a tick; verifier / text reader (0.3 s) do not


@pytest.fixture(scope="module")
def rt_run():
    cfg = _cfg()
    cfg["agent"]["verifier"] = "always"      # so slow verifier calls really happen
    return run_episode(Episode(DEV / "dev_B02_tube_leak_fast"), cfg, arch="multi",
                       wrap_backend=lambda b: _Route(b), mode="realtime", tick_s=TICK)


class _Route(LLMBackend):
    """Slows every call but keeps the lane routing of the facade below it."""
    name = "slow-lanes"

    def __init__(self, inner):
        self.inner = inner
        self.delay = {"diagnostician": 0.15, "verifier": 0.3, "text_reader": 0.3}

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        time.sleep(self.delay.get(role, 0.1))
        return self.inner.generate(prompt, role=role, max_tokens=max_tokens, mock_hint=mock_hint)


@needs_dev
def test_episode_ticks_never_wait_for_a_model(rt_run):
    rt = rt_run["multi"]["realtime"]
    # verifier and text-reader calls are longer than a tick; every tick still starts on time
    assert rt["tick_lag_s_max"] < TICK
    p0 = [a["multi"]["p0_ms"] for a in rt_run["assessments"] if a.get("multi")]
    assert p0 and max(p0) < 200
    assert rt_run["mode"] == "realtime" and rt["idle_at_end"]


@needs_dev
def test_episode_both_lanes_work_at_the_same_time(rt_run):
    rt = rt_run["multi"]["realtime"]
    assert rt["calls"]["npu"] > 0 and rt["calls"]["cpu"] > 0
    assert rt["both_lanes_busy_s"] > 0
    agents = {(s["agent"], s["lane"]) for s in rt["spans"]}
    assert agents <= {("diag_water", "npu"), ("diag_heat", "npu"),
                      ("verifier", "cpu"), ("text_reader", "cpu")}


@needs_dev
def test_episode_no_fresh_answer_older_than_one_tick_is_merged(rt_run):
    merged = 0
    for a in rt_run["assessments"]:
        for c in (a.get("multi") or {}).get("compact", []):
            if c.get("agent") != "merge":
                continue
            for side, ev in zip(c["order"], c["evidence_ticks"]):
                if side not in c["reused"]:
                    assert c["now_tick"] - ev <= 1
                    merged += 1
    assert merged > 0                                # diagnoses arrive within a tick and are merged
    stale = rt_run["multi"]["stale_dropped"]
    assert stale and all(s["now_tick"] - s["evidence_tick"] > 1 for s in stale)


@needs_dev
def test_episode_never_two_jobs_in_flight_for_a_side_with_the_same_evidence(rt_run):
    spans = {s["job"]: s for s in rt_run["multi"]["realtime"]["spans"]}
    subs = [s for a in rt_run["assessments"] for s in (a.get("multi") or {}).get("submitted", [])
            if s["agent"].startswith("diag_")]
    assert subs
    by_side = {}
    for s in subs:
        by_side.setdefault(s["side"], []).append(s)
    for jobs in by_side.values():
        for prev, nxt in zip(jobs, jobs[1:]):
            if prev["fp"] == nxt["fp"] and prev["job"] in spans:
                # the same evidence is asked again only after the first answer came back
                assert spans[prev["job"]]["end_s"] <= nxt["wall_s"]


@needs_dev
def test_episode_ticks_are_paced_by_the_wall_clock_and_never_early(rt_run):
    lags = rt_run["multi"]["realtime"]["tick_lag_s"]
    assert len(lags) == rt_run["n_ticks"] and min(lags) >= 0.0


@needs_dev
def test_episode_verifier_is_sent_only_after_a_fresh_side_answer_was_merged(rt_run):
    sent = 0
    for a in rt_run["assessments"]:
        m = a.get("multi") or {}
        if not any(s["agent"] == "verifier" for s in m.get("submitted", [])):
            continue
        sent += 1
        merges = [c for c in m.get("compact", []) if c.get("agent") == "merge"]
        assert merges and any(side not in merges[0]["reused"] for side in merges[0]["order"])
    assert sent > 0


@needs_dev
def test_episode_each_note_is_read_once(rt_run):
    ep = Episode(DEV / "dev_B02_tube_leak_fast")
    arrived = [n for n in ep.notes if n["t"] <= ep.duration_s]
    spans = [s for s in rt_run["multi"]["realtime"]["spans"] if s["agent"] == "text_reader"]
    assert rt_run["text_calls"] == len(spans) == len(arrived) > 0


def test_a_stale_answer_does_not_block_reuse_of_the_sides_cached_answer():
    from tests.test_on_change import FACTS, _fold, _rig, _with_fp
    from tests.test_split import _result, _sched
    from fieldmind.multi.agents.diagnostician import DiagnosticianAgent
    s = _sched()
    bb = _rig(FACTS)
    _fold(bb, [_with_fp(_result("water", 84, {"r": [[1, [1]]]}, s), bb)])
    cache = dict(bb.read("side_answers"))
    bb2 = _rig(FACTS, cache=cache)
    late = _result("water", 81, {"r": [[2, [1]]]}, s)          # three ticks old: stale
    late.job.line_map["fingerprint"] = DiagnosticianAgent.fingerprint(bb2, "water")
    out, gate = _fold(bb2, [late])
    assert late.stale and gate.compact_log[-1]["reused"] == ["water"]
    assert [h["case_ref"] for h in out["hypotheses"]][0] == "RCA-01"


class _SlowDiag(_Route):
    def __init__(self, inner):
        super().__init__(inner)
        self.delay = {"diagnostician": 0.45, "verifier": 0.1, "text_reader": 0.1}


@needs_dev
def test_episode_slow_diagnosis_is_not_asked_again_while_it_runs():
    """Each diagnosis takes longer than two ticks: the side's job is still
    running when the next ticks come, and the same evidence is not sent again."""
    run = run_episode(Episode(DEV / "dev_B02_tube_leak_fast"), _cfg(), arch="multi",
                      wrap_backend=lambda b: _SlowDiag(b), mode="realtime", tick_s=TICK)
    spans = {s["job"]: s for s in run["multi"]["realtime"]["spans"]}
    subs = [s for a in run["assessments"] for s in (a.get("multi") or {}).get("submitted", [])
            if s["agent"].startswith("diag_")]
    pairs = 0
    by_side = {}
    for s in subs:
        by_side.setdefault(s["side"], []).append(s)
    for jobs in by_side.values():
        for prev, nxt in zip(jobs, jobs[1:]):
            if prev["fp"] == nxt["fp"] and prev["job"] in spans:
                assert spans[prev["job"]]["end_s"] <= nxt["wall_s"]
                pairs += 1
    model_ticks = [a for a in run["assessments"] if a["triage"] != "QUIET"]
    assert subs and len(subs) < len(model_ticks)            # not asked again every tick
    # calls of 0.45 s, longer than two ticks: the tick still never waits
    assert run["multi"]["realtime"]["tick_lag_s_max"] < TICK
    assert max(a["multi"]["p0_ms"] for a in run["assessments"] if a.get("multi")) < 200
    # late answers are USED while their side's evidence is unchanged (plan p.10)
    assert run["multi"]["accepted_late_unchanged_evidence"] > 0
    fresh = [c for a in run["assessments"] for c in (a.get("multi") or {}).get("compact", [])
             if c.get("agent") == "merge" and set(c["order"]) - set(c["reused"])]
    assert fresh


@needs_dev
def test_realtime_summary_reports_lanes_and_flags_calls_slower_than_a_tick(rt_run):
    from bench.realtime_summary import summarise
    s = summarise(rt_run)
    assert s["mode"] == "realtime" and s["wall_tick_s"] == TICK
    assert s["calls"]["diag_water@npu"] > 0 and s["calls"]["verifier@cpu"] > 0
    # verifier calls (0.3 s) are slower than the 0.2 s tick; diagnoses (0.15 s) are not
    assert "verifier@cpu" in s["calls_slower_than_a_tick"]
    assert "diag_water@npu" not in s["calls_slower_than_a_tick"]


@needs_dev
def test_on_assessment_hook_sees_every_tick_live_and_changes_nothing():
    ep = Episode(DEV / "dev_B02_tube_leak_fast")
    seen = []
    a = run_episode(ep, _cfg(), arch="multi")
    b = run_episode(ep, _cfg(), arch="multi", on_assessment=lambda d: seen.append(d["tick"]))
    assert seen == [x["tick"] for x in b["assessments"]] and seen
    keep = ("tick", "state", "triage", "hypotheses", "actions", "escalate")
    assert [{k: x.get(k) for k in keep} for x in a["assessments"]] == \
           [{k: x.get(k) for k in keep} for x in b["assessments"]]
    live = []
    run_episode(ep, _cfg(), arch="multi", mode="realtime", tick_s=0.05,
                on_assessment=lambda d: live.append((d["tick"], time.monotonic())))
    assert len(live) == len(seen) and all(t2 > t1 for (_, t1), (_, t2) in zip(live, live[1:]))
