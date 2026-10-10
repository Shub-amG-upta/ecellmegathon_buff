"""
=============================================================================
 L2  --  TRIAGE, THE COST CONTROLLER  (Plan §4.4)
=============================================================================

Decides whether the expensive stages run at all. A small decision table over
the fact list -- NOT a model.

    QUIET        L0-L2 only. Emit NORMAL. No LLM.
    WATCH        Retrieval + Diagnostician. Verifier skipped.
    INVESTIGATE  Full chain including Verifier.
    URGENT       Full chain, shortest context, actions first.

On the normal episodes this should keep the LLM idle for the large majority
of ticks. The fraction of ticks that invoke a model (metric S4) is itself a
headline result: it is the difference between an agent that could run all
shift on a battery and one that could not.

Expect this to be the single biggest energy lever in the sweep (Plan §11.4).
=============================================================================
"""

from __future__ import annotations

from ..schemas import Fact, WorldModel


def triage(facts: list[Fact], wm: WorldModel, cfg: dict) -> tuple[str, str]:
    """Returns (level, reason). The reason string is logged so that a sweep
    over the thresholds can be explained afterwards, not just plotted."""
    sev = {f.severity for f in facts}
    thr = cfg.get("triage", {})

    # --- URGENT: a CRITICAL fact, or a trip predicted inside the horizon ---
    if "CRITICAL" in sev:
        return "URGENT", "critical fact present"
    if _trip_predicted(facts, wm, thr.get("trip_horizon_min", 5.0)):
        return "URGENT", "trip predicted within horizon"

    # --- INVESTIGATE: an ALARM fact, or a fault signature we have not seen ---
    if "ALARM" in sev:
        return "INVESTIGATE", "alarm-severity fact present"
    if _new_signature(facts, wm):
        return "INVESTIGATE", "new fault signature this episode"

    # --- WATCH: something moving, or an unresolved finding still open ---
    if "WATCH" in sev:
        return "WATCH", "watch-severity fact present"
    if any(not f.resolved for f in wm.open_findings):
        return "WATCH", "open finding not yet resolved"

    # --- QUIET: nothing is happening. This is the common case, and keeping
    #     it genuinely free is the entire cost argument.
    return "QUIET", "no facts above INFO and no open findings"


def _trip_predicted(facts: list[Fact], wm: WorldModel, horizon_min: float) -> bool:
    """Linear extrapolation of drum level and bed temperature to their trip
    thresholds. Crude on purpose -- this is a gate on how much compute to
    spend, not a diagnosis, and a fancier predictor here would be spending
    compute to decide whether to spend compute."""
    for f in facts:
        if f.check != "RATE":
            continue
        if "drum_level" in f.tags and f.value < 0:
            level = wm.residuals.get("last_drum_level")
            if level is not None and (level - 10.0) / abs(f.value) < horizon_min:
                return True
        if "bed_temp_avg" in f.tags and f.value > 0:
            bed = wm.residuals.get("last_bed_temp_avg")
            if bed is not None and (940.0 - bed) / max(f.value, 1e-6) < horizon_min:
                return True
    return False


def _new_signature(facts: list[Fact], wm: WorldModel) -> bool:
    """Has a fact signature appeared that is not already an open finding?

    This is what stops the same alarm re-triggering the full chain every tick
    for 400 ticks (alarm flood, scored under T5).
    """
    known = {f.signature_key for f in wm.open_findings}
    for f in facts:
        if f.severity in ("ALARM", "CRITICAL"):
            key = f"{f.check}:{'|'.join(sorted(f.tags))}"
            if key not in known:
                return True
    return False
