#!/usr/bin/env python3
"""
Phase 2 decision comparison (gates G1a, G1b and the per-commit checks).

    python bench/gate_phase2.py A/runs.json B/runs.json \
        [--summary-a A/summary.json --summary-b B/summary.json] \
        [--g1a] [--expect unchanged=19,rank1=580,ver=327]

Default (strict): every key is compared except the timing keys and the fields
pre-registered as moving BY CONSTRUCTION in
reports/multi_phase2_prompt_shrink.md (printed below). Envelope `cited_facts`
IS compared, after the tick stamp is stripped (`t84.F1` -> `F1`).

--g1a additionally allows the CONFIDENCE-DERIVED fields of amendment 2, and
only these: confidence / confidence_shown of `model_only` hypotheses, the
assessment's confidence when rank 1 is `model_only`, the presence of the
verifier envelope on such ticks at INVESTIGATE or URGENT, ver_calls and
envelope_status_counts, and the summary's low_conf_rate keys. It counts them
so the counts can be checked against the committed predictions. Anything else
that differs is listed and fails the gate.

A key that one whole run lacks (`belief_supports`, `text_calls` in Phase 1
files) is skipped and SAID to be skipped.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys

sys.path.insert(0, ".")
from bench.compare_runs import EXCLUDED as TIMING, diff

# pre-registered: move by construction when the prompt or answer format changes
MOVING = {"prompt", "raw_reply", "payload", "tokens", "prompt_tokens", "prefill",
          "decode", "mean_prompt_tokens", "text_calls"}
SUMMARY_CONF = {"low_conf_rate", "low_conf_rate_decision"}
VER_LEVELS = ("INVESTIGATE", "URGENT")


def _strip(v, drop):
    if isinstance(v, dict):
        return {k: ([c.rpartition(".")[2] for c in x] if k == "cited_facts"
                    else _strip(x, drop))
                for k, x in v.items() if k not in drop}
    if isinstance(v, list):
        return [_strip(x, drop) for x in v]
    return v


def _has(runs, key):
    return any(key in a for r in runs for a in r["assessments"])


def compare(runs_a, runs_b, g1a=False):
    drop = set(TIMING) | MOVING
    skipped = []
    for key in ("belief_supports",):
        if _has(runs_a, key) != _has(runs_b, key):
            drop.add(key)
            skipped.append(key)
    counts = {"model_only": 0, "model_only_conf_unchanged": 0,
              "rank1_conf_changed": 0, "verifier_flips": 0,
              "ver_calls_a": sum(r["ver_calls"] for r in runs_a),
              "ver_calls_b": sum(r["ver_calls"] for r in runs_b)}
    other: list = []
    if [r["episode_id"] for r in runs_a] != [r["episode_id"] for r in runs_b]:
        return counts, [("episode ids differ", None, None)], skipped
    for ra, rb in zip(runs_a, runs_b):
        ra, rb = copy.deepcopy(ra), copy.deepcopy(rb)
        if g1a:
            for r in (ra, rb):
                r.pop("ver_calls", None)
                r.pop("envelope_status_counts", None)
            if len(ra["assessments"]) == len(rb["assessments"]):
                for a, b in zip(ra["assessments"], rb["assessments"]):
                    _neutralise(a, b, counts)
        ep = ra["episode_id"]
        diff(_strip(ra, drop), _strip(rb, drop), ep, other, 10**6)
    return counts, other, skipped


def _neutralise(a, b, counts):
    """Blank the confidence-derived fields of one assessment pair, counting
    them. Everything it does not blank is still compared strictly."""
    ha, hb = a.get("hypotheses", []), b.get("hypotheses", [])
    if len(ha) != len(hb):
        return
    rank1_mo = bool(ha and ha[0].get("model_only") and hb[0].get("model_only"))
    if ha and ha[0].get("confidence") != hb[0].get("confidence"):
        counts["rank1_conf_changed"] += 1
    for x, y in zip(ha, hb):
        if x.get("model_only") and y.get("model_only"):
            counts["model_only"] += 1
            counts["model_only_conf_unchanged"] += x.get("confidence") == y.get("confidence")
            for h in (x, y):
                h["confidence"] = h["confidence_shown"] = None
    if rank1_mo:
        a["confidence"] = b["confidence"] = None
        if a.get("triage") in VER_LEVELS:
            va = [e for e in a.get("envelopes", []) if e.get("agent") == "verifier"]
            vb = [e for e in b.get("envelopes", []) if e.get("agent") == "verifier"]
            if bool(va) != bool(vb):
                counts["verifier_flips"] += 1
                for s in (a, b):
                    s["envelopes"] = [e for e in s.get("envelopes", [])
                                      if e.get("agent") != "verifier"]


def compare_summaries(sa, sb, g1a=False):
    drop = set(TIMING) | MOVING | (SUMMARY_CONF if g1a else set())
    new = sorted((set(sb.get("summary", sb)) - set(sa.get("summary", sa))))
    out: list = []
    a, b = _strip(sa, drop), _strip(sb, drop)
    # keys only one file has (new metrics such as Q3_rel) are reported, not failed
    extra = {"Q3_rel", "rel", "n_rel_citations"}
    present_a, present_b = json.dumps(a), json.dumps(b)
    drop2 = {k for k in extra if (f'"{k}"' in present_a) != (f'"{k}"' in present_b)}
    diff(_strip(a, drop2), _strip(b, drop2), "summary", out, 10**6)
    return out, sorted(drop2), new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--summary-a")
    ap.add_argument("--summary-b")
    ap.add_argument("--g1a", action="store_true")
    ap.add_argument("--expect", default="",
                    help="g1a predictions: unchanged=19,rank1=580,ver=327")
    args = ap.parse_args()
    load = lambda p: (lambda d: d if isinstance(d, list) else d["runs"])(json.load(open(p)))
    ra, rb = load(args.a), load(args.b)
    counts, other, skipped = compare(ra, rb, args.g1a)
    n_asmt = sum(len(r["assessments"]) for r in ra)
    print(f"runs: {len(ra)} episodes, {n_asmt} assessments compared"
          f" ({'G1a: confidence-derived fields allowed' if args.g1a else 'strict'})")
    print(f"moving by construction (not compared): {sorted(MOVING)}")
    if skipped:
        print(f"skipped, absent from one run file: {skipped}")
    print(f"runs: {len(other)} differences outside the allowed fields")
    for path, x, y in other[:10]:
        print(f"   {path}: {str(x)[:90]!r} != {str(y)[:90]!r}")
    fail = bool(other)
    if args.g1a:
        print(f"G1a counts: {counts}")
        for item in filter(None, args.expect.split(",")):
            k, v = item.split("=")
            got = {"unchanged": counts["model_only_conf_unchanged"],
                   "model_only": counts["model_only"],
                   "rank1": counts["rank1_conf_changed"],
                   "ver": counts["ver_calls_b"]}[k]
            ok = got == int(v)
            fail |= not ok
            print(f"   prediction {k}: expected {v}, measured {got} -> "
                  f"{'MATCH' if ok else 'MISMATCH'}")
    if args.summary_a and args.summary_b:
        sd, dropped, new = compare_summaries(json.load(open(args.summary_a)),
                                             json.load(open(args.summary_b)), args.g1a)
        if dropped:
            print(f"summary: keys in one file only, not compared: {dropped}")
        if args.g1a:
            print(f"summary: confidence-derived keys not compared: {sorted(SUMMARY_CONF)}")
        print(f"summary: {len(sd)} differences")
        for path, x, y in sd[:10]:
            print(f"   {path}: {x!r} != {y!r}")
        fail |= bool(sd)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
