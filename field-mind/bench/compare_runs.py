#!/usr/bin/env python3
"""
Decision-identity check between two runs (multi-agent Phase 1 gate).

    python bench/compare_runs.py results/x/runs_A.json results/x/runs_B.json \
        [--summary-a summary_A.json --summary-b summary_B.json]

Walks both runs_*.json files episode by episode, assessment by assessment, and
compares every key except the pre-registered exclusions (timing-derived fields
and the multi-only telemetry key; reports/multi_phase1_plumbing.md, "Excluded
fields"). Prints the number of differences and the FIRST differing
(episode, tick, field path). Exit code 0 only when there are no differences.

The exclusion is by key name at any depth, so a new timing field added later
must be added here on purpose, not silently ignored.
"""

from __future__ import annotations

import argparse
import json
import sys

# Pre-registered (reports/multi_phase1_plumbing.md). Timing-derived or
# multi-only telemetry; everything else is compared.
EXCLUDED = {
    "latency_ms", "prefill_ms", "tick_latency_ms", "wall_clock_s",
    "deadline_miss", "deadline_miss_rate",
    "S1_tick_latency_ms_p50", "S1_tick_latency_ms_p95", "S7_deadline_miss_rate",
    "multi",
}


def diff(a, b, path: str, out: list, limit: int) -> None:
    """Append (path, a, b) for every difference under a/b, skipping EXCLUDED keys."""
    if len(out) >= limit:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in EXCLUDED:
                continue
            if k not in a or k not in b:
                out.append((f"{path}.{k}", a.get(k, "<missing>"), b.get(k, "<missing>")))
                continue
            diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((f"{path}[len]", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit)
    elif a != b or type(a) is not type(b):
        out.append((path, a, b))


def compare_runs(runs_a: list, runs_b: list, limit: int = 10**7) -> list:
    """Differences between two runs lists, in episode then tick order."""
    out: list = []
    by_b = {r["episode_id"]: r for r in runs_b}
    ids_a = [r["episode_id"] for r in runs_a]
    if ids_a != [r["episode_id"] for r in runs_b]:
        out.append(("episode_ids", ids_a, [r["episode_id"] for r in runs_b]))
    for ra in runs_a:
        rb = by_b.get(ra["episode_id"])
        if rb is None:
            continue
        ep = ra["episode_id"]
        # run-level keys first, then each assessment labelled with its tick
        diff({k: v for k, v in ra.items() if k != "assessments"},
             {k: v for k, v in rb.items() if k != "assessments"}, ep, out, limit)
        aa, ab = ra["assessments"], rb["assessments"]
        if len(aa) != len(ab):
            out.append((f"{ep}.assessments[len]", len(aa), len(ab)))
        for x, y in zip(aa, ab):
            diff(x, y, f"{ep}.t{x.get('tick')}", out, limit)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs_a")
    ap.add_argument("runs_b")
    ap.add_argument("--summary-a")
    ap.add_argument("--summary-b")
    ap.add_argument("--show", type=int, default=5, help="differences to print")
    args = ap.parse_args()

    ra = json.load(open(args.runs_a))
    rb = json.load(open(args.runs_b))
    n_asmt = sum(len(r["assessments"]) for r in ra)
    n_env = sum(len(a["envelopes"]) for r in ra for a in r["assessments"])
    d = compare_runs(ra, rb)
    print(f"runs: {len(ra)} episodes, {n_asmt} assessments, {n_env} envelopes compared")
    print(f"runs: {len(d)} differences")
    for p, x, y in d[:args.show]:
        print(f"  FIRST: {p}\n    a={str(x)[:300]}\n    b={str(y)[:300]}")

    ds = []
    if args.summary_a and args.summary_b:
        sa = json.load(open(args.summary_a))
        sb = json.load(open(args.summary_b))
        diff(sa, sb, "summary", ds, 10**7)
        print(f"summary: {len(ds)} differences")
        for p, x, y in ds[:args.show]:
            print(f"  {p}: a={x} b={y}")
    return 0 if not d and not ds else 1


if __name__ == "__main__":
    sys.exit(main())
