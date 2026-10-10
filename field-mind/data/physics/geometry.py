"""
=============================================================================
 STEAM-DRUM GEOMETRY  --  where the water-balance constant K comes from
=============================================================================

The old simulator asserted  K_LEVEL = 0.22 %/min per TPH  with no source.
This module derives K from drum geometry and IF97 water density instead, and
lets it land wherever the geometry puts it -- it is NOT tuned back to 0.22.
(0.22 needs a ~1045 mm level span on a 1.4 m x 7 m drum; real drum level
transmitter spans are 300-600 mm, which give K ~ 0.4-0.8.  See
data/physics/DERIVATIONS.md section 3.)

Consequence, flagged in the before/after report: K comes out ~2.7x the old
value, so the water side (families A and B) responds ~2.7x faster for the
same driver schedule, and configs/base.yaml's drum_level rate threshold and
episode_build's DEVIATION band were tuned against the old (wrong) physics.
No threshold is retuned here.

NOTE for DERIVATIONS.md: K_level() uses the free-surface area at NWL
regardless of the actual level.  The water-line chord narrows ~6 % at 20 %
level, so the true K is mildly level-dependent; the fixed nominal value used
by the L1 check is a stated approximation.

Provenance tags on every constant:  DERIVED | CITED | STANDARD | ASSUMED
=============================================================================
"""
from __future__ import annotations

import math

from data.physics.steam_tables import kgfcm2g_to_Pa, rho_f_kg_m3

# --- drum dimensions ------------------------------------------------------
# ASSUMED, ranges from AFBC bi-drum design practice for a ~60-70 TPH unit.
# Each affects K linearly (K ~ 1 / (ID * L * span)).
DRUM_ID_M = 1.30           # ASSUMED  (range 1.1-1.5 m)
DRUM_LENGTH_M = 6.50       # ASSUMED  tangent-to-tangent (range 5.5-8.0 m)
LEVEL_SPAN_M = 0.45        # ASSUMED  level-transmitter calibrated span,
                           #          NWL +/- span/2
LEVEL_SPAN_RANGE_M = (0.30, 0.60)   # ASSUMED plausible range for this drum
                                    # class; sets the K band in the self-test

# Natural-circulation ratio: kg water circulated per kg steam raised.
# ASSUMED (range 6-12 typical for low/medium-pressure natural circulation).
# Used only to sanity-bound the circuit water inventory, not for K.
CIRCULATION_RATIO = 8.0

# --- nominal operating point (for the reported K and for feed sizing) ----
# CITED: docs/FieldMind_Boiler_Agent_Plan.pdf section 1 -- 67 TPH / 67 kg/cm2(g).
# The simulator's NOMINAL uses 66 kg/cm2(g) drum pressure; use that here so the
# config K matches the simulator's nominal state.
NOMINAL_DRUM_KGFCM2G = 66.0
NOMINAL_STEAM_TPH = 67.0
NOMINAL_BLOWDOWN_TPH = 1.0          # CITED: Plan section 4.2 water-balance example

# Feed system sizing margin over (steam + blowdown) at MCR.
# ASSUMED 1.20 (range 1.10-1.25) from feed-pump/valve sizing practice: MCR flow
# plus three-element-control authority.  It is NOT reverse-engineered from the
# old literal, though note 68.0 * 1.206 = 82.0, so the old FEED_VALVE_MAX_TPH
# was itself consistent with a ~20 % margin.
FEED_SIZING_MARGIN = 1.20


# =======================================================================
#  Horizontal-cylinder water geometry
# =======================================================================
def _radius_m() -> float:
    return DRUM_ID_M / 2.0


def water_height_m(level_pct: float) -> float:
    """Water height above the drum bottom for an indicated level %.

    NWL (50 %) sits on the drum centreline; the transmitter span is
    LEVEL_SPAN_M about NWL.  Clamped to the physical drum bore.
    """
    r = _radius_m()
    h = r + (level_pct - 50.0) / 100.0 * LEVEL_SPAN_M
    return min(2.0 * r, max(0.0, h))


def water_surface_area_m2(level_pct: float = 50.0) -> float:
    """Free-surface area = chord width at the water line x drum length.

    DERIVED: chord of a circle at height h is  2*sqrt(2 r h - h^2).
    At NWL this is exactly ID x L.
    """
    r = _radius_m()
    h = water_height_m(level_pct)
    chord = 2.0 * math.sqrt(max(0.0, 2.0 * r * h - h * h))
    return chord * DRUM_LENGTH_M


def drum_water_volume_m3(level_pct: float) -> float:
    """Partial volume of the horizontal cylinder filled to indicated level.

    DERIVED: circular-segment area  r^2 * acos((r-h)/r) - (r-h)*sqrt(2rh-h^2),
    times drum length.
    """
    r = _radius_m()
    h = water_height_m(level_pct)
    if h <= 0.0:
        return 0.0
    if h >= 2.0 * r:
        return math.pi * r * r * DRUM_LENGTH_M
    seg = r * r * math.acos((r - h) / r) - (r - h) * math.sqrt(2.0 * r * h - h * h)
    return seg * DRUM_LENGTH_M


# =======================================================================
#  The water-balance constant K
# =======================================================================
def K_level(p_Pa: float | None = None) -> float:
    """Drum-level gain [%/min per TPH of net water inflow].

    DERIVED:
        1 TPH net        = 1000/3600 = 0.277778 kg/s
        volume rate      = 0.277778 / rho_f(p)              m3/s
        dV / d(level%)   = A_surface * LEVEL_SPAN_M / 100    m3 per %
        level rate       = volume_rate / (dV/dlevel%)  * 60 -> %/min

        K(p) = (1e5 / 60) / (rho_f(p) * A_surface * LEVEL_SPAN_M)

    where 1e5/60 = (1000 kg/h per TPH) / (60 min/h) * (100 % per unit span)
    -- kept as an exact expression, not a rounded literal.

    Worked example at 66 kg/cm2(g) (independent of the algebra above, routed
    through physical volumes):
        rho_f          = 747.40 kg/m3
        1 TPH          = 1000/60 = 16.667 kg/min = 0.022299 m3/min
        1 % of span    = A_surface * span / 100
                       = 8.45 * 0.45 / 100 = 0.038025 m3
        K              = 0.022299 / 0.038025 = 0.5864 %/min per TPH

    rho_f from IAPWS-IF97 (data/physics/steam_tables).  Pressure-dependent;
    the simulator evaluates it every tick, the L1 check uses the nominal
    value (configs/base.yaml).
    """
    if p_Pa is None:
        p_Pa = kgfcm2g_to_Pa(NOMINAL_DRUM_KGFCM2G)
    a_surf = water_surface_area_m2(50.0)
    return (1.0e5 / 60.0) / (rho_f_kg_m3(p_Pa) * a_surf * LEVEL_SPAN_M)


# =======================================================================
#  Boiler-water inventory  (used by the dissolved-solids balance)
# =======================================================================
def drum_water_mass_kg(level_pct: float, p_Pa: float | None = None) -> float:
    if p_Pa is None:
        p_Pa = kgfcm2g_to_Pa(NOMINAL_DRUM_KGFCM2G)
    return drum_water_volume_m3(level_pct) * rho_f_kg_m3(p_Pa)


# Circuit (downcomers + water walls + bed coils + risers up to the two-phase
# front) water mass, held roughly constant as drum level moves.
# ASSUMED 7000 kg (range 5000-11000 kg for a 67 TPH AFBC evaporator circuit).
# Lower bound sanity: the circulating water must carry CIRCULATION_RATIO x the
# per-second steam mass over one circulation pass (~30-60 s), i.e.
#   8 * (67e3/3600) kg/s * ~45 s ~= 6.7 t -- consistent with the assumed value.
CIRCUIT_WATER_MASS_KG = 7000.0


def boiler_water_inventory_kg(level_pct: float,
                              p_Pa: float | None = None) -> float:
    """Total boiler water in contact with the dissolved-solids pool:
    drum water (varies with level) + circuit water (roughly fixed).

    When drum level falls, this total falls, so a fixed solids mass
    concentrates -- the Case 1 mechanism.  See DERIVATIONS.md section 5.
    """
    return drum_water_mass_kg(level_pct, p_Pa) + CIRCUIT_WATER_MASS_KG


# =======================================================================
#  Feed valve / pump maximum deliverable flow
# =======================================================================
def feed_valve_max_tph() -> float:
    """DERIVED: (nominal steam + blowdown) * FEED_SIZING_MARGIN.

    Replaces the old bare literal FEED_VALVE_MAX_TPH = 82.0.  A seized
    actuator (family A) caps deliverable flow at a fraction of this.
    """
    return (NOMINAL_STEAM_TPH + NOMINAL_BLOWDOWN_TPH) * FEED_SIZING_MARGIN


def _self_test() -> None:
    """Sanity invariants -- run on import."""
    p_nom = kgfcm2g_to_Pa(NOMINAL_DRUM_KGFCM2G)
    errs = []

    # surface area at NWL is exactly ID x L
    a = water_surface_area_m2(50.0)
    if abs(a - DRUM_ID_M * DRUM_LENGTH_M) > 1e-9:
        errs.append(f"A(NWL) = {a}, expected {DRUM_ID_M * DRUM_LENGTH_M}")

    # drum water at NWL is exactly half the cylinder
    v50 = drum_water_volume_m3(50.0)
    vfull = math.pi * _radius_m() ** 2 * DRUM_LENGTH_M
    if abs(v50 - vfull / 2.0) > 1e-6:
        errs.append(f"V(50%) = {v50}, expected {vfull / 2.0}")

    # K lands in the band implied by THIS drum's dims and the declared span
    # range (NOT a hardcoded band, NOT 0.22). Computed from DRUM_ID_M,
    # DRUM_LENGTH_M, LEVEL_SPAN_RANGE_M so it tracks the assumptions.
    k = K_level(p_nom)
    rho = rho_f_kg_m3(p_nom)
    a_nwl = DRUM_ID_M * DRUM_LENGTH_M
    k_wide_span = (1.0e5 / 60.0) / (rho * a_nwl * LEVEL_SPAN_RANGE_M[1])
    k_narrow_span = (1.0e5 / 60.0) / (rho * a_nwl * LEVEL_SPAN_RANGE_M[0])
    if not (k_wide_span <= k <= k_narrow_span):
        errs.append(f"K_level = {k:.4f}, outside "
                    f"[{k_wide_span:.4f}, {k_narrow_span:.4f}] for this drum")

    # Independent-ish re-derivation: route through physical volumes instead of
    # the K expression. This confirms the volume bookkeeping (m3 per TPH-minute
    # vs m3 per 1 % of span); it still adopts the "1 % = span/100" convention,
    # so it does NOT independently verify the definition of a percent of span.
    vol_per_min_per_tph = (1000.0 / 60.0) / rho                 # m3/min per TPH
    vol_per_pct_span = water_surface_area_m2(50.0) * LEVEL_SPAN_M / 100.0
    k_vol = vol_per_min_per_tph / vol_per_pct_span
    if abs(k_vol - k) / k > 1e-12:
        errs.append(f"K volume-bookkeeping check {k_vol} != {k}")

    # partial-volume monotonic in level
    vs = [drum_water_volume_m3(p) for p in (0, 10, 25, 50, 75, 90, 100)]
    if any(b < a for a, b in zip(vs, vs[1:])):
        errs.append(f"drum_water_volume not monotonic: {vs}")

    if errs:
        raise AssertionError("geometry self-test FAILED:\n  " + "\n  ".join(errs))


_self_test()


if __name__ == "__main__":
    p_nom = kgfcm2g_to_Pa(NOMINAL_DRUM_KGFCM2G)
    rho_f_nom = rho_f_kg_m3(p_nom)
    a_nwl = water_surface_area_m2(50.0)
    v_total = math.pi * _radius_m() ** 2 * DRUM_LENGTH_M
    k_nom = K_level()
    k_lo = K_level(kgfcm2g_to_Pa(55.0))
    k_hi = K_level(kgfcm2g_to_Pa(72.0))
    inv50 = boiler_water_inventory_kg(50.0)
    inv20 = boiler_water_inventory_kg(20.0)
    span_vol = a_nwl * LEVEL_SPAN_M
    span_for_022 = 1666.6667 / (rho_f_nom * 0.22 * a_nwl)

    print("Steam-drum geometry (ASSUMED dims):")
    print(f"  ID x L x span        = {DRUM_ID_M} x {DRUM_LENGTH_M} x {LEVEL_SPAN_M} m")
    print(f"  surface area @ NWL   = {a_nwl:.3f} m2")
    print(f"  drum volume total    = {v_total:.3f} m3")
    print(f"  drum water @ 50%     = {drum_water_volume_m3(50.0):.3f} m3 "
          f"({drum_water_mass_kg(50.0):.0f} kg)")
    print(f"  chord @ 20% vs NWL   = {water_surface_area_m2(20.0) / a_nwl:.3f} x")
    print()
    print(f"  rho_f @ nominal      = {rho_f_nom:.2f} kg/m3")
    print(f"  K_level nominal      = {k_nom:.4f} %/min per TPH  "
          f"(old 0.22 ; ratio {k_nom / 0.22:.2f}x)")
    print(f"  K_level 55 / 72 barg = {k_lo:.4f} / {k_hi:.4f}")
    print(f"  full-span water vol  = {span_vol:.3f} m3  "
          f"(0.22 would need a {span_for_022:.3f} m span)")
    print()
    print(f"  boiler water inv 50% = {inv50:.0f} kg")
    print(f"  boiler water inv 20% = {inv20:.0f} kg  "
          f"({100 * (inv20 / inv50 - 1):.1f}%)")
    print(f"  feed_valve_max_tph   = {feed_valve_max_tph():.2f} TPH  (old 82.0)")
