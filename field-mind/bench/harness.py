"""
=============================================================================
 HARNESS  --  replays an episode tick by tick  (Plan §3.2)
=============================================================================

The harness holds the clock. At each tick the agent may see:
  - sensor samples up to now (rolling 60-minute window, older data summarised)
  - engineer notes and records timestamped AT OR BEFORE now
  - its own world model from the previous tick
  - NOTHING FROM THE FUTURE -- the harness enforces this, which is the whole
    point of tick-based evaluation.

Why tick-based matters for the result: it makes DETECTION LEAD TIME
measurable -- how many minutes before the trip did the agent first say the
right thing. A one-shot benchmark cannot measure that, and lead time is the
metric a plant engineer actually cares about.

Wall-clock replay is accelerated. The agent is measured on COMPUTE PER TICK,
not on how fast the clock moves.
=============================================================================
"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from fieldmind.agent.l0_ingest import SensorWindow
from fieldmind.agent.l1_checks import CheckLayer
from fieldmind.agent.l3_retrieve import Retriever
from fieldmind.agent.l4_diagnose import Diagnostician
from fieldmind.agent.l5_verify import Verifier
from fieldmind.agent.l6_gate import ActionCatalogue, Gate
from fieldmind.agent.orchestrator import Orchestrator
from fieldmind.multi.lanes import LaneBackend
from fieldmind.multi.orchestrator import MultiOrchestrator, make_lanes
from fieldmind.multi.scheduler import Scheduler
from fieldmind.agent.world_model import new_world_model
from fieldmind.kb.stores import (AssetModel, CaseLibrary, ExperienceStore,
                                 NotesStore)
from fieldmind.runtime.llm_backend import make_backend

EPOCH = datetime(2026, 8, 21, 6, 0, 0)


class Episode:
    """Loads one episode off disk. Nothing here may return future data."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.id = self.path.name
        with open(self.path / "timeseries.csv") as fh:
            self.rows = [{k: float(v) for k, v in r.items()}
                         for r in csv.DictReader(fh)]
        nf = self.path / "notes.jsonl"
        self.notes = [json.loads(l) for l in nf.read_text().splitlines()
                      if l.strip()] if nf.exists() else []
        self.ground_truth = json.loads((self.path / "ground_truth.json").read_text())
        self.records = json.loads((self.path / "records.json").read_text()) \
            if (self.path / "records.json").exists() else {}
        # query.txt: three operator query variants (vague / specific / wrong-
        # premise, notes_gen.build_queries). The agent is handed the SPECIFIC
        # one as the operator's question; the wrong-premise variant is kept for
        # a future T-series sweep. Previously this file was written and never
        # read (known bug 4).
        qf = self.path / "query.txt"
        self.queries = [l.strip() for l in qf.read_text().splitlines()
                        if l.strip()] if qf.exists() else []
        self.operator_query = (self.queries[1] if len(self.queries) >= 2
                               else (self.queries[0] if self.queries else ""))

    def samples_between(self, t0: float, t1: float) -> list[dict]:
        return [r for r in self.rows if t0 <= r["t"] < t1]

    @property
    def duration_s(self) -> float:
        return self.rows[-1]["t"]


def build_agent(cfg: dict, notes: list[dict], records: dict | None = None,
                wrap_backend=None):
    """Wire the pipeline. One place, so a sweep can rebuild it per configuration.

    `records` is the episode's records.json (coal lab reports, maintenance
    history, boiler-water conductivity log). Eight of the thirty episodes list
    `records` as a required modality; without this argument tier C was
    untestable (known bug 3).

    `wrap_backend` (bench tooling, e.g. bench.prompt_log.RecordingBackend) wraps
    the backend before anything is built on it; None leaves it untouched.
    """
    p = cfg["paths"]
    backend = make_backend(cfg["llm"])          # <-- THE MODEL SWITCH
    if wrap_backend is not None:
        backend = wrap_backend(backend)

    asset = AssetModel(Path(p["asset_model"]))
    cases = CaseLibrary(Path(p["case_library"]))
    notes_store = NotesStore(notes)
    experience = ExperienceStore(Path(p["experience"]))
    catalogue = ActionCatalogue(Path(p["action_catalogue"]))

    acfg = cfg["agent"]
    checks = CheckLayer(cfg["checks"])
    retriever = Retriever(asset, cases, notes_store, experience, acfg,
                          records=records or {})
    diag = Diagnostician(backend, acfg)
    ver = Verifier(backend, acfg)
    gate = Gate(catalogue, acfg)
    orch = Orchestrator(checks, retriever, diag, ver, gate, acfg)
    return orch, asset, cases, experience, backend, diag, ver


def build_multi_agent(cfg: dict, notes: list[dict], records: dict | None = None,
                      wrap_backend=None):
    """Same components as bench.harness.build_agent (built BY it, so nothing is
    duplicated), with the model agents' backend replaced by the lane facade.
    `wrap_backend` wraps the facade, so a prompt recorder sees every call in
    the order it is made, whichever lane runs it."""
    box = {}

    def wrap(backend):
        sched = Scheduler(make_lanes(cfg, backend), cfg["multi"])
        box["scheduler"] = sched
        facade = LaneBackend(sched)
        return wrap_backend(facade) if wrap_backend is not None else facade

    orch, asset, cases, experience, backend, diag, ver = build_agent(
        cfg, notes, records, wrap_backend=wrap)
    multi = MultiOrchestrator(orch.checks, orch.retriever, diag, ver, orch.gate,
                              cfg["agent"], cfg["multi"], box["scheduler"])
    return multi, asset, cases, experience, backend, diag, ver


def run_episode(ep: Episode, cfg: dict, ablate_text: bool = False,
                verbose: bool = False, arch: str = "single",
                record_prompts: str | None = None, wrap_backend=None,
                mode: str = "lockstep", tick_s: float | None = None,
                on_assessment=None, on_tick=None) -> dict:
    """Replay one episode. `ablate_text` removes every note -- that is the T8
    modality ablation, and tier B/C accuracy MUST collapse under it or the
    episode is mislabelled.

    `arch`: "single" (fieldmind/agent, the baseline) or "multi"
    (fieldmind/multi, lockstep). `record_prompts`: path of a JSONL file every
    backend call is appended to (bench/prompt_log.py), for the Phase 1
    prompt-identity check. `on_assessment(d)`: called after every tick with that
    tick's published assessment as a dict (the same dict the run file stores),
    in lockstep and real time; for a live UI. Telemetry only: it must not touch
    the agent, and an exception in it stops the run. `wrap_backend`: a bench tool's wrapper put between
    the model and the agent (e.g. bench/verifier_wrong_answer.py), inside the
    prompt recorder; None leaves the backend untouched.

    `on_tick(k)`, if given, is called just before tick k is processed. It is
    telemetry only (the board campaign uses it to stamp each model call with
    its tick) and must not touch the agent."""
    notes = [] if ablate_text else ep.notes
    recorder = None
    wrap = wrap_backend
    if record_prompts:
        from bench.prompt_log import RecordingBackend

        def wrap(b):
            nonlocal recorder
            inner = wrap_backend(b) if wrap_backend is not None else b
            recorder = RecordingBackend(inner, record_prompts)
            recorder.episode = ep.id
            return recorder
    # records.json is a separate modality from notes -- text ablation (T8)
    # removes notes, not the coal lab report / conductivity log.
    if arch == "single":
        orch, asset, cases, experience, backend, diag, ver = build_agent(
            cfg, notes, ep.records, wrap_backend=wrap)
    elif arch == "multi":
        orch, asset, cases, experience, backend, diag, ver = build_multi_agent(
            cfg, notes, ep.records, wrap_backend=wrap)
    else:
        raise ValueError(f"unknown arch {arch!r}")
    orch.operator_query = ep.operator_query

    rt = None
    if mode == "realtime":
        # Phase 4 (demonstration subset): lanes on worker threads; ticks on the
        # wall clock every `wall_tick_s` seconds (default the agent's period);
        # the tick never waits for a model.
        if arch != "multi":
            raise ValueError("real-time mode is multi-agent only")
        from fieldmind.multi.realtime import RealtimeRunner
        rt = RealtimeRunner(orch.scheduler)
        orch.start_realtime(rt)
    elif mode != "lockstep":
        raise ValueError(f"unknown mode {mode!r}")
    wall_tick_s = tick_s if tick_s is not None else cfg["agent"]["tick_period_s"]
    tick_lag_s: list[float] = []

    tick_s = cfg["agent"]["tick_period_s"]
    window = SensorWindow(window_min=60.0,
                          sample_period_s=cfg["checks"]["sample_period_s"])
    wm = new_world_model(ep.id, asset.equipment)
    if arch == "multi":
        orch.bind(wm)                      # the blackboard wraps this world model

    assessments, t0 = [], time.perf_counter()
    n_ticks = int(ep.duration_s // tick_s)

    for k in range(n_ticks):
        t_start, t_end = k * tick_s, (k + 1) * tick_s

        # ---- feed only samples belonging to this tick. The window is the
        #      ONLY channel by which sensor data reaches the agent.
        for row in ep.samples_between(t_start, t_end):
            window.append(row["t"], row)

        if not window.ready(min_minutes=5.0):
            continue                       # still filling; do not guess

        ts = (EPOCH + timedelta(seconds=t_end)).isoformat()
        if on_tick is not None:
            on_tick(k)
        if recorder is not None:
            recorder.tick = k
        if rt is not None:
            # wait for this tick's wall-clock slot (never for a model)
            due = len(tick_lag_s) * wall_tick_s
            delay = due - rt.now()
            if delay > 0:
                time.sleep(delay)
            tick_lag_s.append(round(rt.now() - due, 4))
            asmt = orch.tick_realtime(wm, window, k, ts, now_s=t_end,
                                      tick_wall_s=rt.now())
        else:
            asmt = orch.tick(wm, window, k, ts, now_s=t_end)
        d = asmt.to_dict()
        # Q3_rel (Phase 2): the DETERMINISTIC supports of every case belief
        # holds this tick, read from the world model after the tick. Both
        # arches; nothing in fieldmind/agent/ writes or reads it.
        # Accuracy-fix step 0: belief's live hypotheses in INSERTION order, so an
        # offline replay (bench/replay_multi.py) breaks confidence ties exactly as
        # rank_hypotheses does. Both arches; nothing in fieldmind/ reads it.
        d["belief_order"] = ([] if asmt.triage == "QUIET" else
                             [h.case_ref for h in wm.hypotheses if not h.retired])
        d["belief_supports"] = ({} if asmt.triage == "QUIET" else
                                {h.case_ref: list(h.supports)
                                 for h in wm.hypotheses if h.case_ref})
        if arch == "multi":
            d["multi"] = orch.last_telemetry   # jobs, lanes, P0 time; not compared
        assessments.append(d)
        if on_assessment is not None:          # live UI hook (telemetry only)
            on_assessment(d)

        if verbose and (asmt.triage != "QUIET" or k % 40 == 0):
            top = asmt.hypotheses[0]["cause"][:46] if asmt.hypotheses else "-"
            print(f"  t{k:4d} {asmt.state:13s} {asmt.triage:11s} "
                  f"{asmt.headline[:58]:58s} | {top}")

    rt_end = None
    if rt is not None:
        # let the background lanes finish what is queued (answers after the last
        # tick are counted, not folded), then stop the worker threads
        idle = rt.wait_idle(timeout_s=max(10 * wall_tick_s, 60.0))
        late = rt.drain()
        rt.close()
        rt_end = {"idle_at_end": idle, "answers_after_last_tick": len(late),
                  "wall_tick_s": wall_tick_s, "tick_lag_s": tick_lag_s,
                  "tick_lag_s_max": max(tick_lag_s) if tick_lag_s else None}
    wall = time.perf_counter() - t0
    if recorder is not None:
        recorder.close()
    envs = [e for a in assessments for e in a.get("envelopes", [])]
    ptoks = [e.get("prompt_tokens", 0) for e in envs if e.get("prompt_tokens")]
    env_status = {}
    for e in envs:
        env_status[e["status"]] = env_status.get(e["status"], 0) + 1
    out = {
        "episode_id": ep.id,
        "backend": cfg["llm"]["backend"],
        "n_ticks": len(assessments),
        "assessments": assessments,
        "ground_truth": ep.ground_truth,
        "wall_clock_s": round(wall, 2),
        "llm_invocation_rate": round(
            sum(a["llm_invoked"] for a in assessments) / max(1, len(assessments)), 3),
        "parse_failure_rate": round(
            diag.parse_failures / max(1, diag.calls), 3),
        "verifier_disagreement_rate": round(
            ver.disagreements / max(1, ver.calls), 3),
        "deadline_miss_rate": round(
            sum(a["deadline_miss"] for a in assessments) / max(1, len(assessments)), 4),
        "diag_calls": diag.calls,
        "ver_calls": ver.calls,
        "llm_retries": getattr(diag, "retries", 0) + getattr(ver, "retries", 0),
        "mean_prompt_tokens": round(sum(ptoks) / len(ptoks), 1) if ptoks else 0,
        "envelope_status_counts": env_status,
        "ablate_text": ablate_text,
    }
    if arch == "multi":
        out["multi"] = orch.run_telemetry()
        if rt_end is not None:
            out["multi"]["realtime"].update(rt_end)
            out["mode"] = "realtime"
        if orch.text is not None:           # key present only when the reader is on
            out["text_calls"] = out["multi"]["text_calls"]
    return out
