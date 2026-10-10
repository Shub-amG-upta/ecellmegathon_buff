"""
=============================================================================
 L7  --  MEMORY WRITE and the PROMOTION GATE  (Plan §8)
=============================================================================

Three things adapt. MODEL WEIGHTS ARE NOT AMONG THEM -- there is no on-device
fine-tuning. 'Self-learning' means the retrievable corpus and the retrieval
weights change.

  per-tag baselines   rolling robust stats, FROZEN ON SUSPICION (world_model.py)
  experience store    closed episodes, admitted only through the gate below
  retrieval weights   cases that led to correct diagnoses get up-weighted,
                      bounded to [0.5, 2.0] and NEVER removed -- a case
                      suppressed to zero could never be retrieved again, which
                      is an unrecoverable failure mode

The gate exists because the obvious failure is the agent's own guess re-entering
as evidence, after which it converges happily on its favourite answer.
=============================================================================
"""

from __future__ import annotations

from ..schemas import WorldModel

WEIGHT_FLOOR, WEIGHT_CEIL = 0.5, 2.0


def promote(wm: WorldModel, episode_outcome: dict, experience, cases) -> dict:
    """Four-stage gate (Plan §8.3). Returns what happened and why.

      1. CLOSURE     episode reached a terminal state, else discard
      2. OUTCOME     a recorded outcome exists, else store as unverified
      3. SIGN-OFF    an engineer confirmed the root cause, else unverified
      4. NOVELTY     signature not already covered, else merge and increment
    """
    result = {"admitted": False, "provenance": None, "reason": ""}

    # -- 1. closure --
    if not episode_outcome.get("closed"):
        result["reason"] = "episode did not reach a terminal state"
        return result

    # -- 2 & 3. outcome and human sign-off --
    root_cause = episode_outcome.get("actual_root_cause")
    if not root_cause:
        result["reason"] = "no recorded outcome"
        provenance = "agent_unverified"
    elif episode_outcome.get("engineer_signed_off"):
        provenance = "agent_confirmed"
    else:
        provenance = "agent_unverified"

    signature = episode_outcome.get("signature", {})

    # -- 4. novelty: merge into an existing case rather than duplicating --
    existing = cases.match(signature, k=1)
    if existing and existing[0]["raw_score"] > 0.85:
        case = next(c for c in cases.cases
                    if c["case_id"] == existing[0]["case_id"])
        case["occurrences"] = case.get("occurrences", 1) + 1
        result.update(admitted=False, provenance=provenance,
                      reason=f"merged into {case['case_id']} "
                             f"(now {case['occurrences']} occurrences)")
        return result

    experience.add({
        "case_id": f"EXP-{len(experience.records) + 1:03d}",
        "title": f"Observed in {wm.episode_id}",
        "signature": signature,
        "root_cause": root_cause or "unconfirmed",
        "actions": episode_outcome.get("actions", []),
        "provenance": provenance,
        "weight": 1.0,
        "occurrences": 1,
        "episode_id": wm.episode_id,
    })
    result.update(admitted=True, provenance=provenance, reason="new signature")
    return result


def update_retrieval_weights(cases, case_id: str, was_correct: bool) -> float:
    """Bounded multiplicative update. Never removes a case."""
    case = next((c for c in cases.cases if c["case_id"] == case_id), None)
    if case is None:
        return 0.0
    w = case.get("weight", 1.0) * (1.05 if was_correct else 0.95)
    case["weight"] = max(WEIGHT_FLOOR, min(WEIGHT_CEIL, w))
    return case["weight"]
