"""
=============================================================================
 PLANT SIDES  (multi-agent Phase 3, "Split by side"; plan p.5 agent table)
=============================================================================

Pure functions: which facts, findings, cases, signature triples and
note-facts belong to the water side and to the heat side. No board, no
backend. Retrieval and belief are NOT split (Phase 3 decision 4 and the
"Sides" section of reports/multi_phase3_split.md): these functions only decide
what each side's diagnostician is shown and when a side is active.

  water   drum_level, feed_water_flow, steam_flow        + water_balance
  heat    bed_temp_avg, drum_pressure, ms_temperature,
          steam_flow (as load)                           + energy_balance

Stdlib only (copied to the device with the rest of fieldmind/).
=============================================================================
"""

from __future__ import annotations

SIDES = ("water", "heat")

# DESIGN, CITED: plan p.5 ("Heat diagnostician ... bed temperature, pressure,
# steam temperature, with steam flow as load").
SIDE_TAGS = {
    "water": frozenset({"drum_level", "feed_water_flow", "steam_flow"}),
    "heat": frozenset({"bed_temp_avg", "drum_pressure", "ms_temperature", "steam_flow"}),
}
PSEUDO = {"water_balance": "water", "energy_balance": "heat"}
# CITED: fieldmind/agent/l1_checks._balances, `water_tags`. A BALANCE fact whose
# tags all lie in this set is the water balance (or its SUSPENDED notice, which
# names only the untrusted water tags); every other BALANCE fact is energy.
_WATER_BALANCE_TAGS = frozenset({"feed_water_flow", "steam_flow", "drum_level"})
# CITED: fieldmind/agent/l1_checks._patterns. A PATTERN fact's tags include the
# CONTEXT tags its condition reads (steam and pressure flat; bed untouched), so
# its side is the side the pattern names, by its tag set:
#   'energy accumulating in bed'  bed, steam, pressure  -> heat
#   'water side only'             level, bed            -> water
#   'heat side steady' (INFO)     bed, pressure         -> heat
# An unknown pattern falls back to its tags; a test fails if L1 emits one.
PATTERN_SIDE = {
    frozenset({"bed_temp_avg", "steam_flow", "drum_pressure"}): "heat",
    frozenset({"drum_level", "bed_temp_avg"}): "water",
    frozenset({"bed_temp_avg", "drum_pressure"}): "heat",
}
SEV_RANK = {"CRITICAL": 0, "ALARM": 1, "WATCH": 2, "INFO": 3}


def _sides_of_tags(tags) -> set[str]:
    tags = set(tags)
    return {s for s in SIDES if tags & SIDE_TAGS[s]}


def fact_sides(fact) -> set[str]:
    """A BALANCE fact is water when all its tags are water-balance tags (the
    balance itself, or "water balance SUSPENDED - untrusted drum_level"),
    otherwise heat. Any other fact belongs to every side whose tags it names
    (steam_flow: both)."""
    return _sides_of_check(fact.check, set(fact.tags))


def _sides_of_check(check: str, tags: set) -> set[str]:
    if check == "BALANCE":
        return {"water"} if tags <= _WATER_BALANCE_TAGS else {"heat"}
    if check == "PATTERN" and frozenset(tags) in PATTERN_SIDE:
        return {PATTERN_SIDE[frozenset(tags)]}
    return _sides_of_tags(tags)


def facts_for_side(facts, side: str) -> list:
    return [f for f in facts if side in fact_sides(f)]


def finding_sides(finding) -> set[str]:
    """Findings are keyed `CHECK:tag|tag` (world_model.update_findings)."""
    check, _, tags = finding.signature_key.partition(":")
    return _sides_of_check(check, set(filter(None, tags.split("|"))))


def active_sides(facts, findings) -> list[str]:
    """Sides with a non-INFO fact or an open finding, in SIDES order. Empty
    when neither side has evidence (decision 5: then both sides are asked)."""
    act = set()
    for f in facts:
        if f.severity != "INFO":
            act |= fact_sides(f)
    for fnd in findings:
        if not fnd.resolved:
            act |= finding_sides(fnd)
    return [s for s in SIDES if s in act]


def sides_to_run(facts, findings) -> list[str]:
    """The active sides; both when neither has evidence (decision 5)."""
    return active_sides(facts, findings) or list(SIDES)


def case_sides(case: dict) -> set[str]:
    """The sides a case's MOVING (non-FLAT) signature triples touch. A case
    that moves nothing (all-FLAT: RCA-09, 10, 15) belongs to both."""
    out = set()
    for k, v in (case.get("signature") or {}).items():
        if isinstance(v, list):
            if v[0] != "FLAT":
                out |= _sides_of_tags([k])
        else:
            tag, d, _ = k.split("|")
            if d == "FLAT":
                continue
            out |= {PSEUDO[tag]} if tag in PSEUDO else _sides_of_tags([tag])
    return out or set(SIDES)


def cases_for_side(cases, side: str) -> list:
    return [c for c in cases if side in case_sides(c)]


def signature_for_side(signature: dict, side: str) -> dict:
    """The side's (tag, direction, band) triples, pseudo-tags included."""
    return {t: w for t, w in signature.items()
            if (PSEUDO.get(t[0]) == side) or t[0] in SIDE_TAGS[side]}


def note_sides(note: dict) -> set[str]:
    """A raw note (its metadata tags) goes to the sides of its tags; a note
    with no tag goes to both."""
    return _sides_of_tags(note.get("tags") or []) or set(SIDES)


def notefact_sides(nf: dict) -> set[str]:
    """A note-fact goes to the sides of the tags among its subjects; one that
    names no tag (equipment only, or nothing) goes to both."""
    return _sides_of_tags(s for s, _ in nf.get("pairs", [])) or set(SIDES)


def _id_key(fid: str):
    """F2 before F10 (natural order of the local id)."""
    head, _, num = fid.rpartition("F")
    return (head, int(num)) if num.isdigit() else (fid, 0)


def other_side_line(facts, side: str) -> str:
    """One line about the OTHER side, written by code: its most severe
    non-INFO fact, cut at its first ':' (the same cut l1_symbolize.headline
    makes, so the line stays near the plan's ~30 tokens), or `steady`."""
    other = next(s for s in SIDES if s != side)
    ranked = sorted((f for f in facts_for_side(facts, other) if f.severity != "INFO"),
                    key=lambda f: (SEV_RANK.get(f.severity, 9), _id_key(f.id)))
    body = (f"[{ranked[0].check}/{ranked[0].severity}] {ranked[0].detail.split(':')[0]}"
            if ranked else "steady")
    return f"{other} side: {body}"


def primary_side(facts, findings=()) -> str:
    """The side with the more severe evidence, non-INFO facts and open
    findings (decision 1); a tie goes to water."""
    best = {}
    for s in SIDES:
        sev = [SEV_RANK.get(f.severity, 9) for f in facts_for_side(facts, s)
               if f.severity != "INFO"]
        sev += [SEV_RANK.get(f.severity, 9) for f in findings
                if not f.resolved and s in finding_sides(f)]
        best[s] = min(sev, default=9)
    return "heat" if best["heat"] < best["water"] else "water"


def side_fingerprint(signature: dict, findings, notefacts, side: str,
                     case_ids=()) -> tuple:
    """What a side's diagnosis depends on (plan: "its signature, its open
    findings or its note-facts"). Equal fingerprints = unchanged evidence.
    A finding counts with its severity, so an escalation is a change; only the
    side's own open findings and its own note-facts (`notefacts`: the board's
    checked note-fact dicts) count. `case_ids`: the cases the side is shown
    (phase review, commit 3: without them a re-used answer could rank a case
    list retrieval no longer returns)."""
    return (frozenset(signature_for_side(signature, side)),
            frozenset((f.signature_key, f.severity) for f in findings
                      if not f.resolved and side in finding_sides(f)),
            frozenset(nf["id"] for nf in notefacts
                      if nf.get("status") == "ok" and side in notefact_sides(nf)),
            frozenset(case_ids))
