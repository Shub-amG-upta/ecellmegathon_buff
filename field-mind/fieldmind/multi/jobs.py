"""
=============================================================================
 JOB and RESULT  --  the only messages in the system
 (docs/multi_agent_plan.pdf, "Message formats")
=============================================================================

Job:    scheduler -> lane.   Result: lane -> gate (then the board).

Every Job is stamped with its EVIDENCE TICK -- the tick whose facts went into
the prompt -- and the Result carries it back, so the gate checks citations
against the facts of that tick and can tell a stale answer from a fresh one.

The serialised Result is the existing AgentEnvelope (unchanged, so the
assessment's `envelopes` stay identical to the single agent's) plus the
scheduling fields below, which go to the `multi` telemetry record.
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

PRIORITIES = ("P0", "P1", "P2", "P3", "P4")


@dataclass
class Job:
    job_id: str
    agent: str                      # "diagnostician" | "verifier" | ...
    evidence_tick: int
    priority: int                   # 1..4 (P0 is the hard path, never queued)
    deadline_s: float | None        # relative to the evidence tick; None = no deadline
    max_answer_tokens: int
    submit_s: float                 # simulated clock
    side: str = "all"               # water | heat in Phase 3; one side in Phase 1
    work: Callable[[], Any] | None = field(default=None, repr=False)
    lane: str | None = None
    prompt_tokens: int | None = None        # set when the lane is chosen
    # multi.grammar: the GBNF the server must keep this job's answers to
    # (fieldmind/multi/grammar.py), built with the prompt. None = free text.
    grammar: str | None = field(default=None, repr=False)
    predicted_finish_s: float | None = None
    start_s: float | None = None
    finish_s: float | None = None
    replaced: bool = False
    # Phase 2: what each line number in the prompt stood for (cases, facts,
    # notes), fixed when the prompt was built. The gate maps an answer through
    # THIS, never through the board's state when the answer arrives.
    line_map: dict | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {"job": self.job_id, "agent": self.agent,
                "evidence_tick": self.evidence_tick,
                "priority": self.priority, "deadline_s": self.deadline_s,
                "lane": self.lane, "prompt_tokens": self.prompt_tokens,
                "max_answer_tokens": self.max_answer_tokens}


@dataclass
class Result:
    job: Job
    envelope: Any                   # schemas.AgentEnvelope, untouched
    evidence_tick: int
    lane: str | None
    queue_wait_ms: float            # simulated: start - submit
    start_s: float | None
    finish_s: float | None
    tick_epoch_s: float = 0.0       # simulated time of the evidence tick
    stale: bool = False

    @property
    def deadline_miss(self) -> bool:
        d = self.job.deadline_s
        return (d is not None and self.finish_s is not None
                and self.finish_s - self.tick_epoch_s > d)

    def telemetry(self) -> dict:
        return {"job_id": self.job.job_id, "agent": self.job.agent,
                "evidence_tick": self.evidence_tick,
                "priority": f"P{self.job.priority}", "lane": self.lane,
                "queue_wait_ms": round(self.queue_wait_ms, 3),
                "prompt_tokens": self.job.prompt_tokens,
                "predicted_finish_s": self.job.predicted_finish_s,
                "start_s": self.start_s, "finish_s": self.finish_s,
                "sim_deadline_miss": self.deadline_miss,
                "stale": self.stale}
