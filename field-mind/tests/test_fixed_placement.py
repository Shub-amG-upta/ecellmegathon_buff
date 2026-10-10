"""Fixed placement (plan p.11): each agent has one lane and waits for it.

Every assertion is on a dispatched job or a returned Result, never on the
placement table itself. Lane rates are chosen so that earliest-finish would
pick the OTHER lane in each case, so a fixed mode that falls through to
earliest finish fails here.
"""

from pathlib import Path

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.multi.lanes import LaneBackend, SimLane
from fieldmind.multi.scheduler import Scheduler
from fieldmind.runtime.llm_backend import LLMBackend, LLMReply

TABLE = {"diagnostician": "npu", "diag_water": "npu", "diag_heat": "cpu",
         "verifier": "cpu", "text_reader": "cpu", "query": "cpu"}
DEADLINES = {"P1": 10, "P2": 30, "P3": 60, "P4": None}


class StubBackend(LLMBackend):
    name = "stub"

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        return LLMReply(text="{}", prefill_tokens=len(prompt) // 4,
                        decode_tokens=10)


def _lanes(npu=(1000, 10), cpu=(100, 100)):
    return [SimLane("npu", StubBackend(), *npu), SimLane("cpu", StubBackend(), *cpu)]


def _setup(placement="fixed", lanes=None, table=TABLE):
    s = Scheduler(lanes or _lanes(), {"mode": "lockstep", "deadlines_s": DEADLINES,
                                      "placement": placement,
                                      "fixed_placement": table})
    return s, LaneBackend(s)


def _job(s, f, agent, prompt_tokens, max_answer, submit_s=0.0):
    prompt = "x" * (4 * prompt_tokens)
    job = s.new_job(agent, 1, 2, max_answer, submit_s,
                    work=lambda: f.generate(prompt, max_tokens=max_answer))
    s.submit(job)
    return job


# A short prompt with a long answer: earliest finish sends it to the cpu lane
# (fast decode); a long prompt with a short answer goes to the npu lane.
DECODE_HEAVY = dict(prompt_tokens=40, max_answer=200)     # npu 20.04 s, cpu 2.4 s
PROMPT_HEAVY = dict(prompt_tokens=4000, max_answer=10)    # npu 5.0 s, cpu 40.1 s


@pytest.mark.parametrize("agent,lane", sorted(TABLE.items()))
def test_every_agent_lands_on_its_assigned_lane(agent, lane):
    # the shape that earliest finish would send to the OTHER lane
    shape = DECODE_HEAVY if lane == "npu" else PROMPT_HEAVY
    s, f = _setup()
    j = _job(s, f, agent, **shape)
    (r,) = s.run_lockstep(0.0)
    assert j.lane == lane and r.lane == lane
    other = next(l for l in s.lanes if l.name != lane)
    assert other.n_jobs == 0


def test_same_jobs_go_elsewhere_under_earliest_finish():
    """The control: with the switch on earliest_finish the same two shapes land
    on the opposite lanes, so the test above is testing the mode."""
    s, f = _setup("earliest_finish")
    a = _job(s, f, "diagnostician", **DECODE_HEAVY)
    b = _job(s, f, "verifier", **PROMPT_HEAVY)
    s.run_lockstep(0.0)
    assert (a.lane, b.lane) == ("cpu", "npu")


def test_fixed_job_waits_for_its_busy_lane_while_the_other_is_idle():
    lanes = _lanes()
    lanes[0].free_at = 100.0                       # npu busy until t=100 s
    s, f = _setup(lanes=lanes)
    j = _job(s, f, "diagnostician", prompt_tokens=2000, max_answer=10)
    (r,) = s.run_lockstep(0.0)
    assert r.lane == "npu"
    assert r.start_s == 100.0
    assert r.queue_wait_ms == pytest.approx(100_000.0)
    assert r.finish_s == pytest.approx(100.0 + 2.0 + 1.0)
    assert s.lanes[1].n_jobs == 0                  # the idle cpu lane stayed idle


def test_repair_call_stays_on_the_assigned_lane():
    s, f = _setup()

    def work():
        f.generate("x" * 160, max_tokens=200)      # first call
        f.generate("x" * 16000, max_tokens=10)     # repair, different shape

    j = s.new_job("verifier", 1, 3, 30, 0.0, work=work)
    s.submit(j)
    s.run_lockstep(0.0)
    assert j.lane == "cpu"
    assert (s.lanes[0].n_jobs, s.lanes[1].n_jobs) == (0, 1)


def test_agent_without_a_lane_raises_and_is_not_placed():
    s, f = _setup()
    j = _job(s, f, "mystery_agent", **PROMPT_HEAVY)
    with pytest.raises(KeyError):
        s.run_lockstep(0.0)
    assert j.lane is None
    assert all(l.n_jobs == 0 and l.running is None for l in s.lanes)


def test_fixed_mode_without_a_table_is_refused():
    with pytest.raises(ValueError):
        _setup(table={})


def test_table_naming_an_unknown_lane_is_refused():
    with pytest.raises(ValueError):
        _setup(table={"diagnostician": "gpu"})


def test_unknown_placement_is_refused():
    with pytest.raises(ValueError):
        _setup("round_robin")


# ---------------------------------------------------------------- episode
DEV = Path("data/episodes_dev")


def _dev_episode(fragment):
    hits = sorted(p for p in DEV.iterdir() if fragment in p.name) if DEV.exists() else []
    if not hits:
        pytest.skip("dev episodes not generated")
    return Episode(hits[0])


def _results(run):
    return [r for a in run["assessments"] for r in a["multi"]["results"]]


def test_episode_every_job_runs_on_its_table_lane():
    cfg = yaml.safe_load(Path("configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["placement"] = "fixed"
    run = run_episode(_dev_episode("C02"), cfg, arch="multi")
    res = _results(run)
    agents = {r["agent"] for r in res}
    assert {"diagnostician", "verifier"} <= agents      # both kinds occurred
    # literal lanes (plan p.11 + the Phase 2 human decision), not the config's
    # own table, so a wrong table in base.yaml fails here
    expected = {"diagnostician": "npu", "verifier": "cpu"}
    assert all(r["lane"] == expected[r["agent"]] for r in res)
    assert run["multi"]["placement"] == "fixed"


def test_episode_earliest_finish_uses_both_lanes_for_the_diagnostician():
    """Control for the test above: under earliest finish the diagnostician is
    not pinned, so on the same episode it runs on more than one lane."""
    cfg = yaml.safe_load(Path("configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["placement"] = "earliest_finish"
    run = run_episode(_dev_episode("C02"), cfg, arch="multi")
    lanes = {r["lane"] for r in _results(run) if r["agent"] == "diagnostician"}
    assert lanes == {"npu", "cpu"}
