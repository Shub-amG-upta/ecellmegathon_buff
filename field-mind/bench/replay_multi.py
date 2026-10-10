#!/usr/bin/env python3
"""
Offline replay of a saved multi-agent run (accuracy-fix work, step 0).

    .venv/bin/python bench/replay_multi.py RUN.json[.gz] [--rule current|belief]

A board run saves, per tick, belief's live ranking (`belief_ranking`, with
log-odds and confidence) and every diagnostician / verifier answer with the
line numbers it chose and the case and fact ids those lines stood for
(`multi.compact`). That is everything the gate used to publish its ranking,
so a different merge RULE can be applied to the SAME model answers without
the board. The model's answers are not re-generated; a rule that changes the
prompt cannot be replayed, only one that changes what is done with an answer.

Rules (`model` is an alias of `current`; the others are
fieldmind/multi/merge_rules.py, applied exactly as the gate applies them):
  belief_only, tiebreak, nudge, hybrid   see merge_rules.py
  current   what the gate does under merge_rule `model`: the side answers are expanded (case and
            fact ids from the line map, confidence from belief, 0.3 when belief
            has no live entry), combined in side order, folded by the single
            agent's `merge` (model order wins), then the verifier's verdicts
            applied. Used to check that the replay is faithful: it must
            reproduce the run's published ranking tick for tick.
  belief    belief's own top 3 (initial_claims), no model: belief-alone.

Not replayed: cross-tick (stale) acceptance in real time; failed calls (an
answer record is only written for a successful call); experience-store cases.

Host-side tooling; imports fieldmind/ read-only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, ".")
from fieldmind.agent.l5_verify import Verifier
from fieldmind.agent.orchestrator import initial_claims, merge
from fieldmind.multi import compact, merge_rules

CASES = {c["case_id"]: c for c in
         json.loads(Path("data/kb/case_library.json").read_text())["cases"]}
FLAT = merge_rules.flat_case_ids(list(CASES.values()))


def load_run(path: str) -> dict:
    raw = Path(path).read_bytes()
    d = json.loads(gzip.decompress(raw) if path.endswith(".gz") else raw)
    if isinstance(d, list):
        d = d[0]
    return d["runs"][0] if "runs" in d else d


def _live(a: dict) -> list:
    """Belief's live hypotheses in INSERTION order, as the gate reads them."""
    order = a.get("belief_order") or []
    sup = a.get("belief_supports") or {}
    hs = [SimpleNamespace(case_ref=b["case_ref"], cause=b["cause"],
                          confidence=b["confidence"], log_odds=b["log_odds"],
                          supports=list(sup.get(b["case_ref"], [])),
                          discriminator=CASES.get(b["case_ref"], {})
                          .get("discriminating_evidence", ""), retired=False)
          for b in a.get("belief_ranking") or []]
    pos = {c: i for i, c in enumerate(order)}
    return sorted(hs, key=lambda h: pos.get(h.case_ref, len(pos)))


def _belief(a: dict) -> list:
    """Belief's live hypotheses in rank_hypotheses order: descending
    confidence, ties in insertion order (`belief_order`, saved by the harness
    from step 0 on). Older runs lack it; their ties fall back to
    belief_ranking's (log-odds) order, which can differ -- `tie_unresolved`."""
    order = {c: i for i, c in enumerate(a.get("belief_order") or [])}
    live = [SimpleNamespace(case_ref=b["case_ref"], cause=b["cause"],
                            confidence=b["confidence"], log_odds=b.get("log_odds"),
                            supports=[], discriminator=CASES.get(b["case_ref"], {})
                            .get("discriminating_evidence", ""))
            for b in a.get("belief_ranking") or []]
    if order:
        live.sort(key=lambda h: order.get(h.case_ref, len(order)))
    return sorted(live, key=lambda h: -h.confidence)


def tie_unresolved(a: dict) -> bool:
    """True when belief's top 4 holds a confidence tie and the run did not save
    the insertion order needed to break it."""
    if a.get("belief_order"):
        return False
    c = sorted((b["confidence"] for b in a.get("belief_ranking") or []), reverse=True)[:4]
    return len(c) != len(set(c))


def expand(rec: dict, live: list) -> dict:
    """A diagnostician answer record -> the single agent's payload shape,
    as compact.expand_diag_answer does it, line map rebuilt from the record."""
    conf = {h.case_ref: h.confidence for h in live}
    lm = {"cases": [{"case_id": c, "root_cause": CASES.get(c, {}).get("root_cause"),
                     "discriminating_evidence": CASES.get(c, {}).get("discriminating_evidence", "")}
                    for c in rec["case_ids"]],
          "facts": list(rec["fact_ids"]), "fact_detail": ["?"] * len(rec["fact_ids"]),
          "conf": [conf.get(c, compact.NO_BELIEF_CONF) for c in rec["case_ids"]],
          "context": ["?"] * 32, "evidence_tick": rec["evidence_tick"]}
    payload, _ = compact.expand_diag_answer(rec["answer"], lm)
    return payload


def replay(run: dict, rule: str = "current") -> list[dict]:
    """Per assessment: the published hypotheses [(case_ref, confidence,
    flags)] under `rule`. QUIET ticks give []."""
    if rule == "model":
        rule = "current"
    out, cache, offsets = [], {}, {}
    for a in run["assessments"]:
        if a["triage"] == "QUIET" or not a.get("belief_ranking") and not a["hypotheses"]:
            out.append({"tick": a["tick"], "hyps": []})
            continue
        live = _belief(a)
        claims = initial_claims(a["headline"], live[:3])
        recs = a.get("multi", {}).get("compact", [])
        if rule in ("current",) + merge_rules.RULES[1:]:
            answers = {r["side"]: expand(r, live) for r in recs
                       if r.get("agent") == "diagnostician" and r.get("answer") is not None}
            m = next((r for r in recs if r.get("agent") == "merge"), None)
            if m:
                for s in m["reused"]:
                    answers[s] = cache[s]
                cache.update({s: p for s, p in answers.items() if s not in m["reused"]})
                for s in list(cache):
                    if s not in m["order"]:
                        del cache[s]
                payload = compact.combine_side_payloads([answers[s] for s in m["order"]])
                fresh = compact.combine_side_payloads(
                    [answers[s] for s in m["order"] if s not in m["reused"]])
            else:
                payload = fresh = None
            if rule == "current":
                if payload is not None:
                    claims = merge(claims, payload)
            else:
                live = _live(a)
                model = (payload or {}).get("hypotheses", [])
                if rule == "belief_only":
                    hyps, _ = merge_rules.belief_only(live)
                elif rule == "tiebreak":
                    hyps, _ = merge_rules.tiebreak(live, model)
                elif rule == "hybrid":
                    if fresh and fresh["hypotheses"]:
                        offsets = merge_rules.add_nudges(
                            offsets, [m for m in fresh["hypotheses"] if m.get("case_ref") not in FLAT])
                    hyps, _ = merge_rules.hybrid(live, model, offsets, FLAT)
                else:
                    if fresh and fresh["hypotheses"]:
                        offsets = merge_rules.add_nudges(offsets, fresh["hypotheses"])
                    hyps, _ = merge_rules.nudge(live, offsets)
                claims = dict(claims, hypotheses=hyps)
            v = next((r for r in recs if r.get("agent") == "verifier"), None)
            if v and v.get("answer") is not None:
                shown = claims["hypotheses"][:compact.MAX_CLAIMS]
                lm = {"claims": [h["cause"] for h in shown], "facts": ["?"] * 32,
                      "fact_detail": ["?"] * 32, "evidence_tick": v["evidence_tick"]}
                payload, _ = compact.expand_ver_answer(v["answer"], lm)
                payload["strongest_contradiction"] = None
                claims = Verifier.apply(claims, payload)
        if rule not in ("current", "belief") + merge_rules.RULES[1:]:
            raise ValueError(f"unknown rule {rule!r}")
        out.append({"tick": a["tick"],
                    "hyps": [(h["case_ref"], round(float(h["confidence"]), 6),
                              tuple(k for k in ("model_only", "carried", "verifier", "tiebreak", "nudged")
                                    if h.get(k)))
                             for h in claims["hypotheses"]]})
    return out


def published(run: dict) -> list[dict]:
    return [{"tick": a["tick"],
             "hyps": [] if a["triage"] == "QUIET" else
             [(h["case_ref"], round(float(h["confidence"]), 6),
               tuple(k for k in ("model_only", "carried", "verifier", "tiebreak", "nudged") if h.get(k)))
              for h in a["hypotheses"]]} for a in run["assessments"]]


def group_top1(run: dict, ranking: list[dict]):
    """Share of ticks with a ranking whose rank-1 case is in the true case's
    look-alike group; a held-out target uses its cited group, as the evaluator
    does (bench/case_groups.load_group_map). Per tick, not the evaluator's
    scored-window definition: for comparing rules on one run."""
    from bench.case_groups import load_group_map
    gmap, held = load_group_map()
    tgt = run["ground_truth"].get("root_cause_id")
    if not tgt or tgt == "NONE":
        return None, 0
    tg = held.get(tgt) if tgt in held else gmap.get(tgt, tgt)
    n = ok = 0
    for r in ranking:
        if r["hyps"]:
            n += 1
            ok += gmap.get(r["hyps"][0][0], r["hyps"][0][0]) == tg
    return (round(ok / n, 3) if n else None), n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--rule", default="current")
    ap.add_argument("--check", action="store_true",
                    help="with --rule current: compare with the run's published ranking")
    args = ap.parse_args()
    bad = 0
    for p in args.runs:
        run = load_run(p)
        rep = replay(run, args.rule)
        g, n = group_top1(run, rep)
        line = f"{run['episode_id']:30s} rule {args.rule:8s} group top-1 {g} over {n} ticks"
        if args.check:
            pub = published(run)
            ties = {a["tick"] for a in run["assessments"] if tie_unresolved(a)}
            diffs = [(x["tick"], x["hyps"], y["hyps"]) for x, y in zip(rep, pub)
                     if x["hyps"] != y["hyps"]]
            other = [d for d in diffs if d[0] not in ties]
            bad += bool(other)
            line += (f" | differences from the run: {len(diffs)}, of which on an "
                     f"unresolved belief tie {len(diffs) - len(other)}")
            diffs = other
            for t, x, y in diffs[:2]:
                line += f"\n    tick {t}: replay {x[:3]}\n             run    {y[:3]}"
        print(line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
