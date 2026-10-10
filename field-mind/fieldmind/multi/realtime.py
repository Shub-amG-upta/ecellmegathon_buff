"""
=============================================================================
 REAL-TIME LANES  (multi-agent Phase 4, demonstration subset;
 reports/multi_phase4_realtime.md)
=============================================================================

One worker thread per lane. Each lane runs ONE model call at a time from its
own queue; the tick loop only submits jobs and collects whatever has finished,
so the tick never waits for a model (plan rule 1). In the two-model setup the
NPU lane (Llama 3B) runs the side diagnosticians and the CPU lane (Gemma 1B)
the verifier and text reader, at the same time.

  submit(job)  place the job on its lane (multi.fixed_placement; real time
               uses fixed placement only) and queue it. A newer job for the
               same (agent, side) replaces one still WAITING (plan: "at most
               one waiting job per (agent, side)"); a running call finishes.
  drain()      the Results that finished since the last drain (non-blocking).

Timings in Results are WALL-CLOCK seconds since the runner started (real,
laptop or board), not the simulated clock of lockstep mode. The lane's
SimLane object is still used to route the call (LaneBackend) and to record the
lane's planning estimate.

Stdlib only.
=============================================================================
"""

from __future__ import annotations

import queue
import threading
import time

from ..schemas import AgentEnvelope
from .jobs import Result


class RealtimeRunner:
    def __init__(self, scheduler, clock=time.monotonic):
        if scheduler.placement != "fixed":
            raise ValueError("real-time mode uses fixed placement (multi.placement: fixed)")
        self.s = scheduler
        self.clock = clock
        self.t0 = clock()
        self.lock = threading.Lock()
        self.done: queue.Queue = queue.Queue()
        self.queues = {l.name: queue.Queue() for l in scheduler.lanes}
        self.waiting: dict[tuple, object] = {}          # (agent, side) -> job not started
        self.running: dict[str, object] = {l.name: None for l in scheduler.lanes}
        self.spans: list[dict] = []                     # one per finished call
        self.replaced: list[str] = []
        self.threads = [threading.Thread(target=self._worker, args=(l,), daemon=True,
                                         name=f"lane-{l.name}") for l in scheduler.lanes]
        for t in self.threads:
            t.start()

    def now(self) -> float:
        return self.clock() - self.t0

    # ------------------------------------------------------------------
    def submit(self, job, tick_wall_s: float | None = None) -> None:
        lane = self.s.fixed_placement.get(job.agent)
        if lane is None:
            raise KeyError(f"agent {job.agent!r} has no lane in multi.fixed_placement")
        job.lane = lane
        job.wall_submit = self.now()
        job.wall_tick = self.now() if tick_wall_s is None else tick_wall_s
        with self.lock:
            old = self.waiting.get((job.agent, job.side))
            if old is not None:
                old.replaced = True
                self.replaced.append(old.job_id)
            self.waiting[(job.agent, job.side)] = job
        self.queues[lane].put(job)

    def in_flight(self, agent: str, side: str) -> list:
        """Jobs of (agent, side) waiting or running now."""
        with self.lock:
            out = [j for j in self.running.values()
                   if j is not None and (j.agent, j.side) == (agent, side)]
            w = self.waiting.get((agent, side))
            return out + ([w] if w is not None else [])

    def drain(self) -> list[Result]:
        out = []
        while True:
            try:
                out.append(self.done.get_nowait())
            except queue.Empty:
                return out

    def idle(self) -> bool:
        with self.lock:
            return (not self.waiting and all(j is None for j in self.running.values())
                    and all(q.empty() for q in self.queues.values()))

    def wait_idle(self, timeout_s: float) -> bool:
        end = self.clock() + timeout_s
        while self.clock() < end:
            if self.idle():
                return True
            time.sleep(0.01)
        return self.idle()

    def close(self) -> None:
        for q in self.queues.values():
            q.put(None)
        for t in self.threads:
            t.join(timeout=5)

    # ------------------------------------------------------------------
    def _worker(self, lane) -> None:
        while True:
            job = self.queues[lane.name].get()
            if job is None:
                return
            with self.lock:
                if job.replaced:
                    continue
                if self.waiting.get((job.agent, job.side)) is job:
                    del self.waiting[(job.agent, job.side)]
                self.running[lane.name] = job
            start = self.now()
            self.s.current = job                     # this thread's job (LaneBackend)
            try:
                lane.begin(job)
                env = job.work()
            except Exception as e:                   # the tick never fails
                env = AgentEnvelope(agent=job.agent, tick=job.evidence_tick,
                                    status="error", error=repr(e),
                                    payload={"error": repr(e)})
            finally:
                if lane.running is job:
                    lane.end(job)
                self.s.current = None
            end = self.now()
            job.start_s, job.finish_s = start, end   # wall clock, not simulated
            with self.lock:
                self.running[lane.name] = None
                self.spans.append({"lane": lane.name, "job": job.job_id,
                                   "agent": job.agent, "submit_s": round(job.wall_submit, 4),
                                   "start_s": round(start, 4), "end_s": round(end, 4)})
            self.done.put(Result(job=job, envelope=env, evidence_tick=job.evidence_tick,
                                 lane=lane.name,
                                 queue_wait_ms=(start - job.wall_submit) * 1000,
                                 start_s=start, finish_s=end,
                                 tick_epoch_s=job.wall_tick))

    def telemetry(self) -> dict:
        with self.lock:
            spans = list(self.spans)
        by = {}
        for sp in spans:
            by.setdefault(sp["lane"], []).append(sp)
        overlap = 0.0                                # seconds both lanes were busy
        if len(by) == 2:
            a, b = by.values()
            for x in a:
                for y in b:
                    overlap += max(0.0, min(x["end_s"], y["end_s"]) - max(x["start_s"], y["start_s"]))
        return {"calls": {k: len(v) for k, v in by.items()},
                "busy_s": {k: round(sum(s["end_s"] - s["start_s"] for s in v), 3)
                           for k, v in by.items()},
                "both_lanes_busy_s": round(overlap, 3),
                "replaced_waiting_jobs": len(self.replaced),
                "workers_alive": {t.name: t.is_alive() for t in self.threads},
                "spans": spans}
