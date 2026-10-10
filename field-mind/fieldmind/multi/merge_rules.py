"""
=============================================================================
 MERGE RULES  --  how a checked model answer reaches the published ranking
 (accuracy-fix work, reports/multi_accuracy_fix.md, human decision 1 and its
 amendment)
=============================================================================

Pure functions: belief's live hypotheses and the expanded model answer in, the
published hypotheses out. No board, no backend. Used by the gate and by the
offline replay (bench/replay_multi.py), so the two cannot disagree.

  model        the single agent's `merge` (model order wins). Phase 1-4
               behaviour; not handled here, the gate keeps calling `merge`.
  belief_only  belief's own top 3, exactly as `initial_claims` builds them.
               The model's answer is logged and ignored.
  tiebreak     PRIMARY (decision 1, option B). Published order = belief's.
               The model may reorder only belief's LEADERS: live cases whose
               log-odds are within BAND of the best. Cases it ranks that are in
               the band come first, in its order; the rest of the band follows
               in belief's order; everything outside the band keeps its place.
               Recomputed every tick; nothing is written to belief.
  nudge        SECONDARY arm (option A). Every tick a FRESH answer (not a
               reused one) adds STEP to its rank-1 case and STEP/2 to its
               rank-2 case, at most once per case per tick, each case's total
               capped at CAP for the episode. Published order = belief's
               log-odds + that offset. The offsets are kept on the board
               (`model_evidence`, owned by the gate), NOT added to the
               Hypothesis objects: belief's own update, retirement and the
               single agent's belief are untouched, and the offset can be
               reported and removed.

Constants (all CITED, fieldmind/agent/world_model.py):
  BAND = STEP = 0.35  STEP_SUPPORT: one matched movement triple of evidence
  CAP = 1.2           the per-tick bound update_hypotheses puts on belief

Stdlib only (copied to the device with the rest of fieldmind/).
=============================================================================
"""

from __future__ import annotations

import math

RULES = ("model", "belief_only", "tiebreak", "nudge", "hybrid")
BAND = 0.35
STEP = 0.35
CAP = 1.2
# hybrid: belief's best case counts as WEAK below this log-odds. 0 is
# confidence 0.5, i.e. belief has no net evidence FOR its best case. DESIGN
# CHOICE, made after looking at the 7 October reporting-episode replays
# (reports/multi_llm_impact_design.md); validated on dev only. The replay moved
# it over -0.5 / 0 / +0.5 (and BAND over 0.2 / 0.35 / 0.5): the margin over
# belief stayed within +0.181 to +0.191. Affects: which ticks the model leads.
WEAK = 0.0


def _lo(h) -> float:
    """Log-odds at the precision run files save (belief_ranking, 4 decimals),
    so a tie is a tie and the offline replay decides exactly as the gate does
    (unrounded sums such as -0.7875 + 1.2 vs 0.4125 differ in the last bit)."""
    return round(h.log_odds, 4)


def _belief_order(live: list) -> list:
    """rank_hypotheses order: descending confidence, ties in insertion order
    (`live` is given in insertion order)."""
    return sorted(live, key=lambda h: -h.confidence)


def _claim(h, confidence: float, supports=None, **flags) -> dict:
    """The shape initial_claims builds (fieldmind/agent/orchestrator.py)."""
    d = {"rank": 0, "cause": h.cause, "confidence": confidence,
         "supports": list(h.supports) if supports is None else list(supports),
         "case_ref": h.case_ref, "discriminator": h.discriminator}
    d.update({k: v for k, v in flags.items() if v})
    return d


def _finish(hyps: list[dict]) -> list[dict]:
    for i, h in enumerate(hyps):
        h["rank"] = i + 1
    return hyps


def belief_only(live: list) -> tuple[list[dict], dict]:
    top = _belief_order(live)[:3]
    return _finish([_claim(h, h.confidence) for h in top]), \
        {"rule": "belief_only", "differs_from_belief": False, "why": "belief order"}


def tiebreak(live: list, model_hyps: list[dict], band: float = BAND) -> tuple[list[dict], dict]:
    order = _belief_order(live)
    if not order:
        return [], {"rule": "tiebreak", "differs_from_belief": False, "why": "no belief"}
    best = max(_lo(h) for h in order)
    leaders = [h for h in order if _lo(h) >= round(best - band, 4)]
    by_ref = {h.case_ref: h for h in leaders}
    picked, cites = [], {}
    for m in model_hyps:
        ref = m.get("case_ref")
        if ref in by_ref and by_ref[ref] not in picked:
            picked.append(by_ref[ref])
            cites[ref] = m.get("supports", [])
    new = picked + [h for h in leaders if h not in picked] \
        + [h for h in order if h not in leaders]
    top = new[:3]
    belief_top = [h.case_ref for h in order[:3]]
    hyps = [_claim(h, h.confidence, cites.get(h.case_ref),
                   tiebreak=(h.case_ref in cites)) for h in top]
    differs = [h.case_ref for h in top] != belief_top
    outside = [m.get("case_ref") for m in model_hyps if m.get("case_ref") not in by_ref]
    why = ("model reordered belief's leaders" if differs else
           "model agrees with belief's order" if picked else
           "no model-ranked case among belief's leaders" if model_hyps else
           "no model answer")
    return _finish(hyps), {"rule": "tiebreak", "differs_from_belief": differs, "why": why,
                           "leaders": [h.case_ref for h in leaders],
                           "model_in_band": [h.case_ref for h in picked],
                           "model_outside_band": outside}


def add_nudges(offsets: dict, fresh_model_hyps: list[dict],
               step: float = STEP, cap: float = CAP) -> dict:
    """Offsets after this tick's FRESH answer: rank 1 +step, rank 2 +step/2,
    once per case per tick, each total capped. Returns the new offsets."""
    out = dict(offsets)
    seen = set()
    for i, m in enumerate(fresh_model_hyps[:2]):
        ref = m.get("case_ref")
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        out[ref] = min(cap, out.get(ref, 0.0) + (step if i == 0 else step / 2))
    return out


def nudge(live: list, offsets: dict) -> tuple[list[dict], dict]:
    """Belief log-odds + the model's offset; ties keep insertion order."""
    order = _belief_order(live)
    adj = {id(h): round(_lo(h) + offsets.get(h.case_ref, 0.0), 4) for h in live}
    ranked = sorted(live, key=lambda h: -adj[id(h)])
    top = ranked[:3]
    hyps = [_claim(h, round(1.0 / (1.0 + math.exp(-adj[id(h)])), 3),
                   nudged=offsets.get(h.case_ref, 0.0) > 0) for h in top]
    differs = [h.case_ref for h in top] != [h.case_ref for h in order[:3]]
    return _finish(hyps), {"rule": "nudge", "differs_from_belief": differs,
                           "why": "model offsets reorder belief" if differs else
                           ("offsets do not change belief's top 3" if offsets else "no offsets"),
                           "offsets": {k: round(v, 4) for k, v in sorted(offsets.items())}}


def flat_case_ids(cases: list[dict]) -> frozenset:
    """Library cases whose signature has no moving triple (every direction
    FLAT): RCA-09, RCA-10, RCA-15 in today's library. Read from the case data,
    not listed by hand."""
    out = set()
    for c in cases:
        moving = False
        for k, v in (c.get("signature") or {}).items():
            d = v[0] if isinstance(v, list) else k.split("|")[1]
            moving = moving or d != "FLAT"
        if not moving:
            out.add(c["case_id"])
    return frozenset(out)


def regime(live: list, band: float = BAND, weak: float = WEAK) -> str:
    """weak: belief's best log-odds < weak; tie: best minus second < band;
    clear: otherwise (or no live case)."""
    los = sorted((_lo(h) for h in live), reverse=True)
    if not los:
        return "clear"
    if los[0] < weak:
        return "weak"
    if len(los) > 1 and round(los[0] - los[1], 4) < band:
        return "tie"
    return "clear"


def hybrid(live: list, model_hyps: list[dict], offsets: dict, flat_ids,
           band: float = BAND, weak: float = WEAK) -> tuple[list[dict], dict]:
    """Belief weak or tied: the model's ranking of belief's live, non-flat cases
    goes first (in the model's order), the rest follow in nudge order. Belief
    has a clear leader: `nudge`. The offsets must be accumulated without flat
    picks (`add_nudges(offsets, [m for m in fresh if not flat])`).

    Why the flat guard is part of the rule: without it, on the 7 October board
    answers the model put a flat case first on most tie ticks of B01 and D01,
    and the unguarded rule fell to 0.312 on B01 against belief's 0.555.
    Only belief's live cases can be published; nothing is written to belief."""
    reg = regime(live, band, weak)
    base = sorted(live, key=lambda h: -round(_lo(h) + offsets.get(h.case_ref, 0.0), 4))
    by_ref = {h.case_ref: h for h in live}
    picked = []
    for m in model_hyps:
        h = by_ref.get(m.get("case_ref"))
        if h is not None and h.case_ref not in flat_ids and h not in picked:
            picked.append(h)
    lead = reg != "clear" and bool(picked)
    order = picked + [h for h in base if h not in picked] if lead else base
    top = order[:3]
    hyps = [_claim(h, round(1.0 / (1.0 + math.exp(-round(_lo(h) + offsets.get(h.case_ref, 0.0), 4))), 3),
                   model_first=lead and h in picked,
                   nudged=offsets.get(h.case_ref, 0.0) > 0) for h in top]
    belief_top = [h.case_ref for h in _belief_order(live)[:3]]
    why = ("belief has a clear leader: nudge" if reg == "clear" else
           f"belief {reg}: model's ranking first" if lead else
           f"belief {reg}, but no usable model case (none, flat or not live): nudge")
    return _finish(hyps), {"rule": "hybrid", "regime": reg, "model_led": lead,
                           "differs_from_belief": [h.case_ref for h in top] != belief_top,
                           "why": why,
                           "model_flat_ignored": [m.get("case_ref") for m in model_hyps
                                                  if m.get("case_ref") in flat_ids],
                           "offsets": {k: round(v, 4) for k, v in sorted(offsets.items())}}
