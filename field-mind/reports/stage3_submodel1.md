# Stage 3 / sub-model 1 — water balance + shrink-and-swell — report

## Status
DONE — `sim.py` water side rewritten SI-internally: collapsed-liquid **mass**
`_m_liq` [kg] integrated in kg/s, mapped to indicated `drum_level` % through the
geometry linearisation (identically `geometry.K_level(p)`), plus a fast
pressure-driven shrink-and-swell term derived from `d(rho_g)/dp`. Energy /
bed / ms / noise blocks are the pre-rewrite code, fed by the new water side;
replaced in sub-models 2 and 4. Episodes NOT regenerated.

## Verification
| step | result |
|---|---|
| ran the module | `python3 -m data.generator.sim` — exit 0. Self-test passes; all 5 direction checks OK; G_sw re-derivation 0.00 %; mutation check both mutations caught. One expected `RuntimeWarning` (inventory clamp firing in the family-A direction check — that is the guard working). |
| self-tests | `_self_test()` — 3 invariant groups pass: (1) no-fault 60 min stays within ±4 % of 50 and never saturates the clamp; (2) `M_LIQ_FLOOR_KG` == `rho_f(p_nom)·drum_water_volume_m3(0.0)` exactly; (3) severe feed shortage (eff 0.55) pins `_m_liq` at the 1832 kg floor, sets `m_liq_saturated="floor"`, fires the warning, drives indicated level to ~0 — does **not** integrate a negative/runaway mass. |
| independent re-derivation | **G_sw** (shrink-and-swell gain). Code path: `steam_tables.drho_g_dp_kg_m3_per_Pa` (central difference, dp = 1 kPa). Independent: `rho_g` evaluated at exactly `p_nom ± 1 kgf/cm²` (±98066.5 Pa, ~98× wider bracket, different endpoints), G_sw rebuilt by hand from the block-comment formula. **Code 0.7800, independent 0.7800, relative difference 0.00 %** — a genuine curvature check on `rho_g(p)`. `rho_g(p_nom) = 34.060 kg/m³`, `d(rho_g)/dp = 5.723e-6 kg/m³/Pa`. |
| mutation check | Source of `step()` mutated in a fresh namespace, tests still see the real module constants. **M1** — drop `+ level_swell` from the compose line (diag still logs real `level_swell`): caught by **(a) exact-composition** (`|indicated_clean − (liquid+swell)|` jumps from 0 to ~3), **(b) trace-slope** (emitted gain −0.011 vs −0.780), and direction-1 sign. **M2** — flip `level_swell` sign (mutated code's own diag stays internally consistent): caught by **(b) trace-slope** (+0.798 vs −0.780) and direction-1 sign; **not** (a), correctly — composition is still exact. No mutation is caught by nothing; no test is inert. Baseline (unmutated) passes all three. |

Note on the `_headline_recheck` ρ_g figure — **discrepancy recorded, not
explained.** Two independent computations of ρ_g at the same operating point
disagree by 0.019 %:

| | pressure | ρ_g | d(ρ_g)/dp | G_sw |
|---|---|---|---|---|
| this code | 6 573 714 Pa | 34.0600 kg/m³ | 5.7232e-6 kg/m³/Pa | 0.7800 |
| reviewer (independent) | 6 573 714 Pa | 34.0536 kg/m³ | 5.7126e-6 kg/m³/Pa | 0.7787 |

The **pressures are identical** (`kgfcm2g_to_Pa(66.0) = 6573714.0` in both, same
`_ATM_PA = 101325`), so it is **not** the gauge→abs conversion — an earlier
draft attributed it to the atmospheric constant; that was a guess and it is
wrong (a 100000 Pa atmosphere gives ρ_g = 34.046, *further* from the reviewer's
value). The difference is in the Region 2 evaluation of ρ_g at `(p, Tsat(p))`.
This code's ρ_g comes from `_region2` at `Tsat_K(p) = 281.61 °C`, which is
anchored to the IF97 published verification values (`steam_tables._self_test`,
Table 15, rel-tol 1e-7) and matches the `DERIVATIONS.md §2` operating-point
table and its coarse-table cross-check. The source of the reviewer's 34.0536 is
not identified (possibly IAPWS-95 vs IF97, or a different saturation
temperature). **0.17 % in G_sw against a 15× ASSUMED band — immaterial to any
result; carried as an open item.**

## Direction checks
Verified against the **emitted trace** (not the formula). Checks 1, 2, 5 key on
pressure movement from the **pre-rewrite energy block** (`PRESS_GAIN = 4.0`,
unsourced) → **PROVISIONAL**, re-run after sub-model 2.

| # | claim | expected | measured | verdict |
|---|---|---|---|---|
| 1 | load ↑ → pressure falls → voids expand → level rises | swell (offset > 0), trace gain ≈ −G_sw | load +10 t/h: p peak 61.91 (dp −3.89), emitted offset **+3.066 %** vs −G_sw·dp +3.035 %; whole-run trace slope **−0.786** vs −0.780 | **OK** (PROVISIONAL) — fat margin |
| 2 | load ↓ → pressure rises → voids collapse → level falls | shrink (offset < 0), trace gain ≈ −G_sw | load −10 t/h: p peak 66.60 (dp **+0.44**), emitted offset **−0.356 %** vs −G_sw·dp −0.342 %; whole-run trace slope **−0.816** vs −0.780 | **OK** (PROVISIONAL) — **thin margin**: the pre-rewrite energy transient on a load *drop* decays fast, so dp is only +0.44 and the extreme-sample offset (−0.36 %) sits ~2.4σ above the 0.15 % emit noise. The whole-run trace slope (−0.816, 600 samples, tol 0.10) is the load-bearing evidence here, not the extreme sample. |
| 3 | family A (feed valve capped) → feed < steam, level falls | feed < steam, level ↓ | feed 57.1 < steam 69.0 t/h; level 50.0 → 1.0 % (then clamped) | **OK** — pure water side |
| 4 | family B (leak) → controller raises feed above steam, level only holds | feed > steam, level holds | feed 73.4 > steam 68.4 t/h (gap +5.0); level holds at 50.0 % | **OK** — pure water side |
| 5 | fuel availability capped → pressure sags → turbine throttles → steam falls | p ↓, steam ↓ | fuel cap 0.75: pressure 66.0 → 47.3 kg/cm²; steam 67.5 → 54.3 t/h | **OK** (PROVISIONAL) — see "Disagreements" re: magnitude |

Case-wording cross-check of the swell sign (RCA): **Case 5** "shrink then swell"
on a load rejection (pressure ↑ → shrink first) ✓; **Case 4** load increase →
"level rises on swell" ✓; **Case 2** load decrease → "+12 % level step" is
**controller overfill on a sluggish steam-flow transmitter** (RCA's own
mechanism section), not the void response — the void term shrinks on that
pressure rise. The earlier plan's attribution of the +12 % to shrink/swell
physics had the sign wrong.

## Constants introduced
| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `V_DRUM_50_M3` | 4.314 m³ | **DERIVED** — `geometry.drum_water_volume_m3(50)` | — (geometry-fixed) | mass↔level map baseline |
| `VOL_PER_PCT_M3` | 0.038025 m³/% | **DERIVED** — `geometry.water_surface_area_m2(50)·LEVEL_SPAN_M/100` | — | mass↔level gain (= K reciprocal) |
| `M_LIQ_FLOOR_KG` | 1832 kg | **DERIVED** — `rho_f(p_nom)·drum_water_volume_m3(0.0)` (drum water at 0 % indicated, **not** an empty drum) | — | clamp floor; boiler-water inventory floor for sub-model 3 |
| `M_LIQ_CEIL_KG` | 4616 kg | **DERIVED** — `rho_f(p_nom)·drum_water_volume_m3(100.0)` | — | clamp ceiling |
| `V_SW_M3` | 9.0 m³ | **ASSUMED** — drum-span water (4.31) + void-bearing part of the ~9.4 m³ evaporator-circuit water | 4 – 14 m³ | `G_sw` magnitude → shrink/swell excursion size in families A, B |
| `ALPHA_SW` | 0.20 | **ASSUMED** — span-averaged void fraction (drum ~0.03, upper risers ~0.4) | 0.08 – 0.35 | `G_sw` magnitude, as above |
| `G_SW_PCT_PER_KGFCM2` | 0.780 %/(kgf/cm²) | **DERIVED-from-ASSUMED** — structure from `d(rho_g)/dp`, inputs `V_SW_M3`/`ALPHA_SW` | **0.139 – 2.123** (~15×) | indicated drum_level offset per kg/cm² of pressure deviation; families A, B |

Not fittable: `fingerprint.json` has no `drum_level` column, so `V_SW_M3` and
`ALPHA_SW` (hence `G_sw`) stay ASSUMED with ranges. Recorded in `DERIVATIONS.md
§7.0`.

## Numbers that changed
| quantity | old | new | ratio | downstream |
|---|---|---|---|---|
| water-balance K | `0.22` %/min·t/h (bare literal in `sim.py`) | `geometry.K_level(p)` ≈ **0.586** at 66 kg/cm²(g), pressure-dependent | **2.67×** | `configs/base.yaml checks.balance.level_gain_pct_per_min_per_tph` (still 0.22 — **not** retuned here); `episode_build.state_timeline` DEVIATION band; families A/B reach thresholds ~2.7× sooner |
| feed valve max | `FEED_VALVE_MAX_TPH = 82.0` (bare literal) | `geometry.feed_valve_max_tph()` = **81.60** | 0.995× | feed controller saturation point; family-A deficit rate |
| shrink/swell | absent (implicit in `PRESS_GAIN` energy only) | explicit `level_swell = −G_sw·(p − p_nom)`, `G_sw = 0.780` | new term | indicated `drum_level` in every episode; L1 water-balance residual tuning |
| internal units | t/h, %, kg/cm² throughout | **SI** (kg, s, Pa, m³) internally; engineering units only at emit | — | the six emitted columns unchanged (same 7-field CSV incl. `t`) |

Dead constants `K_LEVEL` / `FEED_VALVE_MAX_TPH` / the literal `0.22` — confirmed
**gone** from `sim.py` (grep). Only surviving repo mentions are historical notes
in `geometry.py` docstrings.

## Disagreements recorded, not resolved
1. **Shrink/swell scope — density only.** `G_sw` captures the void *density*
   effect (ρ_g tracking p) and **omits the void-fraction change from steaming
   rate** (more firing → more bubbles → swell at constant pressure). Magnitude:
   a 5 kg/cm² excursion gives ~3.9 % of span from the density term modelled
   here, vs **10 – 20 %** typical for real swell on a large load step. So the
   modelled term is plausibly the **minority contribution**. Not added now
   because its coefficient would itself be unfittable ASSUMED. Recorded in
   `DERIVATIONS.md §7.0`. Affects families A and B (indicated level ≠ true water
   mass).
2. **Fuel-cap pressure collapse magnitude (PROVISIONAL).** Direction check 5:
   66.0 → 47.3 kg/cm² over 40 min is a **28 % collapse**; a real boiler trips on
   low drum pressure well before that (alarm 55; RCA Case 6 treats 66 → 58 over
   ~1 h as a serious event needing burner support). This is pre-rewrite
   `PRESS_GAIN` energy, unsourced — flagged as a **magnitude to re-check after
   sub-model 2**, not a validated result.
3. **Clamp / conductivity artifact (for sub-model 3).** `M_LIQ_FLOOR_KG` =
   1832 kg is drum water at 0 % indicated, not an empty drum. Once the clamp
   engages (severe family A), `boiler_water_inventory_kg` pins at ~8832 kg
   (1832 drum + 7000 circuit), so a near-fixed solids mass stops concentrating —
   in exactly the severe family-A case where rising conductivity is the Case-1
   discriminator. The conductivity trace will therefore **under-state** the
   concentration excursion after the level pins. `DERIVATIONS.md §5` updated.
   Since §5.3 already rates family-A conductivity as a weak signal
   (+3 – 15 %, "not claimed as a positive Case 1 signature"), this makes an
   already-weak signal weaker — acceptable, but recorded.
4. **K vs RCA Case 1 (pre-existing, §3.7).** Derived K 0.586 is 2.2 – 2.6× the
   rate/deficit pair the RCA states for the same instant. Carried to the
   advisor with the level-transmitter-span / drum-GA question (§3.7a). Not
   touched here.

## Blocked / needs a decision
None new. Open advisor questions unchanged: (a) K vs RCA Case 1 — obtain the
level-transmitter calibrated span and the drum GA drawing (`DERIVATIONS.md
§3.7a`); (b) whether the modelled plant has an online CBD conductivity analyser
(`§5.5`) — affects whether conductivity is a valid in-episode discriminator at
all.

## What I could not verify
- **`V_SW_M3`, `ALPHA_SW`** — pure engineering judgement, no reference. Would
  need a drum-level trace from the case-study plant (or a validated
  circulation-circuit void model) to check. The 15× band on `G_sw` is the
  honest statement of that ignorance.
- **The steaming-rate void term is genuinely absent**, not just small — the
  3.9 % vs 10 – 20 % comparison says the modelled swell is likely a minority of
  real swell. Confirming requires plant transient data.
- **Direction checks 1, 2, 5** ride on unsourced pre-rewrite energy — the
  *swell response* is verified, the *pressure stimulus* driving it is not.
  Re-run after sub-model 2.
- Direction check 2's extreme-sample margin is thin (~2.4σ); the whole-run
  trace slope carries it. Not tuned further per instruction.
