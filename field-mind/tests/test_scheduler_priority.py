"""Scheduler priority order and one-waiting-job-per-(agent, side)."""

import pytest

from fieldmind.multi.lanes import SimLane
from fieldmind.multi.scheduler import Scheduler
from fieldmind.runtime.llm_backend import MockBackend

CFG = {"mode": "lockstep", "deadlines_s": {"P1": 10, "P2": 30, "P3": 60, "P4": None}}


def _sched():
    return Scheduler([SimLane("npu", MockBackend(), 909, 13.9),
                      SimLane("cpu", MockBackend(), 126, 42)], CFG)


def _run_order(s):
    order = []
    def mk(tag):
        def work():
            order.append(tag)
        return work
    return order, mk


def test_dispatch_is_by_priority_then_submit_order():
    s = _sched()
    order, mk = _run_order(s)
    # submitted lowest-urgency first; distinct (agent, side) so none replaced
    for agent, p in [("query", 4), ("verifier", 3), ("diag_heat", 2),
                     ("diag_water", 2), ("diag_urgent", 1)]:
        s.submit(s.new_job(agent, evidence_tick=5, priority=p,
                           max_answer_tokens=60, submit_s=0.0, work=mk(agent)))
    s.run_lockstep(0.0)
    assert order == ["diag_urgent", "diag_heat", "diag_water", "verifier", "query"]


def test_deadlines_come_from_priority():
    s = _sched()
    got = {p: s.new_job("a", 1, p, 60, 0.0).deadline_s for p in (1, 2, 3, 4)}
    assert got == {1: 10, 2: 30, 3: 60, 4: None}


def test_newer_job_replaces_waiting_one_for_same_agent_and_side():
    s = _sched()
    order, mk = _run_order(s)
    old = s.new_job("diagnostician", 5, 2, 60, 0.0, work=mk("old"))
    new = s.new_job("diagnostician", 6, 2, 60, 0.0, work=mk("new"))
    s.submit(old)
    s.submit(new)
    results = s.run_lockstep(0.0)
    assert order == ["new"]
    assert [r.evidence_tick for r in results] == [6]
    assert old.replaced and s.replaced == [old]


def test_different_sides_do_not_replace_each_other():
    s = _sched()
    order, mk = _run_order(s)
    s.submit(s.new_job("diag", 5, 2, 60, 0.0, work=mk("water"), side="water"))
    s.submit(s.new_job("diag", 5, 2, 60, 0.0, work=mk("heat"), side="heat"))
    s.run_lockstep(0.0)
    assert sorted(order) == ["heat", "water"]


def test_p0_is_never_a_job():
    with pytest.raises(ValueError):
        _sched().new_job("sensor", 1, 0, 60, 0.0)


def test_realtime_is_not_built():
    with pytest.raises(NotImplementedError):
        Scheduler([SimLane("npu", MockBackend(), 1, 1)], {"mode": "realtime"})
