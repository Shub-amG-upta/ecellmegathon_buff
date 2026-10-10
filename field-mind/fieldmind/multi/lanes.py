"""
=============================================================================
 LANES  --  the NPU and CPU model lanes, on a simulated clock
 (docs/multi_agent_plan.pdf, "Scheduler", "Three lanes")
=============================================================================

A lane runs ONE model call at a time. Each lane wraps a backend (in Phase 1
both lanes share the same backend object on mock; with llamaserver each lane
gets its own URL -- same GGUF on both, plan rule 3).

The simulated clock is what makes lane choice and queueing testable without
hardware: a job starts at max(submit, lane free) and occupies the lane for

    prompt_tokens / prefill_tok_s + answer_tokens / decode_tok_s

seconds (the plan's t_call without the overhead term). Prompt and answer
token counts come from the backend reply when it reports them, else from the
prompt-size estimate. The rates are configured (multi.lanes), not measured,
in Phase 1; on mock they drive telemetry only, never a decision.

LaneBackend is an LLMBackend facade. The Diagnostician and Verifier are built
on it unmodified; each generate() goes to the lane the scheduler chose for the
current job, which is chosen on the job's FIRST call (when the prompt exists),
and a repair call stays on the same lane.
=============================================================================
"""

from __future__ import annotations

from ..agent.l4_diagnose import est_tokens
from ..runtime.llm_backend import LLMBackend


class LaneBusy(AssertionError):
    """A second job was started on a lane that is still running one."""


class SimLane:
    def __init__(self, name: str, backend, prefill_tok_s: float,
                 decode_tok_s: float):
        if prefill_tok_s <= 0 or decode_tok_s <= 0:
            raise ValueError(f"lane {name}: rates must be positive")
        self.name = name
        self.backend = backend
        self.prefill_tok_s = float(prefill_tok_s)
        self.decode_tok_s = float(decode_tok_s)
        self.free_at = 0.0          # simulated seconds
        self.running = None         # the Job in flight, if any
        self.busy_s = 0.0
        self.n_jobs = 0
        self._elapsed = 0.0

    # ------------------------------------------------------------------
    def service_s(self, prompt_tokens: int, answer_tokens: int) -> float:
        return (prompt_tokens / self.prefill_tok_s
                + answer_tokens / self.decode_tok_s)

    def predict_finish(self, now_s: float, prompt_tokens: int,
                       max_answer_tokens: int) -> float:
        """t_finish = t_lane_free + prompt/prefill + max_answer/decode (plan p.10)."""
        return max(now_s, self.free_at) + self.service_s(prompt_tokens,
                                                         max_answer_tokens)

    # ------------------------------------------------------------------
    def begin(self, job) -> None:
        if self.running is not None:
            raise LaneBusy(f"lane {self.name} is running {self.running.job_id}; "
                           f"cannot start {job.job_id}")
        self.running = job
        job.start_s = max(job.submit_s, self.free_at)
        self._elapsed = 0.0

    def generate(self, prompt, role, max_tokens, mock_hint, grammar=None):
        if self.running is None:
            raise LaneBusy(f"lane {self.name}: generate() outside a job")
        # multi.grammar: only a backend that can enforce a grammar is handed one
        # (llama-server); for any other backend the call is exactly as before
        extra = ({"grammar": grammar} if grammar is not None
                 and getattr(self.backend, "supports_grammar", False) else {})
        reply = self.backend.generate(prompt, role=role, max_tokens=max_tokens,
                                      mock_hint=mock_hint, **extra)
        ptok = reply.prefill_tokens if reply.prefill_tokens is not None \
            else est_tokens(prompt)
        # A server stops at max_tokens; the mock backend ignores the cap and
        # reports len(text)//4 (~960 for a diagnosis), which no real lane could
        # decode. The simulated clock uses what a lane could actually emit.
        dtok = min(reply.decode_tokens, max_tokens) \
            if reply.decode_tokens is not None else max_tokens
        self._elapsed += self.service_s(ptok, dtok)
        return reply

    def end(self, job) -> None:
        if self.running is not job:
            raise LaneBusy(f"lane {self.name}: end() for a job it is not running")
        job.finish_s = job.start_s + self._elapsed
        self.free_at = job.finish_s
        self.busy_s += self._elapsed
        self.n_jobs += 1
        self.running = None

    def telemetry(self) -> dict:
        return {"lane": self.name, "prefill_tok_s": self.prefill_tok_s,
                "decode_tok_s": self.decode_tok_s, "n_jobs": self.n_jobs,
                "busy_s": round(self.busy_s, 3), "free_at": round(self.free_at, 3)}


class LaneBackend(LLMBackend):
    """What the model agents see as their backend. Routes each call to the lane
    of the scheduler's current job."""

    name = "lanes"

    def __init__(self, scheduler):
        self.scheduler = scheduler

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        lane = self.scheduler.lane_for_current(prompt)
        # the grammar travels on the job, so a repair call of the same job is
        # held to it too and bench wrappers around this facade need no change
        grammar = getattr(self.scheduler.current, "grammar", None)
        return lane.generate(prompt, role, max_tokens, mock_hint, grammar=grammar)

    def close(self) -> None:
        seen = set()
        for lane in self.scheduler.lanes:
            if id(lane.backend) not in seen:
                seen.add(id(lane.backend))
                lane.backend.close()
