"""
=============================================================================
 WORLD MODEL UPDATE  (Plan §5)
=============================================================================

This is what makes the system an agent rather than a classifier: belief is
carried from tick to tick and updated by an explainable rule, not a learned
one.

Four update rules (Plan §5.1):
  1. UP    when a newly observed fact is in the hypothesis's expected evidence
  2. DOWN  when a fact the hypothesis PREDICTS should be present is ABSENT
           -- absence of expected evidence is information, and this is the
           part most systems skip
  3. DOWN HARD when a fact directly contradicts it
  4. RETIRE below a floor, so the agent does not silently keep a dead idea alive

Log-odds accumulation with bounded per-tick increments. Close enough to
Bayesian to defend in the writeup, and it cannot blow up.
=============================================================================
"""

from __future__ import annotations

import math

from ..kb.stores import CaseLibrary
from ..schemas import (Baseline, EquipmentState, Event, Fact, Finding,
                       Hypothesis, WorldModel, TAGS)

# Bounded increments. The bound is the whole safety property: without it, a
# fault that persists for 300 ticks drives confidence to 1.0 and the agent
# stops being able to change its mind.
STEP_SUPPORT = 0.35
STEP_ABSENT = -0.20
STEP_CONTRA = -0.90
CLAMP = 4.0            # log-odds ceiling ~= confidence 0.982
RETIRE_BELOW = 0.08


def new_world_model(episode_id: str, equipment: list[str]) -> WorldModel:
    wm = WorldModel(episode_id=episode_id)
    wm.equipment = {e: EquipmentState(name=e) for e in equipment}
    wm.baselines = {t: Baseline(tag=t) for t in TAGS}
    return wm


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# =======================================================================
#  Findings: deduplicated so the agent reports a CHANGE OF STATE, not the
#  same sentence 400 times (Plan §9, scored under T5).
# =======================================================================

def update_findings(wm: WorldModel, facts: list[Fact], tick: int) -> None:
    seen_keys = set()
    for f in facts:
        if f.severity == "INFO":
            continue
        key = f"{f.check}:{'|'.join(sorted(f.tags))}"
        seen_keys.add(key)
        existing = next((x for x in wm.open_findings if x.signature_key == key), None)
        if existing:
            existing.last_tick = tick          # extend, do not re-announce
            existing.severity = f.severity
            existing.detail = f.detail
            existing.resolved = False
        else:
            wm.open_findings.append(Finding(
                id=f"FND{len(wm.open_findings) + 1}", signature_key=key,
                first_tick=tick, last_tick=tick,
                severity=f.severity, detail=f.detail))
            wm.timeline.append(Event(tick, "FINDING_OPENED", f.detail))

    # A finding whose fact stopped firing is resolved, but kept in the list so
    # the timeline still shows it happened.
    for fnd in wm.open_findings:
        if fnd.signature_key not in seen_keys and not fnd.resolved:
            fnd.resolved = True
            wm.timeline.append(Event(tick, "FINDING_RESOLVED", fnd.detail))


# =======================================================================
#  Baselines, with the freeze that makes family E survivable
# =======================================================================

def update_baselines(wm: WorldModel, window, tick: int) -> None:
    """Rolling robust statistics over CONFIRMED-NORMAL periods only.

    THE GUARD: a tag with an open finding stops updating its baseline until
    the episode closes as normal. Without this, family E (slow drift, nothing
    ever crosses a limit) is undetectable by construction -- the adaptive
    baseline simply learns the drift as the new normal (Plan §8.2).
    """
    suspicious_tags = set()
    for fnd in wm.open_findings:
        if not fnd.resolved:
            suspicious_tags.update(fnd.signature_key.split(":")[1].split("|"))

    for tag in TAGS:
        b = wm.baselines[tag]
        b.frozen = tag in suspicious_tags
        if b.frozen:
            continue
        s = window.series(tag)
        if len(s) < 60:
            continue
        recent = s[-60:]
        med = sorted(recent)[len(recent) // 2]
        mad = sorted([abs(x - med) for x in recent])[len(recent) // 2]
        # Exponential forgetting so the baseline tracks genuine load changes
        # over hours without chasing minute-to-minute noise.
        a = 0.02
        b.median = med if b.n == 0 else (1 - a) * b.median + a * med
        b.mad = mad if b.n == 0 else (1 - a) * b.mad + a * mad
        b.n += 1


# =======================================================================
#  Hypothesis belief update
# =======================================================================

def _fact_ids(facts: list[Fact], tag: str) -> list[str]:
    """Non-INFO fact ids that speak about `tag`. The two balance pseudo-tags in
    the signature (l1_symbolize.build_signature) map to BALANCE facts: water
    balance is the one naming feed and steam flow, energy balance the rest."""
    water = {"feed_water_flow", "steam_flow"}
    if tag == "water_balance":
        sel = [f for f in facts if f.check == "BALANCE" and water <= set(f.tags)]
    elif tag == "energy_balance":
        sel = [f for f in facts if f.check == "BALANCE" and not water <= set(f.tags)]
    else:
        sel = [f for f in facts if tag in f.tags]
    return [f.id for f in sel if f.severity != "INFO"]


def update_hypotheses(wm: WorldModel, facts: list[Fact], signature: dict,
                      candidates: list[dict], tick: int,
                      retire_after: int | None = None) -> None:
    """candidates: retrieved cases, each with expected/contradicting signatures.

    Evidence is compared as full (tag, direction, band) TRIPLES against the tick
    `signature`, exactly as CaseLibrary.match does (known bug 6: this used to
    compare tag names only, so a flat bed -- which RCA-01 PREDICTS -- was charged
    as a contradiction of RCA-01's "bed falling" signature).

    `retire_after` (agent.belief.retire_after_ticks): a live hypothesis that no
    retrieved case has carried for this many ticks is retired and the retirement
    logged as an Event. Without it a hypothesis that was retrieved once keeps
    its log-odds for the rest of the episode, and look-alike cases that tie
    (RCA-09 / RCA-10 / RCA-15 on an all-FLAT signature) stay ranked forever.
    None disables it (unit tests only).

    Note that confidences are NOT normalised to sum to 1. Two faults at once is
    a real scenario (Plan §9) and forcing a simplex would make the second one
    impossible to express.
    """
    for cand in candidates:
        cause = cand.get("root_cause") or cand.get("cause", "unknown")
        h = next((x for x in wm.hypotheses if x.cause == cause), None)
        if h is None:
            h = Hypothesis(cause=cause, case_ref=cand.get("case_id"),
                           first_tick=tick,
                           discriminator=cand.get("discriminating_evidence", ""))
            wm.hypotheses.append(h)
        h.retired = False
        h.last_retrieved_tick = tick

        expected = CaseLibrary._to_triples(cand.get("signature", {}))

        delta = 0.0
        supports, contradicts = [], []

        for triple, w in expected.items():
            # `w` is the case triple's weight, the same one match() uses: FLAT
            # 0.35, movement and balance pseudo-triples 1.0. Predicting flatness
            # is weak evidence, so a FLAT triple moves belief by a third of what
            # a movement triple does, in both directions.
            if triple in signature:
                # rule 1: expected evidence observed (a FLAT triple observed
                # counts: RCA-01 expects a flat bed, and the signature has it)
                delta += w * STEP_SUPPORT
                supports.extend(_fact_ids(facts, triple[0]))
            else:
                # rule 2: expected evidence ABSENT -- the part most systems skip
                delta += w * STEP_ABSENT

        # rule 3: direct contradiction -- the SAME function retrieval uses
        for triple in CaseLibrary.contradiction_hits(cand, signature):
            delta += STEP_CONTRA
            contradicts.extend(_fact_ids(facts, triple[0]))

        # Bound the per-tick movement regardless of how many tags matched.
        delta = max(-1.2, min(1.2, delta))
        h.log_odds = max(-CLAMP, min(CLAMP, h.log_odds + delta))
        h.confidence = round(_sigmoid(h.log_odds), 3)
        h.supports = sorted(set(supports))[:6]
        h.contradicts = sorted(set(contradicts))[:4]

    # rule 5: retire the stale, but record it. Ticks are counted on the episode
    # clock, so QUIET ticks (where this function is not called) still age a
    # hypothesis; the retirement lands on the next tick that runs the update.
    if retire_after is not None:
        for h in wm.hypotheses:
            if not h.retired and tick - h.last_retrieved_tick >= retire_after:
                h.retired = True
                wm.timeline.append(Event(
                    tick, "HYP_RETIRED",
                    f"{h.case_ref or h.cause} not retrieved for "
                    f"{tick - h.last_retrieved_tick} ticks"))

    # rule 4: retire the dead, but record it
    for h in wm.hypotheses:
        if not h.retired and h.confidence < RETIRE_BELOW:
            h.retired = True
            wm.timeline.append(Event(tick, "HYP_RETIRED",
                                     f"{h.cause} fell below floor"))


def rank_hypotheses(wm: WorldModel, top: int = 3) -> list[Hypothesis]:
    live = [h for h in wm.hypotheses if not h.retired]
    return sorted(live, key=lambda h: -h.confidence)[:top]


def belief_ranking(wm: WorldModel) -> list[dict]:
    """Live hypotheses by descending log-odds, for telemetry. Python's sort is
    stable, so ties keep insertion order (the same tiebreak rank_hypotheses has)."""
    live = sorted((h for h in wm.hypotheses if not h.retired),
                  key=lambda h: -h.log_odds)
    return [{"case_ref": h.case_ref, "cause": h.cause,
             "log_odds": round(h.log_odds, 4), "confidence": h.confidence}
            for h in live]


def shown_confidences(hypotheses: list[dict], live: list) -> list[float]:
    """DISPLAY-ONLY confidence for the shown hypotheses (Session 1b, fix (c)).

    shown_i = min(c_i, sigmoid(l_i - r_i)) where c_i is the hypothesis's
    decision confidence, l_i its belief log-odds and r_i the best rival's
    log-odds among the live hypotheses (0 when there is no rival, so a lone
    hypothesis shows exactly sigmoid(l_i) = c_i). A tie shows 0.5 whatever k
    is (a pairwise margin, not a probability over the tied set). The `min`
    means the margin can only lower the shown value. A hypothesis with no
    belief entry (a model-only idea) keeps its own confidence.

    NOTHING THAT DECIDES MAY READ THE RESULT: the verifier trigger, the
    verifier and diagnostician prompts, retirement, ordering and the gate all
    keep `confidence`. See reports/phase0a_session1b_measurement.md, Step 5.
    """
    out = []
    for h in hypotheses:
        c = float(h.get("confidence", 0.0))
        own = next((x for x in live if x.cause == h.get("cause")), None)
        if own is None and h.get("case_ref"):
            own = next((x for x in live if x.case_ref == h.get("case_ref")), None)
        if own is None:
            out.append(round(c, 3))
            continue
        rivals = [x.log_odds for x in live if x is not own]
        rival = max(rivals) if rivals else 0.0
        out.append(round(min(c, _sigmoid(own.log_odds - rival)), 3))
    return out


# =======================================================================
#  State machine
# =======================================================================

def derive_state(facts: list[Fact], triage_level: str, wm: WorldModel) -> str:
    """The reported plant state. Derived from FACTS, never from the model.

    This is deliberate: the state is what drives escalation, and escalation is
    a safety-adjacent decision. A language model does not get a vote.
    """
    sev = {f.severity for f in facts}
    if "CRITICAL" in sev or triage_level == "URGENT":
        return "TRIP_IMMINENT" if triage_level == "URGENT" else "ALARM"
    if "ALARM" in sev:
        return "ALARM"
    # DEGRADED means the AGENT is degraded (instrument lost, LLM unavailable),
    # not the plant. Kept distinct from DEVIATION on purpose.
    if wm.degraded_mode or len(wm.trusted_tags) < len(TAGS):
        return "DEGRADED"
    if "WATCH" in sev:
        return "DEVIATION"
    return "NORMAL"
