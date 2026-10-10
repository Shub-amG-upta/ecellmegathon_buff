"""
=============================================================================
 L1b  --  SYMBOLIZER  (Plan §4, feeds §6.2)
=============================================================================

Turns the Fact list into two things:

  1. an EVIDENCE PACKET -- the compressed, token-cheap text the LLM will see.
     The language model NEVER sees raw numbers. It sees facts.

  2. a SIGNATURE -- a sparse dict over (tag, direction, magnitude_band)
     triples, used for case matching in L3.

Why symbolize at all: 720 raw samples x 6 tags is ~4300 numbers per tick. At
roughly one token per number that is a 4 k prefill before we have said
anything. The same information as facts is 150-300 tokens. On the NPU, where
prefill dominates, that is the difference between fitting in a 1024 static
shape and not.
=============================================================================
"""

from __future__ import annotations

from ..schemas import Fact

# Magnitude bands. Deliberately coarse: the discriminating power lives in
# WHICH tags moved together, not in the third decimal place of how fast.
BANDS = ["SLOW", "MED", "FAST"]

# Per-tag band edges in tag units per minute: (deadband, slow/med, med/fast).
#
#   |slope| < deadband        -> FLAT
#   deadband..lo              -> SLOW ; lo..hi -> MED ; >= hi -> FAST
#
# CONFIG IS AUTHORITATIVE: configs/base.yaml checks.bands. This dict is only
# the device fallback for a direction_and_band() call made without bands (the
# unit tests) and test_checks.py asserts it equals the config.
#
# Stage 6 moved these into the config and tried a criterion-based
# re-derivation; mock Q2_top1 regressed so the VALUES were reverted to this
# Stage-5 set (see the config comment + reports/stage6_band_edges.md).
BAND_EDGES = {
    "drum_level":      (0.03, 0.15, 0.50),     # %/min
    "feed_water_flow": (0.05, 0.30, 1.00),     # TPH/min
    "steam_flow":      (0.05, 0.30, 1.00),
    "drum_pressure":   (0.004, 0.02, 0.08),    # kg/cm2/min
    "bed_temp_avg":    (0.03, 0.15, 0.50),     # degC/min
    "ms_temperature":  (0.04, 0.20, 0.80),
}


def direction_and_band(tag: str, slope: float,
                       bands: dict | None = None) -> tuple[str, str]:
    """Map a numeric slope onto (UP|DOWN|FLAT, SLOW|MED|FAST|-).

    `bands` is configs/base.yaml checks.bands (list-per-tag). Passing None is
    ONLY for unit tests and uses the module BAND_EDGES mirror; the runtime
    always passes the config bands (build_signature enforces it). If `bands` is
    supplied it must contain `tag` -- a partial dict is a config error and
    raises rather than silently falling back to the mirror, which is how the
    hard-coded edges went stale in the first place.
    """
    if bands is None:
        edges = BAND_EDGES[tag]
    else:
        if tag not in bands:
            raise KeyError(f"checks.bands is missing an entry for {tag!r}")
        edges = bands[tag]
    dead, lo, hi = edges
    mag = abs(slope)
    if mag < dead:
        return "FLAT", "-"
    band = "FAST" if mag >= hi else ("MED" if mag >= lo else "SLOW")
    return ("UP" if slope > 0 else "DOWN"), band


def build_signature(facts: list[Fact], slopes: dict[str, float],
                    bands: dict | None = None) -> dict:
    """Sparse signature over (tag, direction, band) -> weight.

    Two sources feed it:
      - the measured slope of every trusted tag (including the FLAT ones --
        absence of movement is evidence, see case RCA-01 where a flat bed
        temperature is what rules the heat side out)
      - a weight bump for any tag named in an ALARM/CRITICAL fact

    `bands` (configs/base.yaml checks.bands) is REQUIRED on the runtime path.
    None is a unit-test-only shortcut to the BAND_EDGES mirror.
    """
    if bands is not None and not bands:
        raise ValueError("build_signature: checks.bands is empty -- the "
                         "descriptor bands must come from config, not the "
                         "l1_symbolize mirror")
    sig: dict[tuple[str, str, str], float] = {}
    for tag, slope in slopes.items():
        d, b = direction_and_band(tag, slope, bands)
        # FLAT observations carry less weight than movement, but not zero.
        # FLAT is weak evidence: on a healthy plant nearly everything is flat,
        # so a case is not distinguished by predicting flatness. Movement is
        # what discriminates, so it carries roughly three times the weight.
        sig[(tag, d, b)] = 0.35 if d == "FLAT" else 1.0

    for f in facts:
        if f.severity in ("ALARM", "CRITICAL"):
            for tag in f.tags:
                for key in list(sig):
                    if key[0] == tag:
                        sig[key] = min(1.0, sig[key] + 0.2)

    # ---- balance-derived triples ------------------------------------
    # A slope-only signature cannot tell a feed-path fault from a leak: in
    # both, level moves and feed moves. What separates them is the SIGN OF
    # THE WATER BALANCE RESIDUAL, which is a relationship between levels, not
    # a slope. Encoding it as a pseudo-tag lets the same weighted-Jaccard
    # matcher use it with no special-casing.
    for f in facts:
        if f.check != "BALANCE":
            continue
        if "SUSPENDED" in f.detail:
            continue
        if set(f.tags) >= {"feed_water_flow", "steam_flow"}:
            direction = "DEFICIT" if f.value < 0 else "SURPLUS"
            band = "FAST" if abs(f.value) > 4.0 else "MED"
            sig[("water_balance", direction, band)] = 1.0
        elif "drum_pressure" in f.tags and "heat input short" in f.detail:
            sig[("energy_balance", "HEAT_SHORT", "-")] = 1.0
        elif "bed_temp_avg" in f.tags and "accumulating" in f.detail:
            sig[("energy_balance", "HEAT_ACCUMULATING", "-")] = 1.0
    return sig


def evidence_packet(facts: list[Fact], max_facts: int = 12) -> str:
    """The exact text block that goes into the prompt under FACTS.

    Sorted by severity so that if the packet has to be truncated at a tight
    context length (degradation ladder rung 2), what survives is the part that
    matters. Truncation is announced, never silent.
    """
    order = {"CRITICAL": 0, "ALARM": 1, "WATCH": 2, "INFO": 3}
    ranked = sorted(facts, key=lambda f: (order.get(f.severity, 9), f.id))
    kept = ranked[:max_facts]
    lines = [f"{f.id} [{f.check}/{f.severity}] {f.detail}" for f in kept]
    if len(ranked) > len(kept):
        lines.append(f"({len(ranked) - len(kept)} lower-severity facts omitted "
                     f"for context budget)")
    return "\n".join(lines) if lines else "(no facts above threshold)"


def headline(facts: list[Fact]) -> str:
    """One sentence for the engineer, built from the two most severe facts.

    Written by code, not by the model. Even at degradation ladder rung 5 --
    LLM off entirely -- the engineer still gets this line. That is the safe
    floor (Plan §11.5).
    """
    order = {"CRITICAL": 0, "ALARM": 1, "WATCH": 2, "INFO": 3}
    ranked = [f for f in sorted(facts, key=lambda f: order.get(f.severity, 9))
              if f.severity != "INFO"]
    if not ranked:
        return "All six parameters within band; balances close."
    parts = [f.detail.split(":")[0] for f in ranked[:2]]
    return "; ".join(parts) + "."
