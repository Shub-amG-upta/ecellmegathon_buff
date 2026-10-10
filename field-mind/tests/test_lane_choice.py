"""Lane choice by earliest predicted finish, and queueing on the simulated clock.

A stub backend reports fixed token counts so every simulated time is exact.
"""

import pytest

from fieldmind.multi.lanes import LaneBackend, LaneBusy, SimLane
from fieldmind.multi.scheduler import Scheduler
from fieldmind.runtime.llm_backend import LLMBackend, LLMReply

CFG = {"mode": "lockstep", "deadlines_s": {"P1": 10, "P2": 30, "P3": 60, "P4": None}}


class StubBackend(LLMBackend):
    """Reports prompt tokens = len(prompt)//4 and a fixed answer length."""
    name = "stub"

    def __init__(self, answer_tokens):
        self.answer_tokens = answer_tokens

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        return LLMReply(text="{}", prefill_tokens=len(prompt) // 4,
                        decode_tokens=self.answer_tokens)


def _setup(lanes):
    s = Scheduler(lanes, CFG)
    return s, LaneBackend(s)


def _job(s, facade, prompt_tokens, max_answer, submit_s=0.0, agent="a"):
    prompt = "x" * (4 * prompt_tokens)          # est_tokens(prompt) == prompt_tokens
    job = s.new_job(agent, 1, 2, max_answer, submit_s,
                    work=lambda: facade.generate(prompt, max_tokens=max_answer))
    s.submit(job)
    return job


# Lane A reads prompts fast and writes slowly (NPU-like); lane B the opposite.
def _ab(answer_tokens=10):
    return [SimLane("A", StubBackend(answer_tokens), prefill_tok_s=1000, decode_tok_s=10),
            SimLane("B", StubBackend(answer_tokens), prefill_tok_s=100, decode_tok_s=100)]


def test_prompt_heavy_job_goes_to_fast_prefill_lane():
    s, f = _setup(_ab())
    j = _job(s, f, prompt_tokens=4000, max_answer=10)   # A: 4.01 s, B: 40.1 s
    s.run_lockstep(0.0)
    assert j.lane == "A"
    assert j.predicted_finish_s == pytest.approx(4.0 + 1.0)


def test_decode_heavy_job_goes_to_fast_decode_lane():
    s, f = _setup(_ab())
    j = _job(s, f, prompt_tokens=40, max_answer=200)    # A: 20.04 s, B: 2.4 s
    s.run_lockstep(0.0)
    assert j.lane == "B"


def test_busy_lane_loses_to_idle_lane():
    lanes = _ab()
    lanes[0].free_at = 100.0                            # A busy for 100 s
    s, f = _setup(lanes)
    j = _job(s, f, prompt_tokens=4000, max_answer=10)   # A: 105 s, B: 40.1 s
    s.run_lockstep(0.0)
    assert j.lane == "B"


def test_tie_goes_to_first_lane_in_config_order():
    s, f = _setup([SimLane("npu", StubBackend(10), 100, 10),
                   SimLane("cpu", StubBackend(10), 100, 10)])
    j = _job(s, f, prompt_tokens=100, max_answer=10)
    s.run_lockstep(0.0)
    assert j.lane == "npu"


def test_queue_wait_on_one_lane_is_exact():
    # one lane: 2000 prompt tokens / 1000 + 10 answer tokens / 10 = 3.0 s each
    s, f = _setup([SimLane("A", StubBackend(10), 1000, 10)])
    j1 = _job(s, f, 2000, 10, agent="a1")
    j2 = _job(s, f, 2000, 10, agent="a2")
    r1, r2 = s.run_lockstep(0.0)
    assert (r1.start_s, r1.finish_s, r1.queue_wait_ms) == (0.0, 3.0, 0.0)
    assert (r2.start_s, r2.finish_s) == (3.0, 6.0)
    assert r2.queue_wait_ms == pytest.approx(3000.0)


def test_answer_longer_than_cap_is_clocked_at_the_cap():
    # The mock ignores max_tokens; a real lane cannot decode past it.
    s, f = _setup([SimLane("A", StubBackend(964), 1000, 10)])
    j = _job(s, f, 1000, 256)
    (r,) = s.run_lockstep(0.0)
    assert r.finish_s == pytest.approx(1.0 + 25.6)


def test_lane_runs_one_job_at_a_time():
    s, f = _setup(_ab())
    lane = s.lanes[0]
    j1 = s.new_job("a", 1, 2, 10, 0.0)
    j2 = s.new_job("b", 1, 2, 10, 0.0)
    lane.begin(j1)
    with pytest.raises(LaneBusy):
        lane.begin(j2)
    lane.end(j1)
    lane.begin(j2)                                      # free again


def test_repair_call_stays_on_the_lane_of_the_first_call():
    s, f = _setup(_ab())
    big, small = "x" * 16000, "x" * 160               # 4000 tokens, then 40
    def work():
        f.generate(big, max_tokens=10)                 # picks A
        f.generate(small, max_tokens=200)              # alone would pick B
    j = s.new_job("a", 1, 2, 10, 0.0, work=work)
    s.submit(j)
    s.run_lockstep(0.0)
    assert j.lane == "A" and s.lanes[0].n_jobs == 1 and s.lanes[1].n_jobs == 0


def test_lane_is_released_when_a_job_raises():
    s, f = _setup(_ab())
    def boom():
        f.generate("x" * 400, max_tokens=10)
        raise RuntimeError("agent crashed mid-job")
    s.submit(s.new_job("a", 1, 2, 10, 0.0, work=boom))
    with pytest.raises(RuntimeError):
        s.run_lockstep(0.0)
    assert all(l.running is None for l in s.lanes)
    j = _job(s, f, prompt_tokens=4000, max_answer=10, agent="b")
    s.run_lockstep(0.0)                                 # no LaneBusy
    assert j.finish_s is not None
