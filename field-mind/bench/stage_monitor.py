#!/usr/bin/env python3
"""
=============================================================================
 STAGE MONITOR  --  live per-stage view of one episode, plus a timing report
=============================================================================

  python bench/stage_monitor.py --episode ep_A02_fcv_seize_fast
  python bench/stage_monitor.py --episode ep_A02_fcv_seize_fast --backend mock
  python bench/stage_monitor.py --episode ep_A02_fcv_seize_fast --max-ticks 15

Replays one episode through the UNMODIFIED agent and shows, live:

  * every tick, which stage it is in, and how long each finished stage took;
  * what the CPU and the LLM unit (NPU on geniex/genie) are doing right now --
    which stage of which tick.

When the run ends (or on Ctrl-C) it writes a text report with the running
time of every stage of every tick, every LLM call, and per-stage statistics.

HOW IT MEASURES.  Nothing in fieldmind/ or bench/harness.py is edited. The
script wraps the real components from outside (the CheckLayer, Retriever,
Diagnostician, Verifier, Gate, the world-model functions, the backend's
generate()) with a perf_counter timer, then calls harness.run_episode exactly
as run_demo.py does. So the timings are of the real code path.

WHAT "CPU" MEANS HERE.  The orchestrator is Python on whatever machine runs
this script. With `mode: adb` (the config default) that is the HOST, not the
board's Kryo cores: L0-L3, L6 and the world-model updates run on the laptop
and only the LLM call runs on the board. The CPU lane is labelled with the
hostname and architecture so this cannot be misread.

WHAT THE LLM WALL TIME INCLUDES.  On adb backends the wall time of a call is
adb push + process spawn + model load + prefill + decode + adb return. The
TTFT and tok/s columns in the report are runtime-reported and exclude that
overhead. Both are kept, never mixed.

TODAY'S POLICY IS SEQUENTIAL.  The orchestrator is one thread and every LLM
call blocks it, so the CPU lane reads "waiting" whenever the LLM lane is busy.
That is the measured baseline for the pipelined design, not a display bug.
=============================================================================
"""

from __future__ import annotations

import argparse
import contextlib
import io
import math
import os
import platform
import shutil
import socket
import statistics
import sys
import threading
import time
import traceback
from datetime import timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench import harness                                  # noqa: E402
from bench.harness import Episode                          # noqa: E402
from fieldmind.agent import orchestrator as orch_mod       # noqa: E402
from fieldmind.agent import world_model as wm_mod          # noqa: E402

# Stage columns, in pipeline order. WM is the world-model bookkeeping the
# orchestrator calls inline between L1 and L4 (update_findings/baselines/
# hypotheses, rank, derive_state); L7 itself is two assignments and lands in
# "other" with the rest of the orchestrator's own code.
STAGES = ["L0", "L1", "WM", "L2", "L3", "L4", "L5", "L6"]
STAGE_NAME = {"L0": "ingest", "L1": "checks", "WM": "world model",
              "L2": "triage", "L3": "retrieve", "L4": "diagnose",
              "L5": "verify", "L6": "gate"}
LLM_STAGES = ("L4", "L5")
WM_FUNCS = ("update_findings", "update_baselines", "update_hypotheses",
            "rank_hypotheses", "derive_state")


class StopEarly(Exception):
    """Raised inside the replay to honour --max-ticks."""


# ===========================================================================
#  Measurement
# ===========================================================================
class TickRec:
    def __init__(self, k: int, plant: str):
        self.k = k
        self.plant = plant
        self.filling = False          # window not ready -> no assessment
        self.done = False
        self.state = ""
        self.triage = ""
        self.ms: dict[str, float] = {}      # stage -> wall ms (L4/L5 include the LLM wait)
        self.llm_ms: dict[str, float] = {}  # stage -> ms inside backend.generate
        self.running: str | None = None
        self.run_t0 = 0.0
        self.t_first = None           # first perf_counter seen for this tick
        self.t_last = None
        self.wrap_ms = None           # orch.tick() wall, measured by this script
        self.orch_ms = None           # Assessment.tick_latency_ms, the orchestrator's own clock
        self.deadline_miss = False
        self.llm_invoked = False

    @property
    def total_ms(self) -> float:
        return sum(self.ms.values()) + self.other_ms

    @property
    def other_ms(self) -> float:
        """Orchestrator code outside every wrapped call (assembly, L7, headline)."""
        if self.wrap_ms is None:
            return 0.0
        inner = sum(v for s, v in self.ms.items() if s != "L0")
        return max(0.0, self.wrap_ms - inner)


class Monitor:
    def __init__(self, tick_s: float, n_ticks: int, llm_label: str,
                 max_ticks: int | None, plain_out=None):
        self.lock = threading.RLock()
        self.tick_s = tick_s
        self.n_ticks = n_ticks
        self.llm_label = llm_label
        self.max_ticks = max_ticks
        self.plain_out = plain_out    # file to print events to in --plain mode
        self.ticks: list[TickRec] = []
        self.stack: list[str] = []
        self.calls: list[dict] = []
        self.llm_active: dict | None = None
        self.assessed = 0
        self.t0 = time.perf_counter()
        self.t_end: float | None = None

    # ---------------- tick bookkeeping ----------------------------------
    def _open_tick(self) -> TickRec:
        if self.ticks and not self.ticks[-1].done:
            return self.ticks[-1]
        k = len(self.ticks)
        plant = (harness.EPOCH + timedelta(seconds=(k + 1) * self.tick_s)
                 ).isoformat()[11:19]
        rec = TickRec(k, plant)
        self.ticks.append(rec)
        return rec

    def _touch(self, rec: TickRec, t: float) -> None:
        rec.t_first = t if rec.t_first is None else rec.t_first
        rec.t_last = t

    def window_ready(self, ready: bool) -> None:
        with self.lock:
            rec = self._open_tick()
            if not ready:
                rec.filling = True
                rec.done = True
                self._touch(rec, time.perf_counter())

    # ---------------- stage timing --------------------------------------
    def stage(self, name: str, fn):
        """Wrap fn as stage `name`. A call nested inside another timed stage is
        attributed to the OUTER stage, so no time is counted twice."""
        def wrapped(*a, **kw):
            with self.lock:
                if self.stack:
                    nested = True
                else:
                    nested = False
                    rec = self._open_tick()
                    t0 = time.perf_counter()
                    self.stack.append(name)
                    rec.running, rec.run_t0 = name, t0
                    self._touch(rec, t0)
            if nested:
                return fn(*a, **kw)
            try:
                return fn(*a, **kw)
            finally:
                t1 = time.perf_counter()
                with self.lock:
                    self.stack.pop()
                    rec.ms[name] = rec.ms.get(name, 0.0) + (t1 - t0) * 1000
                    rec.running = None
                    self._touch(rec, t1)
        return wrapped

    def wrap_tick(self, fn):
        def wrapped(wm, window, tick_no, timestamp, now_s):
            with self.lock:
                rec = self._open_tick()
                rec.plant = timestamp[11:19]
            t0 = time.perf_counter()
            asmt = fn(wm, window, tick_no, timestamp, now_s)
            t1 = time.perf_counter()
            with self.lock:
                rec.wrap_ms = (t1 - t0) * 1000
                rec.orch_ms = asmt.tick_latency_ms
                rec.state, rec.triage = asmt.state, asmt.triage
                rec.deadline_miss = bool(asmt.deadline_miss)
                rec.llm_invoked = bool(asmt.llm_invoked)
                rec.done = True
                self._touch(rec, t1)
                self.assessed += 1
                self._plain_tick(rec)
                stop = self.max_ticks is not None and self.assessed >= self.max_ticks
            if stop:
                raise StopEarly(f"--max-ticks {self.max_ticks} reached")
            return asmt
        return wrapped

    def wrap_llm(self, fn):
        def wrapped(*a, **kw):
            role = kw.get("role", a[1] if len(a) > 1 else "generic")
            with self.lock:
                rec = self._open_tick()
                stage = self.stack[-1] if self.stack else "?"
                attempt = 1 + sum(1 for c in self.calls
                                  if c["tick"] == rec.k and c["role"] == role)
                call = {"n": len(self.calls) + 1, "tick": rec.k, "role": role,
                        "stage": stage, "attempt": attempt,
                        "t0": time.perf_counter(), "status": "running"}
                self.llm_active = call
                self._plain_call_start(call)
            reply = None
            try:
                reply = fn(*a, **kw)
                return reply
            finally:
                t1 = time.perf_counter()
                with self.lock:
                    call["wall_ms"] = (t1 - call["t0"]) * 1000
                    if reply is None:
                        call["status"] = "exception"
                    else:
                        call["status"] = reply.status
                        call["device"] = reply.backend
                        call["model"] = reply.model
                        call["backend_ms"] = reply.latency_ms
                        call["ttft_ms"] = reply.ttft_ms
                        call["prefill"] = reply.prefill_tokens
                        call["decode"] = reply.decode_tokens
                        # this repo's LLMReply carries tokens and server times, not
                        # rates: rate = tokens / server ms. Unknown stays 0 for display.
                        dms = getattr(reply, "decode_ms", None)
                        call["prefill_tps"] = (reply.prefill_tokens / (reply.ttft_ms / 1000)
                                               if reply.prefill_tokens and reply.ttft_ms else 0.0)
                        call["decode_tps"] = (reply.decode_tokens / (dms / 1000)
                                              if reply.decode_tokens and dms else 0.0)
                        call["ttft_ms"] = reply.ttft_ms or 0.0
                        call["prefill"] = reply.prefill_tokens or 0
                        call["decode"] = reply.decode_tokens or 0
                        call["error"] = (reply.error or "")[:120]
                    rec.llm_ms[stage] = rec.llm_ms.get(stage, 0.0) + call["wall_ms"]
                    self.calls.append(call)
                    self.llm_active = None
                    self._plain_call_end(call)
        return wrapped

    # ---------------- --plain event lines ---------------------------------
    def _plain(self, line: str) -> None:
        if self.plain_out is not None:
            print(line, file=self.plain_out, flush=True)

    def _plain_call_start(self, c: dict) -> None:
        self._plain(f"  [{_clock(time.perf_counter() - self.t0)}] tick {c['tick'] + 1:>4} "
                    f"{c['stage']} {c['role']:<13} -> {self.llm_label} ...")

    def _plain_call_end(self, c: dict) -> None:
        self._plain(f"  [{_clock(time.perf_counter() - self.t0)}] tick {c['tick'] + 1:>4} "
                    f"{c['stage']} {c['role']:<13} <- {c.get('device', '?')} "
                    f"{c['status']} {c['wall_ms']:.0f} ms")

    def _plain_tick(self, rec: TickRec) -> None:
        cells = " ".join(f"{s}={_fmt(rec.ms[s])}" for s in STAGES if s in rec.ms)
        self._plain(f"tick {rec.k + 1:>4}/{self.n_ticks} {rec.plant} {rec.state:<12} "
                    f"{rec.triage:<11} {cells} total={_fmt(rec.total_ms)}"
                    f"{'  DEADLINE MISS' if rec.deadline_miss else ''}")


# ===========================================================================
#  Instrumentation -- wraps the real components; edits no source file
# ===========================================================================
def install(mon: Monitor):
    """Patch module-level names that run_episode / Orchestrator look up at call
    time. Returns an undo function."""
    saved = {}

    def patch(obj, attr, new):
        saved[(obj, attr)] = getattr(obj, attr)
        setattr(obj, attr, new)

    orig_build = harness.build_agent
    orig_window = harness.SensorWindow

    class TimedWindow(orig_window):
        def append(self, *a, **kw):
            return mon.stage("L0", super().append)(*a, **kw)

        def ready(self, *a, **kw):
            r = super().ready(*a, **kw)
            mon.window_ready(r)
            return r

    def build_agent(*a, **kw):
        out = orig_build(*a, **kw)
        orch, _asset, _cases, _exp, backend, diag, ver = out
        orch.checks.run = mon.stage("L1", orch.checks.run)
        orch.retriever.retrieve = mon.stage("L3", orch.retriever.retrieve)
        orch._slopes = mon.stage("L3", orch._slopes)
        diag.run = mon.stage("L4", diag.run)
        ver.run = mon.stage("L5", ver.run)
        ver.apply = mon.stage("L5", ver.apply)
        orch.gate.check_citations = mon.stage("L6", orch.gate.check_citations)
        orch.gate.approve = mon.stage("L6", orch.gate.approve)
        orch.tick = mon.wrap_tick(orch.tick)
        # Diagnostician and Verifier share one backend object today; wrap each
        # distinct object once so a call is never timed twice.
        for b in {id(diag.backend): diag.backend, id(ver.backend): ver.backend}.values():
            b.generate = mon.wrap_llm(b.generate)
        return out

    patch(harness, "build_agent", build_agent)
    patch(harness, "SensorWindow", TimedWindow)
    patch(orch_mod, "triage", mon.stage("L2", orch_mod.triage))
    patch(orch_mod, "build_signature", mon.stage("L3", orch_mod.build_signature))
    patch(orch_mod, "evidence_packet", mon.stage("L4", orch_mod.evidence_packet))
    for fn in WM_FUNCS:
        patch(wm_mod, fn, mon.stage("WM", getattr(wm_mod, fn)))

    def undo():
        for (obj, attr), v in saved.items():
            setattr(obj, attr, v)
    return undo


def llm_unit(cfg: dict) -> tuple[str, str]:
    """(short label, lane description) for the configured LLM backend. This is
    what the config ASKS for; every call also records what the runtime
    REPORTS, and the report flags any disagreement."""
    name = cfg["llm"]["backend"]
    sub = cfg["llm"].get(name, {}) or {}
    where = "board via adb" if sub.get("mode", "adb") == "adb" else "board"
    if name == "geniex":
        from fieldmind.runtime.llm_backend import GenieXBackend
        dev = sub.get("device", "npu")
        label = GenieXBackend._DEVICE_LABEL.get(dev, dev)
        return label, f"{where}, geniex device={dev}"
    if name == "genie":
        return "npu", f"{where}, genie (QNN HTP)"
    if name == "litert":
        return sub.get("accelerator", "cpu"), f"{where}, litert"
    if name == "gemini":
        return "cloud", "Google API"
    if name == "llamaserver":
        # persistent llama-server lane on the QIDK, reached through adb forward
        return sub.get("lane", "npu"), f"board, llama-server at {sub.get('url', '?')}"
    return "mock", "in-process mock"


# ===========================================================================
#  Formatting
# ===========================================================================
def _fmt(ms: float) -> str:
    if ms < 1:
        return f"{ms:.2f}"
    if ms < 100:
        return f"{ms:.1f}"
    if ms < 10_000:
        return f"{ms:.0f}"
    return f"{ms / 1000:.1f}s"


def _clock(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)]


ANSI = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
        "yellow": "\033[33m", "green": "\033[32m", "magenta": "\033[35m",
        "red": "\033[31m", "cyan": "\033[36m"}


def _c(text: str, *styles: str) -> str:
    return "".join(ANSI[s] for s in styles) + text + ANSI["reset"]


TICK_HDR = (f"{'tick':>5}  {'plant':<8}  {'state':<12} {'triage':<11} "
            + " ".join(f"{s:>7}" for s in STAGES) + f" {'total':>8}")


def _cell(rec: TickRec, s: str, now: float) -> str:
    if rec.running == s:
        return "▶" + _fmt((now - rec.run_t0) * 1000)
    if s in rec.ms:
        return _fmt(rec.ms[s])
    return "—" if rec.done else "·"


def render(mon: Monitor, meta: dict) -> list[str]:
    now = time.perf_counter()
    cols, rows = shutil.get_terminal_size((120, 40))
    with mon.lock:
        out = []
        cur = mon.ticks[-1] if mon.ticks else None
        done = sum(1 for t in mon.ticks if t.done)
        out.append(_c(f"FieldMind stage monitor   {meta['episode']}   "
                      f"backend {meta['backend']}   elapsed {_clock(now - mon.t0)}",
                      "bold"))
        out.append(f"policy {meta['policy']} · verifier {meta['verifier']} · "
                   f"tick period {mon.tick_s:g} s · ticks {done}/{mon.n_ticks} · "
                   f"LLM calls {len(mon.calls)}"
                   + (f" · stop after {mon.max_ticks} assessed" if mon.max_ticks else ""))
        if meta["backend"] == "mock":
            out.append(_c("mock backend: these timings are plumbing only, "
                          "NOT agent results", "yellow"))
        out.append("")

        # ---- the two compute units -------------------------------------
        llm = mon.llm_active
        host_now = "idle"
        host_style = ("dim",)
        if llm is not None:
            host_now = (f"waiting for {mon.llm_label.upper()}  "
                        f"(tick {llm['tick'] + 1} {llm['stage']} {STAGE_NAME.get(llm['stage'], '')})")
        elif mon.stack and cur is not None:
            s = mon.stack[0]
            host_now = (f"tick {cur.k + 1:<4} {s} {STAGE_NAME[s]:<12} "
                        f"{_fmt((now - cur.run_t0) * 1000)} ms")
            host_style = ("green", "bold")
        if llm is not None:
            llm_now = (f"tick {llm['tick'] + 1:<4} {llm['stage']} {llm['role']:<13} "
                       f"call #{len(mon.calls) + 1}  {now - llm['t0']:.1f} s")
            llm_style = ("magenta", "bold")
        else:
            llm_now, llm_style = "idle", ("dim",)
        out.append(f" {'UNIT':<44} NOW")
        out.append(f" {('CPU   ' + meta['host'])[:44]:<44} " + _c(host_now, *host_style))
        out.append(f" {(mon.llm_label.upper() + '   ' + meta['llm_where'])[:44]:<44} "
                   + _c(llm_now, *llm_style))
        if mon.calls:
            c = mon.calls[-1]
            flag = "" if c.get("device") == mon.llm_label else "  [!] reported device differs from config"
            out.append(_c(f" last call #{c['n']} tick {c['tick'] + 1} {c['role']} on "
                          f"{c.get('device', '?')}: {c['status']} wall {c['wall_ms']:.0f} ms, "
                          f"ttft {c.get('ttft_ms', 0):.0f} ms, pre {c.get('prefill', 0)} / "
                          f"dec {c.get('decode', 0)} tok{flag}", "dim"))
        out.append("")

        # ---- tick list ---------------------------------------------------
        out.append(_c(TICK_HDR, "bold"))
        body: list[tuple[str, tuple]] = []
        i = 0
        while i < len(mon.ticks):
            t = mon.ticks[i]
            if t.filling:
                j = i
                while j + 1 < len(mon.ticks) and mon.ticks[j + 1].filling:
                    j += 1
                body.append((f"{t.k + 1:>5}" + (f"-{mon.ticks[j].k + 1}" if j > i else "")
                             + "  window filling -- first 5 min of plant time, "
                               "no assessment", ("dim",)))
                i = j + 1
                continue
            cells = " ".join(f"{_cell(t, s, now):>7}" for s in STAGES)
            total = _fmt(t.total_ms) if t.done else _fmt((now - (t.t_first or now)) * 1000)
            line = (f"{t.k + 1:>5}  {t.plant:<8}  {t.state or '…':<12} "
                    f"{t.triage or '…':<11} {cells} {total:>8}"
                    + (" !" if t.deadline_miss else ""))
            style = (("yellow", "bold") if not t.done
                     else ("red",) if t.deadline_miss else ())
            body.append((line, style))
            i += 1
        room = max(3, rows - len(out) - 3)
        if len(body) > room:
            hidden = len(body) - room + 1
            body = [(f"  … {hidden} earlier rows (all in the report)", ("dim",))] \
                + body[-(room - 1):]
        for line, style in body:
            line = line[:cols - 1]
            out.append(_c(line, *style) if style else line)
        out.append(_c("cells: ms (s suffix = seconds) · ▶ running · · not reached · "
                      "— skipped this tick · ! deadline miss · Ctrl-C stops and "
                      "writes the report", "dim"))
        return [l if "\033" in l else l[:cols - 1] for l in out]


# ===========================================================================
#  Report
# ===========================================================================
def write_report(path: Path, mon: Monitor, meta: dict, outcome: str,
                 error: str = "", run: dict | None = None) -> None:
    with mon.lock:
        ticks = [t for t in mon.ticks if t.done and not t.filling]
        filling = [t for t in mon.ticks if t.filling]
        calls = list(mon.calls)
        wall_s = ((mon.t_end or time.perf_counter()) - mon.t0)

    L = []
    w = L.append
    w("FieldMind stage timing report")
    w("=" * 78)
    w(f"outcome            {outcome}")
    w(f"episode            {meta['episode']}  ({meta['family']})")
    w(f"backend            {meta['backend']}   model {meta['model']}")
    w(f"LLM unit (config)  {mon.llm_label}  -- {meta['llm_where']}")
    reported = sorted({c.get('device', '?') for c in calls})
    w(f"LLM unit (runtime) {', '.join(reported) if reported else 'no calls'}"
      + ("" if not reported or reported == [mon.llm_label]
         else "   [!] DIFFERS FROM CONFIG"))
    w(f"CPU stages ran on  {meta['host']}")
    w(f"policy             {meta['policy']}   verifier {meta['verifier']}   "
      f"tick period {mon.tick_s:g} s")
    w(f"started            {meta['started']}")
    w(f"wall clock         {wall_s:.2f} s")
    w(f"ticks              {len(ticks)} assessed, {len(filling)} window-filling, "
      f"{mon.n_ticks} in episode")
    if meta.get("log"):
        w(f"captured stdout    {meta['log']}")
    if meta["backend"] == "mock":
        w("")
        w("NOTE: mock backend. These timings measure plumbing and the deterministic")
        w("      layers only. They are NOT agent results and must not be reported as such.")
    if error:
        w("")
        w("ERROR")
        w(error.rstrip())

    w("")
    w("LEGEND")
    w("  L0 ingest       harness feeding this tick's samples into the window")
    w("  L1 checks       deterministic checks -> Facts")
    w("  WM world model  update_findings/baselines/hypotheses, rank, derive_state")
    w("  L2 triage       decides whether the LLM stages run")
    w("  L3 retrieve     slopes + signature + case/note retrieval")
    w("  L4 diagnose     evidence packet + Diagnostician (prompt build, LLM call(s), parse)")
    w("  L5 verify       Verifier run + apply (prompt build, LLM call, parse)")
    w("  L6 gate         citation check + action approval")
    w("  other           orchestrator code outside the above (assembly, L7 write, headline)")
    w("  llm             part of L4/L5 spent inside backend.generate (wall, incl. adb on device)")
    w("  All values in milliseconds. '-' = stage did not run this tick.")
    w("  Policy is sequential: the CPU thread blocks for every LLM call, so the CPU and")
    w("  the LLM unit are never busy at the same time (by construction, single thread).")

    # ---- per tick ----------------------------------------------------------
    w("")
    w("PER TICK")
    hdr = (f"{'tick':>5} {'plant':<8} {'state':<12} {'triage':<11}"
           + "".join(f"{s:>9}" for s in STAGES)
           + f"{'L4 llm':>10}{'L5 llm':>10}{'other':>9}{'total':>11}  miss")
    w(hdr)
    w("-" * len(hdr))
    if filling:
        w(f"{filling[0].k + 1:>5}-{filling[-1].k + 1:<4} window filling, no assessment "
          f"(L0 total {sum(t.ms.get('L0', 0) for t in filling):.2f} ms)")
    for t in ticks:
        def v(d, s):
            return f"{d[s]:9.2f}" if s in d else f"{'-':>9}"
        w(f"{t.k + 1:>5} {t.plant:<8} {t.state:<12} {t.triage:<11}"
          + "".join(v(t.ms, s) for s in STAGES)
          + (f"{t.llm_ms['L4']:10.1f}" if "L4" in t.llm_ms else f"{'-':>10}")
          + (f"{t.llm_ms['L5']:10.1f}" if "L5" in t.llm_ms else f"{'-':>10}")
          + f"{t.other_ms:9.2f}{t.total_ms:11.2f}  {'MISS' if t.deadline_miss else ''}")

    # ---- per LLM call ------------------------------------------------------
    w("")
    w("LLM CALLS")
    chdr = (f"{'#':>4} {'tick':>5} {'stage':<6}{'role':<14}{'try':>4} {'device':<8}"
            f"{'status':<15}{'wall ms':>10}{'ttft ms':>9}{'pre tok':>8}{'dec tok':>8}"
            f"{'pre t/s':>9}{'dec t/s':>8}")
    w(chdr)
    w("-" * len(chdr))
    for c in calls:
        w(f"{c['n']:>4} {c['tick'] + 1:>5} {c['stage']:<6}{c['role']:<14}{c['attempt']:>4} "
          f"{c.get('device', '?'):<8}{c['status']:<15}{c['wall_ms']:10.1f}"
          f"{c.get('ttft_ms', 0):9.1f}{c.get('prefill', 0):8d}{c.get('decode', 0):8d}"
          f"{c.get('prefill_tps', 0):9.1f}{c.get('decode_tps', 0):8.1f}"
          + (f"  {c['error']}" if c.get("error") else ""))
    if not calls:
        w("  (none -- every assessed tick was QUIET or the LLM stages were skipped)")

    # ---- per stage summary -----------------------------------------------
    w("")
    w("PER STAGE  (over the ticks where the stage ran)")
    shdr = (f"{'stage':<16}{'ran':>5}{'median':>11}{'p95':>11}{'max':>11}"
            f"{'total s':>10}{'% wall':>8}")
    w(shdr)
    w("-" * len(shdr))
    every = ticks + filling
    for s in STAGES:
        xs = [t.ms[s] for t in every if s in t.ms]
        if not xs:
            w(f"{s + ' ' + STAGE_NAME[s]:<16}{0:>5}")
            continue
        w(f"{s + ' ' + STAGE_NAME[s]:<16}{len(xs):>5}{statistics.median(xs):11.2f}"
          f"{_p95(xs):11.2f}{max(xs):11.2f}{sum(xs) / 1000:10.2f}"
          f"{100 * sum(xs) / 1000 / wall_s if wall_s else 0:7.1f}%")
    for s in LLM_STAGES:
        xs = [t.llm_ms[s] for t in ticks if s in t.llm_ms]
        if xs:
            w(f"{'  of which llm':<16}{len(xs):>5}{statistics.median(xs):11.2f}"
              f"{_p95(xs):11.2f}{max(xs):11.2f}{sum(xs) / 1000:10.2f}"
              f"{100 * sum(xs) / 1000 / wall_s if wall_s else 0:7.1f}%   ({s})")
    xs = [t.other_ms for t in ticks]
    if xs:
        w(f"{'other':<16}{len(xs):>5}{statistics.median(xs):11.2f}{_p95(xs):11.2f}"
          f"{max(xs):11.2f}{sum(xs) / 1000:10.2f}"
          f"{100 * sum(xs) / 1000 / wall_s if wall_s else 0:7.1f}%")

    # ---- per unit ----------------------------------------------------------
    llm_s = sum(c["wall_ms"] for c in calls) / 1000
    stage_s = sum(sum(t.ms.values()) + t.other_ms for t in every) / 1000
    cpu_s = stage_s - llm_s
    w("")
    w("PER COMPUTE UNIT")
    w(f"  CPU ({meta['host']}) busy   {cpu_s:10.2f} s   {100 * cpu_s / wall_s if wall_s else 0:5.1f}% of wall")
    w(f"  {mon.llm_label.upper()} ({meta['llm_where']}) busy   {llm_s:10.2f} s   "
      f"{100 * llm_s / wall_s if wall_s else 0:5.1f}% of wall")
    w(f"  neither (harness loop, untimed)   {wall_s - stage_s:10.2f} s")

    # ---- self-consistency ------------------------------------------------
    # Independent route: the orchestrator times every tick itself
    # (Assessment.tick_latency_ms, its own perf_counter). This script times the
    # same call from outside. They should differ only by the wrapper overhead.
    w("")
    w("CROSS-CHECK  (this script's clock vs the orchestrator's own tick_latency_ms)")
    diffs = [t.wrap_ms - t.orch_ms for t in ticks
             if t.wrap_ms is not None and t.orch_ms is not None]
    if diffs:
        w(f"  ticks compared        {len(diffs)}")
        w(f"  median |diff|         {statistics.median(abs(d) for d in diffs):.3f} ms")
        w(f"  max |diff|            {max(abs(d) for d in diffs):.3f} ms")
        w(f"  max 'other' residual  {max(t.other_ms for t in ticks):.3f} ms")
    else:
        w("  no assessed ticks")

    # Every LLM call must sit inside L4 or L5, and the call counts must agree
    # with the agents' own counters (Verifier has no repair retry, so its count
    # is exact; Diagnostician may add one repair call per run).
    w("")
    w("CALL ACCOUNTING")
    stray = [c["n"] for c in calls if c["stage"] not in LLM_STAGES]
    w(f"  calls outside L4/L5   {len(stray)}"
      + (f"   [!] calls {stray[:10]}" if stray else "   ok"))
    if run:
        n_ver = sum(1 for c in calls if c["role"] == "verifier")
        n_diag = sum(1 for c in calls if c["role"] == "diagnostician")
        n_l5 = sum(1 for t in ticks if "L5" in t.llm_ms)
        w(f"  verifier calls        {n_ver} timed, {run['ver_calls']} counted by Verifier"
          f"{'   ok' if n_ver == run['ver_calls'] == n_l5 else '   [!] MISMATCH'}")
        ok = run["diag_calls"] <= n_diag <= 2 * run["diag_calls"]
        w(f"  diagnostician calls   {n_diag} timed (incl. repair), {run['diag_calls']} "
          f"Diagnostician runs{'   ok' if ok else '   [!] MISMATCH'}")
    else:
        w("  run did not complete -- agent counters unavailable")

    path.write_text("\n".join(L) + "\n")


# ===========================================================================
#  Main
# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episode", required=True, help="episode id, e.g. ep_A02_fcv_seize_fast")
    ap.add_argument("--config", default=str(ROOT / "configs/base.yaml"))
    ap.add_argument("--backend", default=None,
                    choices=["mock", "gemini", "litert", "llamaserver"],
                    help="overrides llm.backend in the config")
    ap.add_argument("--out", default=str(ROOT / "results"),
                    help="directory for the report (default: results/)")
    ap.add_argument("--model-file", default=None,
                    help="llamaserver: the GGUF the lane has loaded (recorded in the results)")
    ap.add_argument("--save-run", action="store_true",
                    help="also write <episode>.run.json.gz and <episode>.summary.json "
                         "(evaluation) into --out, for the presentation benchmark")
    ap.add_argument("--max-ticks", type=int, default=None,
                    help="stop after N assessed ticks (quick device check)")
    ap.add_argument("--refresh", type=float, default=0.5, help="screen refresh, seconds")
    ap.add_argument("--plain", action="store_true",
                    help="one line per event instead of the live screen "
                         "(automatic when stdout is not a terminal)")
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    out_dir = Path(args.out).resolve()
    os.chdir(ROOT)                       # config paths are repo-relative
    cfg = yaml.safe_load(cfg_path.read_text())
    if args.backend:
        cfg["llm"]["backend"] = args.backend
    if args.model_file:
        cfg["llm"].setdefault("llamaserver", {})["model_file"] = args.model_file
    # The per-call TracingBackend would print into the live screen; this
    # script records the same fields itself.
    cfg["llm"]["trace_file"] = ""
    cfg["llm"]["trace"] = False
    cfg.setdefault("agent", {})["log_prompts"] = False

    ep_path = Path(cfg["paths"]["episodes"]) / args.episode
    if not (ep_path / "ground_truth.json").exists():
        ap.error(f"no episode at {ep_path}")
    ep = Episode(ep_path)
    tick_s = cfg["agent"]["tick_period_s"]
    n_ticks = int(ep.duration_s // tick_s)

    backend = cfg["llm"]["backend"]
    label, where = llm_unit(cfg)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"stage_timing_{ep.id}_{backend}_{stamp}.txt"
    log_path = out_dir / f"stage_timing_{ep.id}_{backend}_{stamp}.log"
    gt = ep.ground_truth
    meta = {
        "episode": ep.id,
        "family": f"family {gt.get('family', '?')}, tier {gt.get('tier', '?')}",
        "backend": backend,
        "model": ((cfg["llm"].get(backend, {}) or {}).get("model")
                  or (cfg["llm"].get(backend, {}) or {}).get("model_file") or backend),
        "llm_where": where,
        "host": f"{socket.gethostname()} ({platform.machine()})",
        "policy": cfg["agent"].get("policy", "sequential"),
        "verifier": cfg["agent"].get("verifier", "conditional"),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    term = sys.__stdout__
    live = not args.plain and term.isatty()
    mon = Monitor(tick_s, n_ticks, label, args.max_ticks,
                  plain_out=None if live else term)
    undo = install(mon)

    result: dict = {}

    def worker():
        try:
            result["run"] = harness.run_episode(ep, cfg)
            result["outcome"] = "COMPLETE"
        except StopEarly as e:
            result["outcome"] = f"PARTIAL ({e})"
        except BaseException:
            result["outcome"] = "ERROR"
            result["error"] = traceback.format_exc()
        finally:
            mon.t_end = time.perf_counter()

    if not live:
        print(f"{ep.id}: {n_ticks} ticks, backend {backend} ({label}, {where}), "
              f"CPU stages on {meta['host']}", file=term, flush=True)
        if backend == "mock":
            print("NOTE: mock backend -- plumbing timings only, NOT agent results.",
                  file=term, flush=True)

    captured = io.StringIO()
    th = threading.Thread(target=worker, daemon=True)
    outcome = None
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        if live:
            term.write("\033[?1049h\033[?25l")          # alternate screen, hide cursor
        try:
            th.start()
            while th.is_alive():
                if live:
                    frame = render(mon, meta)
                    term.write("\033[H" + "".join(l + "\033[K\n" for l in frame) + "\033[J")
                    term.flush()
                th.join(args.refresh)
        except KeyboardInterrupt:
            mon.t_end = time.perf_counter()
            outcome = "INTERRUPTED (Ctrl-C)"
        finally:
            if live:
                term.write("\033[?25h\033[?1049l")
                term.flush()
            undo()

    outcome = outcome or result.get("outcome", "UNKNOWN")
    if captured.getvalue().strip():
        log_path.write_text(captured.getvalue())
        meta["log"] = str(log_path)
    write_report(report, mon, meta, outcome, result.get("error", ""),
                 result.get("run"))
    saved = ""
    if args.save_run and result.get("run") is not None and outcome == "COMPLETE":
        import gzip
        import json
        from bench.evaluator import evaluate
        run = result["run"]
        (out_dir / f"{ep.id}.run.json.gz").write_bytes(gzip.compress(json.dumps(run).encode()))
        (out_dir / f"{ep.id}.summary.json").write_text(json.dumps(
            {"meta": {**meta, "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "runner": "bench/stage_monitor.py (LlamaServerBackend)",
                      "timing_report": report.name, "energy_mwh": None},
             "evaluation": evaluate(run)}, indent=1))
        saved = f"{out_dir}/{ep.id}.summary.json"

    # Final summary on the normal screen.
    for line in render(mon, meta)[:8] if live else []:
        print(line, file=term)
    print(f"\n{outcome}", file=term)
    if result.get("error"):
        print(result["error"], file=term)
    print(f"report  -> {report}", file=term)
    if saved:
        print(f"results -> {saved}", file=term)
    if meta.get("log"):
        print(f"stdout  -> {log_path}", file=term)
    return 0 if outcome.startswith(("COMPLETE", "PARTIAL")) else 1


if __name__ == "__main__":
    sys.exit(main())
