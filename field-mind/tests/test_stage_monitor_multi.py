"""bench/stage_monitor_multi.py: the multi-agent viewer reads a run's tick
file. Outcome-based: assertions on the rows, cells and lane lines it produces
from tick records shaped like the ones run_demo.py --live-ticks writes."""

from bench.stage_monitor_multi import build, cell, lane_now, render

PLACE = {"diag_water": "npu", "diag_heat": "npu", "verifier": "cpu", "text_reader": "cpu"}


def _tick(k, triage="WATCH", submitted=(), results=(), compact=(), text=()):
    return {"tick": k, "timestamp": f"2026-08-21T06:{k:02d}:00", "state": "DEVIATION",
            "triage": triage, "hypotheses": [{"cause": "cause A"}],
            "multi": {"p0_ms": 50.0, "submitted": list(submitted), "results": list(results),
                      "compact": list(compact), "text": list(text)}}


def _sent(job, agent, side, wall_s):
    return {"job": job, "agent": agent, "side": side, "wall_s": wall_s}


def _got(job, agent, tick, lane, start, finish, stale=False):
    return {"job_id": job, "agent": agent, "evidence_tick": tick, "lane": lane,
            "queue_wait_ms": 1.0, "start_s": start, "finish_s": finish, "stale": stale}


RECORDS = [
    _tick(9, triage="QUIET"),
    _tick(10, submitted=[_sent("j1", "diag_water", "water", 30.0)]),
    _tick(11, submitted=[_sent("j2", "diag_water", "water", 60.0),
                         _sent("j3", "verifier", "all", 60.0)],
          results=[_got("j1", "diag_water", 10, "npu", 30.0, 32.5)],
          compact=[{"agent": "diagnostician", "side": "water", "evidence_tick": 10,
                    "answer": {"r": [[1, [1]]]}},
                   {"agent": "merge", "reused": []}]),
    _tick(12, results=[_got("j2", "diag_water", 11, "npu", 60.0, 64.0),
                       _got("j3", "verifier", 11, "cpu", 60.0, 61.0)],
          compact=[{"agent": "diagnostician", "side": "water", "evidence_tick": 11},
                   {"agent": "verifier", "evidence_tick": 11, "answer": {"v": []}},
                   {"agent": "merge", "reused": ["heat"]}]),
    _tick(13, submitted=[_sent("j4", "diag_heat", "heat", 120.0)]),
]


def _cells(records, now_s, busy=None):
    m = build(records)
    return {r["tick"]: [cell(r, a, m["jobs"], now_s, busy)
                        for a in ("diag_water", "diag_heat", "verifier", "text_reader")]
            for r in m["rows"]}


def test_cells_show_each_jobs_time_and_what_the_gate_made_of_it():
    c = _cells(RECORDS, now_s=123.0)
    assert c[9] == ["—", "—", "—", "—"]                 # quiet tick
    assert c[10][0] == "2.5s✓"                           # well-formed answer, used
    assert c[11][0] == "4.0s✗" and c[11][2] == "1.0s✓"  # no answer record -> not usable
    assert c[12][1] == "reuse" and c[12][0] == "·"       # heat kept its earlier answer
    assert c[13][1] == "▶3s"                             # sent at 120.0, now 123.0


def test_a_job_shows_as_running_until_its_answer_is_in_the_file():
    before = _cells(RECORDS[:3], now_s=70.0)
    assert before[11][0] == "▶10s" and before[11][2] == "▶10s"
    assert _cells(RECORDS[:3], now_s=70.0, busy=False)[11][0] == "answered"
    assert _cells(RECORDS[:4], now_s=95.0)[11][0] == "4.0s✗"


def test_lane_line_names_the_oldest_job_out_on_that_lane():
    jobs = build(RECORDS[:3])["jobs"]
    text, working = lane_now("npu", jobs, PLACE, True, 61.5)
    assert working and "tick 11" in text and "water diagnosis" in text and "1.5 s ago" in text
    text, working = lane_now("cpu", jobs, PLACE, True, 61.5)
    assert working and "verifier" in text
    # the board says idle: the call is over, whatever the tick file still lacks
    text, working = lane_now("npu", jobs, PLACE, False, 61.5)
    assert not working and "answered" in text
    # two jobs out on one lane: the oldest is the one running, the other waits
    two = RECORDS + [_tick(14, submitted=[_sent("j5", "diag_water", "water", 150.0)])]
    text, working = lane_now("npu", build(two)["jobs"], PLACE, True, 151.0)
    assert working and "tick 13" in text and "heat diagnosis" in text and "+1 waiting" in text
    # nothing out on either lane after the answers arrived
    jobs = build(RECORDS[:4])["jobs"]
    assert lane_now("npu", jobs, PLACE, False, 95.0) == ("idle", False)
    assert lane_now("cpu", jobs, PLACE, None, 95.0) == ("idle", False)


def test_render_puts_units_and_ticks_on_one_screen():
    meta = {"episode": "ep_X", "tick_s": 30.0, "host": "laptop", "placement": PLACE,
            "models": {"npu": "Llama", "cpu": "Gemma"}, "poll": True, "last_tick": 139}
    out = render(build(RECORDS), meta, {"npu": True, "cpu": False}, 123.0, (200, 60),
                 colour=False)
    text = "\n".join(out)
    assert "tick 13 of 139" in text and "model calls answered 3 · still out 1" in text
    npu = next(l for l in out if l.startswith(" NPU"))
    assert "tick 13" in npu and "heat diagnosis" in npu and "running" in npu
    assert next(l for l in out if l.startswith(" CPU")).rstrip().endswith("idle")
    assert sum(l.lstrip().split(" ")[0].isdigit() for l in out) == len(RECORDS)


# ---------------------------------------------------------------- back to back
def _lockstep_tick(k):
    """A lockstep tick: no `submitted`; job times in `results` are SIMULATED."""
    return {"tick": k, "timestamp": f"2026-08-21T06:{k:02d}:00", "state": "ALARM",
            "triage": "INVESTIGATE", "hypotheses": [{"cause": "cause A"}],
            "tick_latency_ms": 9000.0,
            "envelopes": [
                {"agent": "diagnostician", "status": "ok", "latency_ms": 2500.0, "backend": "npu"},
                {"agent": "diagnostician", "status": "invalid_schema", "latency_ms": 5200.0,
                 "backend": "npu"},
                {"agent": "verifier", "status": "ok", "latency_ms": 1300.0, "backend": "cpu"}],
            "multi": {"p0_ms": 60.0,
                      "results": [_got("j1", "diag_water", k, "npu", 1000.0, 1099.0),
                                  _got("j2", "diag_heat", k, "npu", 1099.0, 1150.0),
                                  _got("j3", "verifier", k, "cpu", 1150.0, 1190.0)],
                      "compact": [{"agent": "diagnostician", "side": "water", "evidence_tick": k,
                                   "answer": {"r": [[1, [1]]]}},
                                  {"agent": "diagnostician", "side": "heat", "evidence_tick": k},
                                  {"agent": "merge", "reused": []},
                                  {"agent": "verifier", "evidence_tick": k, "answer": {"v": []}}],
                      "text": [{"notefact": {"status": "rejected"},
                                "envelope": {"agent": "text_reader", "status": "ok",
                                             "latency_ms": 1800.0, "backend": "cpu"}}]}}


def test_back_to_back_cells_show_the_real_call_times_not_the_simulated_clock():
    from bench.stage_monitor_multi import is_lockstep
    recs = [_lockstep_tick(20)]
    assert is_lockstep(recs) and not is_lockstep(RECORDS)
    c = _cells(recs, now_s=0.0)
    # envelope latency_ms (2.5, 5.2, 1.3, 1.8 s), never results' 99 / 51 / 40 s
    assert c[20] == ["2.5s✓", "5.2s✗", "1.3s✓", "1.8s✗"]
    jobs = build(recs)["jobs"]
    assert sorted((j["agent"], j["lane"]) for j in jobs) == [
        ("diag_heat", "npu"), ("diag_water", "npu"), ("text_reader", "cpu"), ("verifier", "cpu")]
    # the lane works on the tick that is not in the file yet
    text, working = lane_now("npu", jobs, PLACE, True, 0.0, running_tick=21)
    assert working and "tick 21" in text


def test_back_to_back_viewer_counts_the_calls_of_a_real_lockstep_run(tmp_path, monkeypatch):
    import json
    import sys
    from pathlib import Path

    import pytest

    import run_demo
    from bench.stage_monitor_multi import is_lockstep
    root = Path(__file__).resolve().parent.parent
    dev = root / "data/episodes_dev"
    if not (dev / "dev_B02_tube_leak_fast").exists():
        pytest.skip("dev episodes not generated")
    monkeypatch.chdir(root)
    live = tmp_path / "ticks.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "run_demo.py", "--backend", "mock", "--arch", "multi", "--overlay", "configs/fast.yaml",
        "--mode", "lockstep", "--episodes-dir", str(dev), "--episode", "dev_B02_tube_leak_fast",
        "--live-ticks", str(live), "--tag", "t", "--out", str(tmp_path)])
    run_demo.main()
    run = json.loads((tmp_path / "runs_mock_t.json").read_text())[0]
    recs = [json.loads(x) for x in live.read_text().splitlines()]
    assert is_lockstep(recs)
    jobs = build(recs)["jobs"]
    n = lambda pre: sum(j["agent"].startswith(pre) for j in jobs)
    assert n("diag_") == run["diag_calls"] > 0
    assert n("verifier") == run["ver_calls"] and n("text_reader") == run["text_calls"] > 0
    assert all(j["verdict"] in ("ok", "empty", "bad") and j["finish_s"] is not None for j in jobs)
    meta = {"episode": "dev", "tick_s": 30.0, "host": "laptop", "placement": PLACE,
            "models": {}, "poll": False, "lockstep": True, "finished": True}
    assert "BACK TO BACK" in "\n".join(render(build(recs), meta, {}, 10.0, (200, 400), colour=False))
