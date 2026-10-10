# FieldMind simulator - constant derivations

Every constant in `data/generator/sim.py` and `data/physics/` is one of:

| tag | meaning |
|---|---|
| **DERIVED** | from geometry / thermodynamics; the algebra is shown here |
| **CITED** | to `docs/FieldMind_Boiler_Agent_Plan.pdf` or a case-study PDF, with the section |
| **FITTED** | to `data/reference/fingerprint.json`, with the JSON path and method |
| **STANDARD** | to a named standard, with a specific clause / equation number |
| **ASSUMED** | plausible value, no citation checked; a range and the affected quantity are given |

`STANDARD` is used only where a clause number is actually verifiable (IAPWS-IF97).
Anything that would need a paywalled or unverified clause is **ASSUMED** instead.

Reproduce every headline number in this file with:

```
python -m data.physics.steam_tables      # IF97 self-test + operating-point dump
python -m data.physics.geometry          # geometry self-test + K, inventory, feed max
```

Both run their self-tests on import; a wrong coefficient fails loudly there.

---

## 1. Operating point

| quantity | value | provenance |
|---|---|---|
| main steam flow (MCR) | 67 t/h | **CITED**: Plan section 1; RCA Case 1 section 1 |
| drum pressure, gauge, nominal | 66 kg/cm2(g) | Plan section 1 gives 67 at MCR; the simulator `NOMINAL` runs 66 (normal 1 kg/cm2 operating margin below MCR). `geometry.NOMINAL_DRUM_KGFCM2G = 66.0` so the config K matches the simulator's nominal state. |
| main steam temperature | 495 degC | **CITED**: Plan section 1; RCA Case 1 section 1 |
| continuous blowdown, nominal | 1.0 t/h | **CITED**: Plan section 4.2 water-balance worked example uses `blowdown = 1.0` |
| tick period | 30 s | **CITED**: Plan section 3.2 |
| raw sample period | 5 s | **CITED**: Plan section 3.2; corroborated **FITTED**: `fingerprint.json.sampling.dt_seconds_median = 5.0` |

Unit conversion `kg/cm2(g)` <-> `MPa`:

- **DERIVED** from **STANDARD** `1 kgf := 9.80665 N` (standard gravity, ISO 80000-4)
  => `1 kgf/cm2 = 98066.5 Pa` exactly => `1 MPa = 10.197162129779 kgf/cm2`.
- `fingerprint.json._pressure_conversion` uses the same number but is **not its
  source**, so this is not marked CITED to the fingerprint.
- Immaterial note, recorded so it is not chased later: the fingerprint converted
  the reference boiler's pressure MPa->kg/cm2 without subtracting atmosphere, so
  its "99.6 kg/cm2(g)" is about 1 unit high if the source reading was absolute.
  Affects no calibration in this project.

---

## 2. Steam properties - IAPWS-IF97 (`steam_tables.py`)

**STANDARD**: IAPWS-IF97, *Revised Release on the IAPWS Industrial Formulation
1997 for the Thermodynamic Properties of Water and Steam* (IAPWS R7-97(2012)).

| region | used for | clause | coefficients |
|---|---|---|---|
| Region 1 | saturated / compressed liquid: feedwater, rho_f, h_f | Eq. 7 | Table 2, 34 terms (ni, Ii, Ji) |
| Region 2 | saturated / superheated vapour: main steam, rho_g, h_g | Eq. 15-17 | Table 10 ideal 9 terms, Table 11 residual 43 terms |
| Region 4 | saturation line p <-> Tsat | Eq. 30-31 | Table 34, 10 terms (ni) |

Specific gas constant `R = 0.461526 kJ/(kg K)` - **STANDARD**: IF97 Table 1.

Region 3 (near-critical) is **not implemented**; the module must not be called
above about 22 MPa. The whole operating envelope is 55-75 bar -> Regions 1, 2, 4.

### Self-test anchors (STANDARD: IF97 published verification values)

Run on import (`_self_test`), relative tolerance 1e-7 (1e-6 for Region 4):

| check | expected | IF97 source |
|---|---|---|
| Tsat(10 MPa) | 584.149488 K | Table 35 |
| Tsat(0.1 MPa) | 372.755919 K | Table 35 |
| Tsat(1 MPa) | 453.035632 K | Table 35 |
| psat(300 K) | 0.00353658941 MPa | Eq. 30 verification |
| psat(500 K) | 2.63889776 MPa | Eq. 30 verification |
| psat(600 K) | 12.3443146 MPa | Eq. 30 verification |
| Region 1 (3 MPa, 300 K) | v = 1.00215168e-3, h = 115.331273 kJ/kg, s = 0.392294792 | Table 5 |
| Region 1 (80 MPa, 300 K) | v = 9.71180894e-4, h = 184.142828 kJ/kg | Table 5 |
| Region 1 (3 MPa, 500 K) | v = 1.20241800e-3, h = 975.542239 kJ/kg | Table 5 |
| Region 2 (3.5 kPa, 300 K) | v = 39.4913866, h = 2549.91145 kJ/kg, s = 8.52238967 | Table 15 |
| Region 2 (3.5 kPa, 700 K) | v = 92.3015898, h = 3335.68375 kJ/kg | Table 15 |
| Region 2 (30 MPa, 700 K) | v = 5.42946619e-3, h = 2631.49474 kJ/kg | Table 15 |

The (30 MPa, 700 K) check is the **only** one that exercises the high-Ii
residual terms (contribution scales as pi^24). During development a transcription
error in the last residual term (Ii=24, Ji=58: `-0.94369707241210e-6` written as
`e-16`) passed every other check and failed only this one - keep it.

### Saturated-line property approximation

`sat_liquid(p)` / `sat_vapour(p)` evaluate Region 1 / Region 2 at `(p, Tsat(p))`.
The IF97 region-boundary inconsistency this introduces is **below 0.02 % in h** -
under the simulator's measurement noise. Recorded, not corrected.

### Values at the operating point (`python -m data.physics.steam_tables`)

| p (kg/cm2g) | Tsat (degC) | rho_f (kg/m3) | rho_g (kg/m3) | h_f (kJ/kg) | h_g (kJ/kg) | h_fg (kJ/kg) |
|---|---|---|---|---|---|---|
| 55 | 269.91 | 767.61 | 28.03 | 1184.63 | 2789.77 | 1605.14 |
| 66 | 281.61 | 747.40 | 34.06 | 1245.11 | 2777.94 | 1532.83 |
| 67 | 282.60 | 745.62 | 34.62 | 1250.32 | 2776.74 | 1526.42 |
| 72 | 287.38 | 736.84 | 37.47 | 1275.73 | 2770.43 | 1494.70 |

Main steam `h(66 kg/cm2g, 495 degC) = 3404.22 kJ/kg`;
feedwater `h(70 bar, 150 degC) = 636.30 kJ/kg`.

**Independent cross-check of Tsat** (different method than the code's IF97
Eq. 31): linear interpolation of a coarse published saturation table between
6.5 MPa (280.86 degC) and 7.0 MPa (285.83 degC) to 6.574 MPa abs gives about
281.6 degC, matching the IF97 value 281.61 degC.

---

## 3. Steam-drum geometry and the water-balance constant K (`geometry.py`)

### 3.1 The old value was not a source

`sim.py` asserted `K_LEVEL = 0.22 %/min per TPH` with no derivation. It is **not**
reproduced here. Reconciling drum dimensions to hit 0.22 and then calling the
result DERIVED would launder a guess:

- 0.22 needs a **1.20 m** level-transmitter span on this drum
  (full-span water volume 3.803 m3, computed by the module), or a **22.5 m2**
  free surface at the 0.45 m span (a 17 m long drum). Neither is a real bi-drum.
- Real drum level-transmitter spans are **300-600 mm**.

### 3.2 Drum dimensions - ASSUMED

| symbol | value | range | provenance |
|---|---|---|---|
| `DRUM_ID_M` | 1.30 m | 1.1-1.5 m | **ASSUMED**, AFBC bi-drum design practice for a 60-70 t/h unit; K prop 1/ID |
| `DRUM_LENGTH_M` | 6.50 m | 5.5-8.0 m | **ASSUMED**, tangent-to-tangent; K prop 1/L |
| `LEVEL_SPAN_M` | 0.45 m | `LEVEL_SPAN_RANGE_M = (0.30, 0.60)` | **ASSUMED**, level-transmitter calibrated span, NWL +/- span/2; K prop 1/span |
| `CIRCULATION_RATIO` | 8 | 6-12 | **ASSUMED**, natural circulation; used only to sanity-bound circuit water mass, not for K |

**The FieldMind boiler's drum dimensions are a declared benchmark parameter,
not a measured property.** The plan PDF (`docs/FieldMind_Boiler_Agent_Plan.pdf`
section 1) fixes the operating point - 67 t/h, 67 kg/cm2(g), 495 degC - but
specifies nothing about drum internal diameter, tangent-to-tangent length or
level-transmitter span. The three values above were chosen to be plausible for
the class; they are **inputs to the benchmark, not findings from it**, and every
number that depends on them inherits that status.

### 3.3 Derivation of K(p)

Horizontal-cylinder drum; NWL (50 %) on the centreline; free-surface area at the
water line `A_s = 2*sqrt(2 r h - h^2) * L`, which at NWL is exactly `ID*L`.

```
1 t/h net inflow  = 1000/3600 kg/s = 0.277778 kg/s
volume rate       = 0.277778 / rho_f(p)                    m3/s
dV / d(level %)   = A_s * LEVEL_SPAN_M / 100               m3 per %
level rate        = volume_rate / (dV/dlevel%) * 60       -> %/min

K(p) = (1e5 / 60) / (rho_f(p) * A_s * LEVEL_SPAN_M)
```

`1e5/60 = (1000 kg/h per t/h) / (60 min/h) * (100 % per unit span)`, kept as an
exact expression (a rounded literal `1666.6667` fails the module's
volume-bookkeeping check at the 8th digit).

### 3.3a Provenance of K - DERIVED-from-ASSUMED, not DERIVED

The algebra in 3.3 is derived. The **value** it produces is not: it rests
entirely on the three ASSUMED inputs of 3.2 - `DRUM_ID_M`, `DRUM_LENGTH_M`,
`LEVEL_SPAN_M` - none of which is a measured property of any real drum. Labelling
K plain **DERIVED** oversells it. Its honest provenance is **DERIVED-from-ASSUMED**:
derived algebra, assumed inputs.

Propagating the full declared ranges (ID 1.1-1.5 m, L 5.5-8.0 m, span
0.30-0.60 m) through `K = (1e5/60) / (rho_f * ID * L * span)` at
`rho_f(66 kg/cm2(g)) = 747.40 kg/m3`:

```
K_min  (ID 1.5,  L 8.0, span 0.60) = 1666.667 / (747.40 * 12.00 * 0.60) = 0.310
K_nom  (ID 1.30, L 6.50, span 0.45)                                     = 0.586
K_max  (ID 1.1,  L 5.5, span 0.30) = 1666.667 / (747.40 *  6.05 * 0.30) = 1.229
```

**Uncertainty on K across the declared dimension ranges: 0.31 - 1.23 %/min per
t/h - a 4x band.** The nominal 0.586 is one point inside that band, not a
determined quantity. (`geometry._self_test` asserts only the narrower span-only
sub-range 0.44-0.88; the 0.31-1.23 figure is the honest full band and is
recorded here.)

### 3.4 Result

| quantity | value | note |
|---|---|---|
| A_s at NWL | 8.450 m2 | = ID*L |
| rho_f at 66 kg/cm2(g) | 747.40 kg/m3 | IF97 |
| **K (nominal)** | **0.5864 %/min per t/h** | **DERIVED-from-ASSUMED** (3.3a); 2.67x the old 0.22 |
| K at 55 / 72 kg/cm2(g) | 0.5710 / 0.5948 | K is pressure-dependent through rho_f |
| K band, full declared ranges (ID, L, span) | **0.31 - 1.23** | **4x band**; nominal is one point in it (3.3a) |
| K band, span only (`LEVEL_SPAN_RANGE_M`) | 0.440 - 0.880 | computed in `_self_test` |

**Independent cross-check** (through physical volumes, not the K expression):
at 66 kg/cm2(g), 1 t/h = 16.667 kg/min = 0.022299 m3/min; 1 % of span =
8.45 * 0.45 / 100 = 0.038025 m3; K = 0.022299 / 0.038025 = **0.5864**.

### 3.5 Approximations, stated

- `K_level()` uses A_s **at NWL regardless of actual level**. The water-line
  chord narrows about 2 % at 20 % level (`chord @ 20% vs NWL = 0.978x`), so the
  true K is mildly level-dependent; the fixed nominal value used by the L1
  check is a stated approximation.
- The simulator evaluates `K_level(p)` every tick (pressure-dependent); the L1
  water-balance check (`configs/base.yaml`) uses the single nominal value.

### 3.6 Consequence - water side is about 2.7x faster (NOT re-tuned here)

`configs/base.yaml checks.rates.drum_level` (0.5 %/min over 3 min) and the
`episode_build.state_timeline` DEVIATION band were tuned against the old K.
They are **not** changed in this stage. The before/after report shows family A
and family B reaching their thresholds about 2.7x sooner.

### 3.7 OPEN DISCREPANCY - the RCA over-determines K, and geometry disagrees

**This is an advisor question, not a settled point.**

RCA Case 1 section 2.1, verbatim:

> T-0:00 - Level 50%, steam flow 60 TPH, feed flow 60 TPH, stable.
> T+0:12 - Level drifting down at approx 1% per minute; feed flow reads 4-5 TPH
>          less than steam flow (persistent negative mismatch).
> T+0:25 - Drum level Low alarm at 20%; FCV shows 100% demand, no further
>          increase in feed flow.

The rate ("approx 1% per minute") and the deficit ("4-5 TPH less than steam")
are stated **for the same instant**, T+0:12. Taken together they pin K
empirically:

```
implied K (rate / deficit)                 = 1.0 / 4.5   = 0.222
implied K (50%->20% over T:00..T+0:25)     = 1.2 / 4.5   = 0.267
our geometry K                                            = 0.586
```

So our derived K is **2.2x to 2.6x** a documented event. And no plausible
bi-drum geometry closes the gap: K = 0.22 at a 0.45 m span needs a 22.5 m2 free
surface, i.e. a 17 m long drum; at a real length it needs a 1.2 m level span,
double the top of the assumed range.

**Candidate explanations (unresolved):**

1. The RCA is a "representative composite" (its own section 3 note). "approx
   1%/min" and "4-5 TPH" may be independently rounded field observations, not a
   matched pair - the timeline is itself slightly inconsistent (50%->20% in
   25 min is a 1.2 %/min *average*, not 1.0).
2. Three-element control recovery: the *instantaneous* valve-position deficit is
   4-5 TPH, but the controller keeps clawing feed back, so the *effective*
   time-averaged deficit driving the level is smaller than 4-5 TPH.
3. The RCA drum's level transmitter is calibrated on a wider span than 0.45 m.

**Choice made here, with its cost stated:** we preserve the derived geometry and
calibrate the later severity-tuning step to match the RCA *rate*, which at our K
needs a deficit of about **2.3 t/h** - roughly **half** the 4-5 t/h the RCA
records. We are choosing the rate over the deficit. This disagreement with the
recorded deficit is **not resolved**; it is carried to the advisor.

```
time_to_trip = 40 %span / (K * net_deficit_t/h)          [50 % -> 10 % trip]

  ep_A01 now:  eff 0.77 -> feed cap 0.77*81.6 = 62.8 -> deficit 5.25 t/h
               t_trip = 40 / (0.586 * 5.25) = 13.0 min
  RCA rate ~1 %/min:  deficit ~= 1.0 / 0.586 = 1.7 t/h  (feed_valve_eff ~= 0.81)
  midpoint target for tuning: deficit ~2.3 t/h, feed_valve_effectiveness ~= 0.805
```

Family A visibly trips at about 13 min in this stage's report. Driver
severities are **not** changed in this stage.

### 3.7a Advisor question - two measured numbers settle both the band and the discrepancy

The 4x uncertainty band on K (3.3a) and the 2.2-2.6x disagreement with RCA Case 1
(3.7) are **the same question asked from two directions**, and one answer
probably settles both:

> **Obtain, from the case-study plant, (a) the drum-level transmitter's
> calibrated span and (b) the steam-drum general-arrangement (GA) drawing
> giving internal diameter and tangent-to-tangent length.**

- The **calibrated span** is the single largest lever on K (`K prop 1/span`),
  currently ASSUMED 0.45 m over a 0.30-0.60 m range. RCA Case 1 can only be
  reconciled with our geometry at a ~1.2 m span (3.7), which is off the top of
  that range - so either the assumed span is wrong or the RCA rate/deficit pair
  is not a matched observation. A measured span decides it.
- The **GA drawing** fixes `DRUM_ID_M` and `DRUM_LENGTH_M` (ASSUMED 1.30 m x
  6.50 m), which set the other two factors in `K prop 1/(ID*L*span)`.

With all three measured, the 0.31-1.23 band in 3.3a collapses to a point, K's
provenance moves from **DERIVED-from-ASSUMED** to **DERIVED**, and the RCA
discrepancy either resolves or is confirmed as a rounding artefact in the
composite. Until then both are carried, unresolved.

### 3.8 Feed valve / pump maximum flow

`feed_valve_max_tph()` = `(NOMINAL_STEAM_TPH + NOMINAL_BLOWDOWN_TPH) *
FEED_SIZING_MARGIN` = `(67 + 1) * 1.20` = **81.60 t/h**.

- `FEED_SIZING_MARGIN = 1.20` - **ASSUMED** (range 1.10-1.25), feed-pump / valve
  sizing practice: MCR flow plus three-element-control authority. Affects how
  hard the feed controller can push before it saturates.
- Replaces the old bare `FEED_VALVE_MAX_TPH = 82.0`. Not reverse-engineered from
  it, though note `68 * 1.206 = 82.0`, so the old literal was itself consistent
  with a 20 % margin.

---

## 4. Energy balance and combustion  *(sub-model 2 -- built 2026-09-01)*

Structure (SI internally). `sim.py` "ENERGY-SIDE constants (sub-model 2)".

```
air_st    = (2.667 C + 7.937 H + 0.998 S - O) / 0.2315      [kg air / kg fuel]  DERIVED
GCV       = 8080 C + 34500 (H - O/8) + 2240 S               [kcal/kg]  DERIVED (Dulong)
Q_steam   = W_steam * (h_ms(p,T_ms) - h_fw(p,T_fw))         [W]   DERIVED (IF97)  ~51.5 MW
Q_fuel    = m_coal * GCV                                    [W]   gross
m_coal    = (Q_steam / eta) / GCV                           [kg/s]  DERIVED  ~3.58 kg/s
Q_quench  = W_leak * (1 - flash_x) * h_fg(1 atm)            [W]   flue-gas latent, flash_x~0.366
dp/dt     = (Q_evap_supply - W_steam * h_fg(p)) / E_P       [Pa/s]
  Q_evap_supply = Q_absorbed - W_feed*(h_f(p)-h_fw) - W_steam*(h_ms(p,T_ms)-h_g(p))
  -- identically W_steam*h_fg at the nominal point, so dp/dt = 0 with no fitted gain.
T_bed_target = 850 + K_BED_LOAD*(steam-67) + K_COMB*(hr-ha)
               + K_FUEL*min(0, fuel_avail - fire_for_load) + K_CV*(cv-1)
               + K_PA*(1 - min(1, PA)) - Q_quench / G_BED_EFF
```

### 4.1 Coal: ONE ultimate analysis, air_st AND GCV derived from it

CLAUDE.md: *"ASSUMED inputs describing the same object must be mutually
consistent."* The old plan took `GCV = 3400 kcal/kg` **ASSUMED directly** while a
separately-assumed analysis gave `air_st ~ 5.46`. Dulong ties them: an analysis
giving `air_st ~ 5.46` implies `GCV ~ 4000-4100`, **not 3400**. So one analysis
is fixed and both are derived.

| element (as-fired mass frac) | value | range |
|---|---|---|
| C | 0.415 | 0.38 - 0.45 |
| H | 0.029 | 0.025 - 0.033 |
| O | 0.075 | 0.060 - 0.090 |
| N | 0.009 | 0.006 - 0.012 |
| S | 0.0045 | 0.003 - 0.006 |
| ash | 0.335 | 0.30 - 0.38 |
| moisture | 0.1325 | 0.10 - 0.16 |

- **`air_st` = 5.471 kg/kg** -- DERIVED. Independent kmol-balance route
  (`O2 = C + H/4 + S - O/2`, `/0.20948` mol frac, `x 28.965`) gives 5.467,
  **0.06 %** difference.
- **`GCV` = 4040 kcal/kg** (1.691e7 J/kg) -- DERIVED (Dulong). Independent Boie
  correlation (`35160 C + 116225 H - 11090 O + 6280 N + 10465 S` kJ/kg) gives
  4116 kcal/kg, **1.9 %** difference.
- **Consistency self-test** (`_self_test` check 4): heat release per kg
  stoichiometric air is ~fuel-independent (~3 MJ ~ 725-760 kcal). `GCV/air_st`
  must land in **700-780 kcal/kg-air**. Ours = **739**. The inconsistent
  `(3400, 5.46)` pair gives **623** and FAILS the band. Mutation M5 (Dulong
  coefficient `8080 -> 5080`) drops it to 511 -- caught.

`notes_gen.py base_cv` and `data/kb` coal reports still say 3400 (the feeder
*calibration basis*). See "Disagreements" in `reports/stage3_submodel2.md`:
they should move to ~4040 at the next episode regeneration; episodes are NOT
regenerated in this stage.

### 4.2 Other constants

| constant | value | provenance |
|---|---|---|
| steam duty at MCR | **51.5 MW** | **DERIVED**: `18.61 kg/s * (h_ms(66 bar,495 C) - h_fw(66 bar,150 C))` = `18.61 * (3404.2 - 635.7) kJ/kg`. IF97. |
| `eta_boiler` | 0.85 | **ASSUMED** (0.80 - 0.87). Sets `m_coal` and every energy gain. |
| `LAMBDA_EXCESS` | 1.25 | **ASSUMED** (1.15 - 1.35). Total excess-air ratio; sets flue-gas mass flow and `PRIMARY_AIR_MIN`. |
| `PA_AIR_FRACTION` | 0.55 | **ASSUMED** (0.45 - 0.65). Primary/total air split. Sets `K_PA` and `PRIMARY_AIR_MIN`. |
| `C_PG` | 1150 J/kg/K | **ASSUMED** (1100 - 1250). Flue-gas cp; sets `G_FG`. |
| `BETA_FURNACE` | 0.45 | **FITTED-TO-EXPECTATION** (0.30 - 0.65). NOT a practice figure -- chosen so the bed sensitivity is right (§4.3). Sets `G_WW`. |
| `T_FW_C` | 150 degC | **ASSUMED** (140 - 160). Economiser-outlet feedwater temp; duty magnitude only. |
| `T_FLUE_REF_C` | 200 degC | **ASSUMED** (150 - 260). APH-heated PA inlet; the `(T_bed - T_ref)` span in `K_PA`. |
| `M_BED_KG`, `C_BED` | 11000 kg, 1000 J/kg/K | **ASSUMED** (8000-16000, 900-1100). `tau_bed = M_bed*c_bed / G_BED_EFF` = **139 s** (`/ G_BED_EFF`, NOT `/ G_FG` -- tau and gain share one conductance; §4.3). |
| `M_SH_METAL_KG`, `C_STEEL`, `_CP_STEAM` | 6000 kg, 490, 2900 J/kg/K | **ASSUMED** (3500-10000, 470-510, 2800-3500). `tau_ms` only (~55 s). |
| `K_MS_BED` | 0.35 | **ASSUMED** (0.28 - 0.40). Superheater outlet sensitivity to bed temp. |
| `K_MS_FLOW` | 0.45 degC/(t/h) | **ASSUMED** (0.2 - 0.8). ms fall per t/h of extra steam (RCA Case 4 direction). See §9. |
| `K_BED_LOAD` | 2.75 degC/(t/h) | **ASSUMED** (2.0 - 4.0). Anchored to `configs/base.yaml load_coef_degc_per_tph` (unchanged); fingerprint bed~load slope 3.9 is the upper bound (different boiler -- §9). |

### 4.3 Bed conductance `G_BED_EFF` and `BETA_FURNACE` -- FITTED to expectation

The bed-temperature deviation gains divide a heat perturbation by a conductance.
Using `G_FG` alone (`m_fluegas * cp_g`, ~3.1e4 W/K) made the bed ~2.5x too
sensitive: a 3.5 t/h leak quenched the *average* bed 45 degC, family E crossed
the 880 alarm. An AFBC bed is strongly clamped by the boiling evaporator surface
immersed in it, so a second parallel sink is added:

```
G_WW  = BETA_FURNACE * Q_fuel_nom / (T_bed_nom - Tsat(p_nom))
      = 0.45 * 6.06e7 / (850 - 281.6)  =  4.80e4 W/K
G_BED_EFF = G_FG + G_WW  =  3.09e4 + 4.80e4  =  7.89e4 W/K
```

**`BETA_FURNACE = 0.45` is FITTED TO EXPECTATION, not an ASSUMED practice
figure.** The *structure* (a parallel water-wall sink linearised about the
saturation temperature) is physics; the *value* was chosen to make two
downstream numbers come out right:

1. a **3.5 t/h tube leak quenches the average bed ~15-25 degC** (RCA Case 11
   scale for a one-compartment sharp drop seen on a six-compartment average);
2. **family-E fouling (heat_absorption 0.985) stays under the 880 degC bed
   alarm** across seeds (an episode that trips is a mislabel).

Measured window where BOTH still hold (bed quench is a clean paired
leak-minus-no-leak difference; family-E max is the worst of 4 seeds):

| `BETA_FURNACE` | `G_BED_EFF` | leak 3.5 t/h bed sag | leak 6.0 t/h | family-E max bed |
|---|---|---|---|---|
| 0.25 | 5.76e4 | 24 degC | 41 degC | ~880 (fails constraint 2) |
| **0.30** | 6.29e4 | 22 degC | 38 degC | 879.6 (edge) |
| **0.45** (nominal) | 7.89e4 | **18 degC** | **30 degC** | 876.5 |
| **0.65** | 1.00e5 | 14 degC | 24 degC | 874.5 |
| 0.75 | -- | 12 degC | 21 degC | -- (family D cv 1.16 only reaches ~955, weak) |

So the **defensible range is `BETA_FURNACE` in [0.30, 0.65]**, nominal 0.45.
It happens to sit inside the physical furnace water-wall heat-split band
(~40-55 % of gross fuel heat picked up in the bed/furnace zone) -- that
agreement is a sanity check, not the provenance.

`K_COMB = K_FUEL = K_CV = Q_fuel_nom / G_BED_EFF = 768 K` per unit relative
heat imbalance. The bed first-order lag uses the **same** conductance:
`tau_bed = M_bed*c_bed / G_BED_EFF = 139 s` (a `tau = C/G` must use the `G`
that sets its steady-state gain `dT = dQ/G`; an earlier version divided by
`G_FG` and was 2.6x too slow -- `_self_test` check 6a now asserts the two
denominators are one number). `K_PA` keeps the `G_FG` normalisation (implicit
via `lambda`) because reducing excess air raises the *gas* temperature directly
and the water walls buffer it less -- `K_PA = PA_AIR_FRACTION * LAMBDA_EXCESS *
(T_bed - T_ref) = 447 K`.

### 4.4 `e_p` -- PHYSICAL, not fitted to the ar1 timescale (correction 1)

```
E_P = M_bw*cp_bw*dTsat/dp  +  M_metal*cp_steel*dTsat/dp  +  V_steam*(drho_g/dp)*h_fg
    =  550  +  90  +  38   =  679 J/Pa
```

- `M_bw` = 10224 kg (`geometry.boiler_water_inventory_kg(50)`), `cp_bw` = 5316
  J/kg/K (IF97 `sat_liquid(p_nom).cp`), `dTsat/dp` = 1.01e-5 K/Pa (IF97 central
  difference).
- `M_metal` = 18000 kg **ASSUMED** (12000 - 26000): drum shell + evaporator /
  downcomer / riser steel at saturation temperature.
- `V_steam` = 4.31 m3 = drum steam half-volume at NWL (`= V_DRUM_50_M3` by
  centreline symmetry).

**NOT fitted** to `fingerprint.json drum_pressure.ar1.timescale_minutes =
44.53`. That timescale is inherited input slowness (CLAUDE.md "do not fit plant
time constants to fingerprint autocorrelation"); fitting it wants
`e_p ~ 1.3e4 J/Pa`, **19x** the physical value. The derived 679 J/Pa is ~1.2x
below correction 1's independent physical band (0.8 - 1.2e3 J/Pa) -- recorded
as a disagreement, inside 2x, no stop rule. Open-loop thermodynamic
`tau_p ~ E_P / (d(W_steam*h_fg)/dp) ~ 5 min`, consistent with the correction's
2.7 - 4.1 min; the firing p-trim makes the closed loop faster (~1 min).
Achieved `drum_pressure` autocorrelation is reported after sub-model 4.

### 4.5 E8 -- `primary_air` valid range

`K_PA * (1 - min(1, PA))` rises monotonically as PA falls; the model has **no
air-limited turnover**. Below
`PRIMARY_AIR_MIN = 1 - (LAMBDA_EXCESS - 1)/(PA_AIR_FRACTION*LAMBDA_EXCESS) =
0.636` the primary + secondary air can no longer meet stoichiometric demand and
a real bed would go sub-stoichiometric (CO up, bed temp rolls over). **The
`primary_air` driver is only physical on [0.64, ~1.15].** Family D uses 0.80 and
0.84, both inside it. Direction check 7 asserts monotonicity and the bound.

### 4.6 E10 -- leak sign and no double-count

- **Direction**: leak -> drum inventory lost + extra cold make-up feed -> drum
  pressure **falls**; the droplets take latent heat from the flue gas to
  vaporise -> bed **cools** (RCA Case 11: "bed temperature dropped sharply").
- The leaking water is **saturated** at drum pressure, so it flashes
  `flash_x = (h_f(p_drum) - h_f(1 atm)) / h_fg(1 atm) = 0.366` -- **~37 %** --
  on its own sensible heat; only `(1 - flash_x)` draws furnace latent heat.
- **Not double-counted**: the bed term is `(1 - x)*h_fg(1 atm)` (~1431 kJ/kg)
  latent heat from the flue gas -> `T_bed`; the pressure term is
  `W_feed*(h_f(p) - h_fw)` (~609 kJ/kg) sensible heat the raised make-up feed
  carries in and the leak carries out -> `dp/dt`. Different enthalpies,
  different state variables. **Proven by mutations M3 / M4**: killing the bed
  quench removes the bed drop and leaves the pressure drop (even grows it via
  ms coupling); killing the feedwater term removes the pressure drop (reverses
  it to +0.24) and leaves the 36 degC bed drop untouched.

### 4.7 Driver roles (unchanged)

`fuel_availability` is a `min()` **cap** on firing (family C: feeder saturates,
coal still short); `coal_cv_factor` scales released heat via `K_CV` (family D);
`primary_air` via `K_PA` (family D low-PA).

---

## 5. Dissolved-solids balance and boiler-water conductivity

### 5.1 The balance and its units

```
dm_solids/dt = c_fw * W_feed  -  c_bw * (W_blowdown + W_leak)     [kg/s]
c_bw          = 1e6 * m_solids / M_bw(level, p)                    [ppm, mg/kg]
kappa_bw      = c_bw * K_US_PER_PPM                                [uS/cm]
```

`M_bw(level, p)` = `geometry.boiler_water_inventory_kg` = drum water (varies with
level) + circuit water (about fixed).

**Conductivity <-> TDS convention (issue-4 fix).** The standard dilute
NaCl-equivalent relation is `TDS[ppm] ~= 0.55 * EC[uS/cm]`, i.e.
`EC ~= 1.8 * TDS[ppm]`. So `K_US_PER_PPM = 1.8` and `kappa = c_bw * 1.8`
(**not** `c_bw * 0.5`). **ASSUMED**, range 1.4-2.0.

| constant | value | provenance |
|---|---|---|
| `M_bw` at NWL | 10 224 kg | **DERIVED** drum half-volume * rho_f = 3 224 kg, **+ ASSUMED** `CIRCUIT_WATER_MASS_KG = 7000` (range 5000-11000; lower bound sane vs `CIRCULATION_RATIO * (67e3/3600) * ~45 s ~= 6.7 t`) |
| `M_bw` at 20 % level | 9 378 kg | **DERIVED**; only -8.3 % vs NWL |
| `K_US_PER_PPM` | 1.8 uS/cm per ppm | **ASSUMED** (range 1.4-2.0), NaCl-equivalent |
| feedwater conductivity `kappa_fw` | 12 uS/cm | **ASSUMED** (range 6-18). Elevated for a small captive plant with imperfect condensate polishing. Sets the absolute kappa level, not the trend direction. |
| nominal cycles of concentration | 68 | **DERIVED**: `W_feed / W_blowdown = (67 + 1) / 1` |
| **nominal boiler-water conductivity** | **816 uS/cm** (c_bw 453 ppm) | **DERIVED** from the two rows above: `12 * 68`. |
| normal control band | 400-1500 uS/cm | **ASSUMED**; the 816 nominal sits inside it. IS 10496 is **not** cited - its clause text could not be verified offline. A later stage with document access may promote this to CITED with a clause number. |

All three sections (5.1, 5.3, 8) now use `kappa_fw = 12` -> nominal
`kappa_bw = 816 uS/cm`.

**Built in `sim.py` (sub-model 3, 2026-09-01).** `M_bw` uses the sub-model-1
collapsed-liquid mass `_m_liq + CIRCUIT_WATER_MASS_KG` directly (the true water
in contact with the pool -- NOT the swelled indicated level). `_m_solids` starts
at the equilibrium `C_BW_NOMINAL_PPM * 1e-6 * M_bw(0)`, so a no-fault run holds
`kappa_bw` flat (self-test: +0.3 % over 180 min). Verified trajectories
(sim.py `_self_test` 8-10, `_ds_recheck`) reproduce §5.3 to ~1 uS/cm:
no-fault 0 %, leak 3.5 t/h -30 % @ 90 min, CBD-open -23 %, feed-short +14 %.
Independent closed-form re-derivation (`c_bw_ss = c_fw*W_feed/(W_bd+W_leak)`,
first-order approach with `tau = M_bw/(W_bd+W_leak) = 136 min`) matches the
Euler ODE to **0.1-0.2 %**.

### 5.2 Why one ODE gives both Case 1 and Case 11

At steady state `c_bw / c_fw = W_feed / (W_bd + W_leak)` - the cycles of
concentration.

- **Case 11 (family B: tube leak, or CBD stuck open):** `W_leak` (or extra
  `W_bd`) enters the denominator; the controller raises `W_feed` (dilute) to
  hold level. Cycle ratio collapses from 68 toward about 16 for a 3 t/h leak.
  `kappa_bw` **falls hard**.
- **Case 1 (family A: feed short):** the drum boils down, `M_bw` drops, so a
  near-fixed `m_solids` concentrates: `- c_bw * (dM_bw/dt) / M_bw > 0`. But
  `M_bw` only drops 8.3 % over the 50 % -> 20 % level range, and if the
  documented CBD-passing contributory is present it dilutes the other way.

### 5.3 Discriminator framing - leak-exclusion, asymmetric (RESOLVED)

Modelled trajectories (`scratchpad/ds_preview.py`, K = 0.5864, kappa_fw = 12,
K_US_PER_PPM = 1.8, logging every 30 min, one operator grab sample 15 min after
the first ALARM):

| episode | window | kappa_bw start -> end | key samples (uS/cm) |
|---|---|---|---|
| **no fault** | 90 / 240 min | 816 -> 816 (**0.0 %**) | 816 flat at every sample |
| Case 11 `ep_B01` leak 3.5 t/h | 90 min | 816 -> 568 (**-30 %**) | +30 m 776 ; +60 m 661 ; operator 755 |
| Case 11, 150-min window | 150 min | 816 -> 434 (**-47 %**) | +90 m 568 ; +120 m 493 |
| family B, CBD stuck open only (no tube leak) | 90 min | 816 -> 625 (**-23 %**) | +30 m 781 ; +60 m 696 ; operator 766 |
| Case 1 `ep_A01`, blowdown normal | 55 min | 816 -> 933 (**+14 %**) | +30 m 914 ; operator 938 |
| Case 1 `ep_A01`, **CBD ~40 % open** (documented contributory) | 55 min | 816 -> **778 (-5 %)** | +30 m 878 ; operator 857 |

**Rule (matches how the RCA uses the signal):**

- A **sustained fall** in `kappa_bw` => **family B** (tube leak *or* stuck-open
  CBD). RCA Case 11 section 6: falling conductivity "immediately excludes all
  non-leak explanations of the water imbalance."
- **Flat / rising** `kappa_bw` => **not family B**. Family A stays identified by
  feed-valve saturation + feed deficit + level fall. The concentrating signal
  (+3 to +15 %, inverting to -5 % with CBD passing) is **not** claimed as a
  positive Case 1 signature. RCA Case 1 section 6: rising conductivity "supports
  H3 only partially."

**What it does NOT do, stated so it is not over-claimed:**

1. It discriminates **family A from family B**, *not* leak from blowdown. A
   stuck-open CBD (`ep_B04`) removes boiler water at boiler-water concentration
   exactly as a tube leak does and dilutes in the same direction (-23 % vs the
   leak's -30 % above) - both are family B. Blowdown is not one of the six
   tags, so a genuine CBD change is indistinguishable from a leak on
   conductivity alone. Stated false-positive exposure.
2. It does not positively confirm Case 1.

### 5.4 The "sustained fall" threshold - DERIVED, and no-fault drift shown

**No-fault drift is exactly 0.0 %** (`ds_preview.py` "no fault" rows, 90 and
240 min): with `m_solids` initialised to its equilibrium value
`c_fw * cycles * M_bw`, and `W_feed = W_steam + W_blowdown` at steady state, the
balance holds `c_bw` constant. The only no-fault movement in a real episode is
analyser noise (+/-2 %) plus slow OU load wander nudging `W_feed`; call the
realistic no-fault band **+/-5 %**.

Family B floor, from the same runs: `kappa_bw` crosses **-10 % by about 45-50
min** and reaches -23 % (CBD only) to -30 % (3 t/h leak) by 90 min. Steeper
leaks (`ep_B02`, 6 t/h) cross -10 % sooner.

So the trigger:

> a monotone fall of **>= 10 % relative to the pre-episode baseline band,
> sustained across >= 3 consecutive 30-min logged samples**

is the smallest change a family-B event reliably produces and that no-fault
operation (0 % +/- 5 %) does not. Recorded as **DERIVED from the solids
balance**; re-checked against the regenerated family-B episodes in the
before/after report.

### 5.5 Sample cadence and source (RESOLVED)

- **Source:** an **online conductivity analyser on the CBD line, logged
  periodically** - **ASSUMED**; many AFBC captive units have one. This is *not*
  modelled as manual grab sampling: a 30-min manual lab cadence is unrealistic
  (real manual sampling is shift-based), and adopting a fake cadence to make a
  trend visible would be the same failure mode as tuning K.
- **If the modelled plant has only manual shift sampling, conductivity cannot
  serve as an in-episode discriminator at all, and the tier-C modality claim
  for the affected episodes would need rethinking.** Recorded so the assumption
  is visible.
- Pre-episode history at -24 h, -16 h, -8 h, 0 to anchor the baseline band.
- Logged samples every 30 min of episode time.
- One `operator_requested` grab sample 10-20 min (seeded jitter) after the
  first ALARM-severity condition - realistic under either sampling regime.
- Analyser / lab noise **ASSUMED** +/-2 %.

### 5.6 Format note

`records.json` gains `water_chemistry_log` (see `notes_gen.build_records`).
**The agent's retrieval layer (L3) does not yet read `records.json` - known
bug 3 in CLAUDE.md, fixed in a later stage.** The format is frozen now so the
later wiring is a plumbing change only.

---

## 6. Time constants  *(FITTED values filled during the `sim.py` rewrite + regen)*

Deterministic tag relaxation stays **fast**, per the CLAUDE.md stage-1
correction ("do NOT slow bed or ms relaxation by 30-170x"; the steam->bed
increment cross-correlation peaks at lag 0). The long autocorrelation in the
real record comes from slow **driver** drift (section 7), not slow tag
relaxation.

| tau | provenance (sub-model 2) |
|---|---|
| drum pressure | **DERIVED** (physical). `tau_p ~ E_P / (dQ/dp)`. `E_P = 679 J/Pa` from water + metal thermal mass * `dTsat/dp` (§4.4). **NOT** fitted to `drum_pressure.ar1.timescale_minutes = 44.53` -- that is inherited input slowness (correction 1); an ar1 fit wants `E_P ~ 1.3e4`, 19x higher. |
| bed temperature | **DERIVED**: `tau_bed = M_bed*c_bed / G_BED_EFF = 139 s` (~2.3 min). Uses the SAME conductance as the bed gains (§4.3); an earlier version divided by `G_FG` (356 s) and was 2.6x too slow for its own sensitivity. Fast, per the CLAUDE.md stage-1 correction. `bed_temp_avg.ar1 = 254.9 min` is inherited from load variation. |
| main steam temperature | **DERIVED**: `tau_ms = M_sh_metal*c_steel / (W_steam*cp_steam) = 55 s`. Fast. `ms.ar1 = 706.7 min` likewise inherited. |

Achieved autocorrelation vs the fingerprint (esp. `drum_pressure` -> 44.53 min)
is reported after **sub-model 4**, once the OU drivers supply the slow wander.

---

## 7. Noise and driver-drift model  *(FITTED values filled during regen)*

### 7.0 Shrink-and-swell gain `G_sw`  --  DERIVED-from-ASSUMED (sub-model 1)

`sim.py` adds a fast pressure-driven term to the indicated drum level on top of
the collapsed-liquid level from the mass balance:

```
level_swell = -G_sw * (p - p_nom)          [p in kgf/cm2(g)],  G_sw > 0
G_sw = (ALPHA_SW * V_SW_M3) / (rho_g(p_nom) * VOL_PER_PCT_M3)
       * d(rho_g)/dp|p_nom * (Pa per kgf/cm2)
```

**Structure - DERIVED.** A void volume `V_void = ALPHA_SW * V_SW_M3` seen by the
level tap holds steam whose density tracks drum pressure. On the seconds
timescale the void *steam mass* is ~constant, so
`V_void = m_void / rho_g(p)` gives `dV_void/dp = -(V_void/rho_g) * d(rho_g)/dp`,
and `dL[%] = dV_void / VOL_PER_PCT_M3`. `d(rho_g)/dp > 0` -> pressure UP
compresses the voids -> level DOWN (shrink); pressure DOWN (load increase) ->
voids expand -> level UP (swell). Sign cross-checked against RCA Case 5
("shrink then swell" on a load rejection) and Case 4 (load increase ->
"level rises on swell").

**Inputs - ASSUMED, cannot be fitted.** `fingerprint.json` has no `drum_level`
column (`FINGERPRINT.md` line 26), so neither factor of `V_void` can be fitted.

| symbol | value | range | provenance |
|---|---|---|---|
| `V_SW_M3` | 9.0 m3 | 4 - 14 m3 | **ASSUMED**: drum-span water (4.31 m3) + the void-bearing part of the ~9.4 m3 evaporator-circuit water |
| `ALPHA_SW` | 0.20 | 0.08 - 0.35 | **ASSUMED**: span-averaged void fraction (drum region ~0.03, upper risers ~0.4) |

**Result** (`python3 -m data.generator.sim`, IF97 `rho_g(66 kg/cm2(g)) = 34.06`,
`d(rho_g)/dp = 5.723e-6 kg/m3/Pa`):

| quantity | value |
|---|---|
| **`G_sw` nominal** | **0.780 %/(kgf/cm2)** |
| `G_sw` band over the ASSUMED ranges | **0.139 - 2.123** (~15x) |

Independent re-derivation: `d(rho_g)/dp` recomputed from `rho_g` at exactly
`p_nom +/- 1 kgf/cm2` (bracket ~98x wider than the code's 1 kPa central
difference) gives `G_sw = 0.780`, **0.00 % difference** - a genuine curvature
check on `rho_g(p)`.

**SCOPE - stated limitation.** `G_sw` captures **only the density effect**:
voids compressing / expanding as `rho_g` tracks `p`. It **omits the
void-fraction change driven by steaming rate** - more firing makes more bubbles
and swells the level *at constant pressure*. Verified magnitude: a 5 kg/cm2
pressure excursion gives **~3.9 % of span** from the density term modelled here,
against **10 - 20 %** typical for real swell on a large load step. So the
modelled term is plausibly the **minority contribution**. It affects **families
A and B**, where the indicated level then reads something other than true water
mass; the L1 water-balance residual absorbs the difference and must be tuned
with that in mind. A steaming-rate void term is a candidate for a later stage;
not added now because its coefficient would itself be unfittable ASSUMED.

### 7.1 Driver drift - two-timescale Ornstein-Uhlenbeck on `load_demand`  (sub-model 4, built 2026-09-01)

The multi-hour wander is produced by an OU perturbation on `load_demand`, not by
tag relaxation. A SINGLE shared process is what makes the tags
cross-correlated - no per-tag noise injection, so the balances still close.

```
load = d.load_demand + ou_fast + ou_slow            [t/h]
ou_x(t+dt) = ou_x(t)*exp(-dt/tau_x) + sigma_x*sqrt(1 - exp(-2 dt/tau_x))*N(0,1)
```

| component | tau | sigma | provenance |
|---|---|---|---|
| `ou_fast` | 26.62 min | 1.4 t/h | **FITTED**: `steam_flow.ar1.timescale_minutes`. The single-AR1 fit; a real record needs a slower part too (steam_flow autocorr is still 0.50 at 1 h). |
| `ou_slow` | 3.0 h | 2.1 t/h | **ASSUMED** (tau 2-6 h) + **FITTED** split: `sqrt(1.4^2 + 2.1^2) = 2.52 t/h` targets emitted `steam_flow` sd ~ 2.5 (fingerprint cov 3.98 % of 67 = 2.67) and 1 h autocorrelation ~ 0.50 (fingerprint 0.502). |

**Achieved** (3 x 30-h no-fault runs, `sim.py _ou_recheck`):

| tag | sd (fp) | acf 5m/30m/1h (fp) |
|---|---|---|
| `steam_flow` | 2.34 (2.38) | 0.91/0.59/0.35  (0.88/0.65/0.50) |
| `bed_temp_avg` | 6.07 (10.68) | 0.97/0.66/0.39  (0.85/0.57/0.42) |
| `ms_temperature` | 1.16 (4.33) | 0.93/0.62/0.37  (0.77/0.41/0.26) |
| `drum_pressure` | 0.22 (0.77) | 0.23/0.00/0.00  (0.44/0.15/0.05) |

Cross-correlation `pearson_level` steam<->bed **+0.95** (fingerprint +0.87). See
`reports/stage3_submodel4.md` for the bed/ms/pressure sd gaps and the
steam<->pressure sign gap (recorded, not forced - CLAUDE.md).

> **Stage 4 update.** The single shared load OU alone came out at steam<->bed
> **+0.98** (bed 96 % explained by steam vs 76 % in the real trace). Corrected by
> adding independent per-subsystem process disturbances - see 7.3. After that:
> steam<->bed **+0.88**, bed R^2 on steam **0.76**, bed 30-min acf **0.57** (fp
> 0.571), steam_flow sd **2.06**. `_ou_recheck` now asserts steam<->bed in
> [0.60, 0.95] (both bounds); mutation M11 kills the process OU and is caught.

### 7.3 Independent per-subsystem process disturbances  (stage 4, 2026-09-01)

One shared `load_demand` OU couples every tag through one input, over-correlating
them. A real plant carries disturbances local to each subsystem (fuel-size and
mixing variation in the bed, calorific scatter between coal parcels, flue-gas /
air-side turbulence) that load does not explain. Modelled as three **independent**
single-timescale OU processes on the *effective* driver values inside `step()` -
`heat_absorption`, `coal_cv_factor`, `primary_air` - never mutating `self.d.*`
(the fault schedule and emitted ground truth stay exact). They enter the SAME
energy balance as the scheduled drivers, so both balances still close; they
decorrelate bed from steam because none of them touches `load_demand`. The
`primary_air` term is applied as a signed `- K_PA * ou_pa` so the `min(1, .)`
rectifier does not bias the bed upward in normal operation.

| symbol | value | provenance | range |
|---|---|---|---|
| `TAU_PROC_S` | 25 min | **ASSUMED** - combustion / air-side disturbance timescale | 15 - 45 min |
| `SIGMA_PROC_HA` | 0.0022 | **ASSUMED**, calibrated so 4 x 20-h no-fault -> steam<->bed +0.87 | 0.001 - 0.004 |
| `SIGMA_PROC_CV` | 0.0022 | **ASSUMED**, same calibration | 0.001 - 0.004 |
| `SIGMA_PROC_PA` | 0.0035 | **ASSUMED**, same calibration | 0.0015 - 0.006 |

The (tau, sigma) pair is a ridge, not a point (tau 20 or 35 min gives the same
+0.87 with a rescaled sigma). The fingerprint has no per-subsystem disturbance
spectrum to pin it further. Achieved value is **reported, not tuned to hit**.

### 7.2 Measurement noise - white, added at emit only  (sub-model 4, built 2026-09-01)

`EMIT_NOISE` in `sim.py`. Dropped from the old ad-hoc dict (bed 1.2, ms 0.8 -
~20x too large) to the fingerprint white-noise estimates
(`tags.<tag>.measurement_noise.hf_noise_est_2nd_diff`):

### 7.2 Measurement noise - white, added at emit only

Dropped from the old ad-hoc dict to the fingerprint's white-noise estimates
(`tags.<tag>.measurement_noise.hf_noise_est_2nd_diff`):

| tag | new sigma | provenance |
|---|---|---|
| `bed_temp_avg` | 0.057 degC | **FITTED** (absolute - same instrument class) |
| `ms_temperature` | 0.026 degC | **FITTED** (absolute) |
| `steam_flow` | 0.109 t/h | **FITTED** |
| `drum_pressure` | **0.019** kg/cm2 | **DERIVED** from the **FITTED** 0.028 kg/cm2 by span scaling (transmitter noise prop span; 67 / 99.6) |
| `drum_level` | 0.15 % (unchanged) | **ASSUMED** - the fingerprint has no `drum_level` column (`FINGERPRINT.md` line 26) |
| `feed_water_flow` | 0.109 t/h | **ASSUMED** - no reference column; mirrors `steam_flow` |

Noise is added to the emitted copy only; it does not feed the controllers.
Achieved emitted per-sample increment sd (3 x 30-h runs): bed 0.085, ms 0.039,
steam 0.200, pressure 0.031 - these are white noise + OU drift; the real
per-sample increments (calibration-gap table, CLAUDE.md) are bed 0.273 /
ms 0.067 / steam 0.188, so bed and ms still move a little less sample-to-sample
than the real trace. Recorded (report), not forced.

---

## 8. `records.json` water-chemistry log format

See sections 5.5 / 5.6. Schema (values **computed from the section 5.1 balance**,
never hand-authored; `kappa_fw = 12` -> baseline about 816 uS/cm):

```json
"water_chemistry_log": [
  {"t": -86400, "sample": "boiler_water", "conductivity_uS_cm": 812, "source": "analyser"},
  {"t": -57600, "sample": "boiler_water", "conductivity_uS_cm": 818, "source": "analyser"},
  {"t": -28800, "sample": "boiler_water", "conductivity_uS_cm": 815, "source": "analyser"},
  {"t": 0,      "sample": "boiler_water", "conductivity_uS_cm": 816, "source": "analyser"},
  {"t": 1800,   "sample": "boiler_water", "conductivity_uS_cm": 781, "source": "analyser"},
  {"t": 2900,   "sample": "boiler_water", "conductivity_uS_cm": 766, "source": "operator_requested"}
]
```

(The in-episode values above are the family-B `CBD stuck open` trajectory from
section 5.3; a no-fault episode stays flat at about 816.)

---

## 9. Load coefficient re-fit  *(done - stage 4, 2026-09-01)*

`configs/base.yaml checks.load_coef_degc_per_tph`: **2.75 -> 2.43**, and the
episodes were regenerated (`state_timeline_load_coef` = 2.43).

- **Level regression** `bed ~ 850 + coef*(steam - 67)` over the regenerated
  N01-N06: **2.43 degC/(t/h), R^2 0.82** - the form the DEVIATION band uses.
  This is the config value.
- The **slope regression** `slope(bed) ~ slope(steam)` is *window-length
  dependent*: 1.43 (5 min) / 1.97 (10 min) / 2.41 (20 min) / 2.88 (40 min) /
  3.16 (55 min). This is the `tau_bed = 139 s` lag - over a short window the
  contemporaneous steam slope is a poor proxy for the lagged load the bed
  responds to. It is **not** measurement-noise attenuation: the errors-in-
  variables factor from the emit noise on `steam_flow`
  (`SE(slope) = 4.3e-4 t/h/min` at 40 min) is **0.99995**.
- No single coefficient is correct for the 5-min (`balance`, `_patterns`) and
  55-min (`rates_long`) slope checks. Their thresholds are set from the
  **measured** no-fault distribution *at* coef 2.43, so the FP rate is
  calibrated regardless of the residual normalisation error.
- **2.75 (the earlier value) is withdrawn** - it had been chosen because it
  matched `K_BED_LOAD`, which is circular (the same defect as `sim.py` and
  `l1_checks` once sharing K = 0.22). `K_BED_LOAD` is now decoupled.
- Still **NOT** the fingerprint's 3.90 (different boiler / operating point).
- Consequence for family E: the no-fault load-normalised bed slope over 55 min
  has p99.7 ~ 0.285, ~1700x the pure emit-noise slope SE - i.e. the spread is
  real low-frequency bed wander, and family E (55-min slope 0.19-0.24) is below
  it. Family E is masked by the drivers' own wander, not by the normalisation
  choice. See `reports/stage4_regeneration.md` disagreement 1.

The new value and R2 go in the before/after report and here on completion.

### 9.1 Raw vs load-normalised bed slope in the SYMBOLIZER  *(stage 6, 2026-09-02)*

The case signatures in `data/kb/case_library.json` were transcribed from
`docs/Boiler_Failure_Case_Studies_RCA.pdf`, which describes bed temperature **as
an engineer reads the trend on the DCS - raw**, with load whatever it happened
to be. `l1_symbolize` builds its `bed_temp_avg` triple from
`orchestrator._slopes`, which as of stage 6 is the **raw** 10-min slope.

Stage 6 tried banding it on the **load-normalised** slope
(`slope(bed) - load_coef*slope(steam)`, coef 2.43, the §9 quantity) so the
descriptor deadband would not have to clear the load-following component of the
no-fault bed wander (OLS `slope(bed) ~ slope(steam)` R^2 0.37 on the no-fault
10-min window). It was reverted (mock `Q2_top1` 0.334 -> 0.173), but the
finding stands and is recorded here:

- **Raw and normalised agree at steady load** and diverge when load moves.
- **They can differ in SIGN under a moving load.** Family C (fuel cap ->
  pressure sag -> turbine throttles -> `slope(steam) < 0`): the normalisation
  term `-coef*slope(steam)` is then **positive** and can push a genuinely
  falling bed toward flat or up. Measured on `ep_C02_feeder_trip`: raw bed
  slope mean **-0.253 degC/min** (falling, matches RCA-06 / RCA-14 `bed DOWN`),
  load-normalised mean **+0.044 degC/min** (rising - wrong sign). `ep_C01`
  -0.135 -> -0.019; `ep_C05` -0.211 -> -0.053 (both normalise toward flat).
  This is the same inversion `_load_normalised_bed_slope` structurally avoids by
  only ever testing it against a **positive** rise threshold (family E / D
  drift), never using it to establish a DOWN direction.
- If bed normalisation is revisited for the symbolizer, it must be gated to the
  rising case, or `steam_flow` must itself be flat, or the family C bed triple
  will contradict the RCA case it is meant to match. **Do not adjust the case
  signatures to fit a normalised symbolizer** - the PDF is the reference.

Full numbers: `reports/stage6_band_edges.md`.
