#!/usr/bin/env python3
"""
Summary of a real-time run (multi-agent Phase 4 demonstration subset).

    python bench/realtime_summary.py results/board_multi/runs_llamaserver_<tag>.json

Per episode: model calls per lane and agent, how long both lanes were busy at
the same time, the slowest call per agent (a call slower than the tick means
its answers arrive stale and are dropped: lengthen --tick-s or speed that
lane), answers dropped as stale, tick lateness, the worst whole-tick time on the
tick thread, and the group top-1 (a mock run's top-1 is not an accuracy result).
Host-side tool: bench/ only.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench.evaluator import evaluate  # noqa: E402


def summarise(run: dict) -> dict:
    m = run.get("multi") or {}
    rt = m.get("realtime") or {}
    tick = rt.get("wall_tick_s")
    slow = defaultdict(float)          # the call itself
    turn = defaultdict(float)          # submit to finish: queue wait behind the lane + the call
    n = defaultdict(int)
    for s in rt.get("spans", []):
        k = (s["agent"], s["lane"])
        slow[k] = max(slow[k], s["end_s"] - s["start_s"])
        turn[k] = max(turn[k], s["end_s"] - s.get("submit_s", s["start_s"]))
        n[k] += 1
    p0 = [a["multi"]["p0_ms"] for a in run["assessments"] if a.get("multi")]
    t2 = evaluate(run)["T2_root_cause"]
    return {
        "episode": run["episode_id"], "backend": run["backend"], "mode": run.get("mode"),
        "wall_tick_s": tick, "ticks": run["n_ticks"],
        "calls": {f"{a}@{l}": n[(a, l)] for (a, l) in sorted(n)},
        "slowest_call_s": {f"{a}@{l}": round(v, 2) for (a, l), v in sorted(slow.items())},
        "slowest_submit_to_answer_s": {f"{a}@{l}": round(v, 2) for (a, l), v in sorted(turn.items())},
        "calls_slower_than_a_tick": sorted(f"{a}@{l}" for (a, l), v in turn.items()
                                           if tick and v > tick),
        "workers_alive": rt.get("workers_alive"),
        "both_lanes_busy_s": rt.get("both_lanes_busy_s"),
        "busy_s": rt.get("busy_s"),
        "stale_dropped": len(m.get("stale_dropped", [])),
        "tick_lag_s_max": rt.get("tick_lag_s_max"),
        "tick_ms_max": round(max(p0), 1) if p0 else None,
        "parse_failure_rate": run.get("parse_failure_rate"),
        "group_top1": t2.get("group_top1") if t2.get("applicable") else None,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    runs = json.loads(Path(sys.argv[1]).read_text())
    runs = runs if isinstance(runs, list) else runs["runs"]
    for run in runs:
        s = summarise(run)
        print(json.dumps(s, indent=1))
        if s["calls_slower_than_a_tick"]:
            print(f"WARNING {s['episode']}: {s['calls_slower_than_a_tick']} took longer than a "
                  f"{s['wall_tick_s']} s tick; their answers arrive stale")
        if s["backend"] == "mock":
            print("NOTE: mock backend -- top-1 measures retrieval, not a model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
