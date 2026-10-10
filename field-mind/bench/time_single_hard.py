#!/usr/bin/env python3
"""
The single agent's hard-stage time, measured from outside.

    .venv/bin/python bench/time_single_hard.py [--episodes-dir data/episodes_dev]

The single agent sums two timers into `hard_ms` (L1 CheckLayer.run + L6
Gate.approve) and uses it for its S7 deadline test, but does not write it to
the run file. This script wraps those two methods and Orchestrator.tick, so
nothing in fieldmind/agent/ changes. Mock backend; LAPTOP timing, not board
timing. It is the measurement behind the "single: hard-stage time" rows in
reports/multi_phase1_plumbing.md (close-out, section 1).
"""
import argparse
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
import yaml

import fieldmind.agent.orchestrator as O
from bench.harness import Episode, run_episode
from fieldmind.agent.l1_checks import CheckLayer
from fieldmind.agent.l6_gate import Gate

ap = argparse.ArgumentParser()
ap.add_argument("--episodes-dir", default="data/episodes_dev")
args = ap.parse_args()

cur = {"ms": 0.0}


def wrap(cls, name):
    orig = getattr(cls, name)

    def w(self, *a, **k):
        t0 = time.perf_counter()
        try:
            return orig(self, *a, **k)
        finally:
            cur["ms"] += (time.perf_counter() - t0) * 1000
    setattr(cls, name, w)


wrap(CheckLayer, "run")
wrap(Gate, "approve")

cfg = yaml.safe_load(Path("configs/base.yaml").read_text())
cfg["llm"]["backend"] = "mock"
orig_tick = O.Orchestrator.tick
rows = []


def tick(self, *a, **k):
    cur["ms"] = 0.0
    asmt = orig_tick(self, *a, **k)
    rows.append((asmt.triage, cur["ms"], asmt.tick_latency_ms))
    return asmt


O.Orchestrator.tick = tick
for p in sorted(Path(args.episodes_dir).iterdir()):
    if (p / "ground_truth.json").exists():
        run_episode(Episode(p), cfg)


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def f(xs):
    return (f"n={len(xs)} mean {st.mean(xs):.3f} p50 {pct(xs, .5):.3f} "
            f"p95 {pct(xs, .95):.3f} max {max(xs):.3f}")


print("single hard-stage ms (L1+L6), all ticks      ", f([r[1] for r in rows]))
print("single hard-stage ms (L1+L6), non-QUIET ticks", f([r[1] for r in rows if r[0] != "QUIET"]))
print("single whole tick ms, all ticks              ", f([r[2] for r in rows]))
print("hard-stage > 200 ms:", sum(r[1] > 200 for r in rows))
