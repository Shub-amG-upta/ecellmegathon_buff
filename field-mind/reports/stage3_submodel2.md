# Stage 3 / sub-model 2 — energy balance (coal stoichiometry + steam-table pressure integrator) — report

## Status
DONE — the pre-rewrite energy block in `sim.py` (`PRESS_GAIN = 4.0` and the five
bare `bed_target` literals `900 / 250 / 450 / 400 / -200`) is replaced by:

- **One coal ultimate analysis** (ASSUMED, ranges), from which **both** `air_st`
  (5.471 kg/kg) and `GCV` (4040 kcal/kg, Dulong) are DERIVED — not chosen
  independently. Dulong consistency (`GCV_kcal / air_st` in the fuel-independent
  700–780 kcal/kg-air band) is a self-test.
- **Steam-table duty**: `Q_steam_nom = 51.5 MW`, `m_coal_nom = 3.58 kg/s`,
  DERIVED from IF97 enthalpies.
- **Physical pressure integrator**: `dp/dt = (Q_evap_supply − W_steam·hfg) / E_P`
  with `E_P = 679 J/Pa` from boiler-water + metal thermal mass × `dTsat/dp`.
  **Not** fitted to the 44.53-min `drum_pressure` ar1 timescale (correction 1).
- **Bed / ms** first-order lags (τ_bed ≈ 139 s — see correction C1 below, was
  wrongly 356 s in the first cut; τ_ms ≈ 55 s — kept fast per the CLAUDE.md
  stage-1 correction) toward a target built from deviation gains, each DERIVED
  from the combustion mass/energy balance via a bed conductance
  `G_BED_EFF = G_FG + G_WW` (flue gas **plus** the in-bed water-wall clamp;
  `BETA_FURNACE` in `G_WW` is FITTED-to-expectation — correction C2 below).

> **Post-review corrections applied — see the "Corrections" section at the end.**
> C1 fixes a physics bug (`TAU_BED_S` used `G_FG` not `G_BED_EFF`); C2 relabels
> `BETA_FURNACE`. The verification tables below the fold are from the first cut;
> the current numbers are in the Corrections section and the sub-model-4 report.

Load-swing placeholder and emit-noise dict are still pre-rewrite (sub-model 4).
Episodes NOT regenerated. `configs/base.yaml` NOT touched.

## Verification
| step | result |
|---|---|
| ran the module | `python3 -m data.generator.sim` — **exit 0**. Full output below. One expected `RuntimeWarning` (family-A inventory-clamp guard, as in sub-model 1). |
| self-tests | `_self_test()` — **7 groups pass**: (1–3) sub-model-1 water-side invariants unchanged; (4) **coal consistency** `GCV_kcal/air_st = 739` in [700, 780]; the inconsistent `(3400, 5.46)` pair → 623, fails the band (asserted); (5) no-fault 90-min run holds pressure ±1 kg/cm2 and bed ±20 °C with **no fitted constant**; (6) `E_P = 679` within 2× of the physical band and >5× below the ar1-fit `1.3e4`; (7) family-E fouling (0.985, 220 min) bed max < 880 (crosses no limit) yet rises ≥ 8 °C. |
| direction checks | **8 / 8 OK** (checks 1, 2, 5 no longer PROVISIONAL; 6–8 are new). Table below. |
| independent re-derivation | **air_st**: code uses the mass-fraction shortcut; independent kmol-balance route gives **5.4673 vs 5.4707**, **0.06 %**. **GCV**: code uses Dulong; independent **Boie** correlation gives **4116 vs 4040 kcal/kg**, **1.88 %**. Both routes are different arithmetic on the same ultimate analysis. |
| mutation check | 4 energy mutations, all caught (details below). M3/M4 are the **E10 non-double-count proof**. |

### Full run output
```
sim self-test passed (clamp floor 1832 kg / ceil 4616 kg; GCV 4040 kcal/kg,
  air_st 5.47 kg/kg -> 739 kcal/kg-air; E_P 679 J/Pa).

direction checks (expected sign -> measured):
  load +10 t/h: p peak 57.92 kg/cm2 (dp -7.96, n=268); emitted offset +6.215 %
      vs -G_sw*dp +6.207 % (expect > 0, swell); trace slope -0.781 vs -0.780  [OK]
  load -10 t/h: p peak 67.37 kg/cm2 (dp +1.24, n=16); emitted offset -0.972 %
      vs -G_sw*dp -0.968 % (expect < 0, shrink); trace slope -0.790 vs -0.780  [OK]
  family A: feed 57.1 < steam 69.0 t/h, level 50.0->0.6 %  [OK]
  family B: feed 73.4 > steam 68.4 t/h (gap +5.0), level holds at 50.0 %  [OK]
  fuel cap 0.75: pressure 65.9->43.5 kg/cm2 (sag), steam 67.5->49.9 t/h (throttles)  [OK]
  leak 8 t/h: pressure 65.92->65.71 (falls), bed 850.9->814.4 (cools),
      feed 77.5 > steam 68.6  [OK]
  primary_air 1.00/0.90/0.80/0.70 -> bed 855/900/944/988 degC (monotone up as
      PA falls); valid range PA >= 0.64  [OK]
  load 67->77: ms 494.98 -> dip 492.79 (Case-4 fall) -> recover 499.59  [OK]
  all eight direction checks OK

independent re-derivation of the sub-model-2 headline numbers:
  air_st  code (mass shortcut) = 5.4707 kg/kg
  air_st  indep (kmol balance) = 5.4673 kg/kg   rel diff 0.06 %
  GCV     code (Dulong)        = 4040.3 kcal/kg
  GCV     indep (Boie)         = 4116.2 kcal/kg   rel diff 1.88 %
  GCV/air_st  = 739 kcal per kg-air (band (700.0, 780.0)); the inconsistent
      (3400, 5.46) pair -> 623, fails
  E_P = 679 J/Pa  (physical; correction band 800-1200); an ar1 fit to the
      44.53-min timescale wants ~13000 -- NOT used

sub-model-2 mutation check:
  baseline leak 8 t/h: bed drop +36.6 degC, pressure drop +0.21 kg/cm2
  M3 kill bed quench (Q_quench -> 0): caught -- bed drop -> -3.8 degC (gone),
      pressure still drops +0.42
  M4 kill feedwater-load term: caught -- pressure drop -> -0.24 (was +0.21),
      bed still drops +35.9
  M5 corrupt a Dulong coefficient (8080 -> 5080): caught -- GCV/air_st -> 511
      kcal/kg-air, outside (700.0, 780.0)
  M6 flip the E12 ms-vs-flow sign: caught -- ms change after load step ->
      +6.09 degC (no longer a dip)
```

### Cross-family behaviour (final constants, seed 1, pre-sub-model-4 noise)
| episode | bed °C [min,max] | pressure [min,max] | ms °C [min,max] | reads as |
|---|---|---|---|---|
| N normal 60 min | 847.8, 858.9 | 65.8, 66.1 | 492.9, 498.1 | in band |
| A feed-valve 0.77 | 847.8, 858.9 | 65.8, 66.5 | 492.9, 498.0 | heat side untouched ✓ |
| B leak 3.5 t/h | 832.0, 854.2 | 65.7, 66.1 | 487.4, 497.2 | bed sags ~18, feed>steam, level held ✓ |
| C fuel-cap 0.80 | 809.2, 855.0 | **46.2**, 66.1 | 485.5, 497.2 | pressure + bed fall together ✓ |
| C fuel-cap 0.62 (trip) | **772.5**, 854.1 | **35.8**, 66.1 | 477.4, 497.2 | severe (see disagreement 6) |
| D coal-CV 1.16 | 847.9, **981.2** | 65.8, 68.2 | 493.3, 541.0 | bed to the 940 trip ✓ |
| D coal-CV 1.24 | 847.9, **1042.6** | 65.8, 69.5 | 493.3, **562.3** | severe, ms past 550 alarm ✓ |
| D low-PA 0.80 | 847.9, **947.8** | 65.3, 66.1 | 493.3, 529.3 | bed to the 940 trip ✓ |
| D low-PA 0.84 (caught) | 847.9, 930.1 | 65.4, 66.1 | 493.3, 523.1 | bed to ALARM, not trip ✓ |
| E fouling 0.986 / 240 min | 847.9, **869.8** | 65.5, 66.1 | 493.2, 502.3 | crosses nothing ✓ |
| E fouling 0.989 / 200 min | 847.1, 862.0 | 65.6, 66.1 | 493.1, 499.8 | crosses nothing; see disagreement 5 |
| E fouling 0.985 / 180 min | 847.9, 865.2 | 65.5, 66.1 | 493.3, 501.7 | crosses nothing ✓ |

## Direction checks
| # | claim | expected | measured | verdict |
|---|---|---|---|---|
| 1 | load ↑ → p falls → voids expand → level rises (swell) | offset > 0, trace slope ≈ −G_sw | dp −7.96, offset +6.215 % vs −G_sw·dp +6.207 %, slope −0.781 vs −0.780 | **OK** (no longer PROVISIONAL) |
| 2 | load ↓ → p rises → voids collapse → level falls (shrink) | offset < 0, slope ≈ −G_sw | dp +1.24, offset −0.972 % vs −0.968 %, slope −0.790 | **OK** (no longer PROVISIONAL) |
| 3 | family A (feed valve capped) → feed < steam, level falls | feed<steam, level↓ | feed 57.1 < steam 69.0, level 50 → 0.6 % | **OK** |
| 4 | family B (leak) → feed > steam, level only holds | feed>steam, level holds | feed 73.4 > steam 68.4 (+5.0), level 50.0 | **OK** |
| 5 | fuel availability capped → p sags → turbine throttles → steam falls | p↓, steam↓ | p 65.9 → 43.5, steam 67.5 → 49.9 | **OK** (no longer PROVISIONAL) |
| 6 | **E10** tube leak → drum pressure **falls** AND bed **cools** | p↓, bed↓, feed>steam | p 65.92 → 65.71 (−0.21), bed 850.9 → 814.4 (−36), feed 77.5 > steam 68.6 | **OK** |
| 7 | **E8** low primary air → bed **hotter**, monotone over the valid range | bed(PA) monotone ↑ as PA↓; PA_MIN ≈ 0.64 | PA 1.00/0.90/0.80/0.70 → bed 855/900/944/988; PRIMARY_AIR_MIN = 0.636 | **OK** |
| 8 | **E12** sharp load rise → ms transient **dip** (RCA Case 4) then recovers | ms dips ≥ 1 °C in first 5 min, then recovers | ms 494.98 → dip 492.79 (−2.2) → recover 499.59 | **OK** |

RCA case-wording cross-checks: Case 11 (tube leak) "bed temperature dropped
sharply" — matches check 6 sign. Case 4 (sharp load rise) "MS temperature fell
from 492 to 428 degC in about eight minutes" — matches check 8 sign (our
transient dip is smaller; see disagreement 3).

## Constants introduced
| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `COAL_ULT` (C/H/O/N/S/ash/M) | .415/.029/.075/.009/.0045/.335/.1325 | **ASSUMED** — one as-fired analysis, Indian washery-reject/imported blend | per-element, DERIVATIONS §4.1 | `air_st`, `GCV`, `m_coal_nom`, every energy gain |
| `AIR_ST` | 5.471 kg/kg | **DERIVED** from `COAL_ULT` (`(2.667C+7.937H+0.998S−O)/0.2315`) | 5.1–5.9 over the analysis ranges | flue-gas mass flow → `G_FG`, `K_PA`, `PRIMARY_AIR_MIN` |
| `GCV_KCAL_PER_KG` | 4040 kcal/kg (1.691e7 J/kg) | **DERIVED** from `COAL_ULT` (Dulong `8080C+34500(H−O/8)+2240S`) | 3600–4400 over the ranges | `m_coal_nom`; magnitude of `Q_released` |
| `ETA_BOILER` | 0.85 | **ASSUMED** | 0.80–0.87 | `m_coal_nom`, every energy gain |
| `LAMBDA_EXCESS` | 1.25 | **ASSUMED** | 1.15–1.35 | `G_FG`, `K_PA`, `PRIMARY_AIR_MIN` |
| `PA_AIR_FRACTION` | 0.55 | **ASSUMED** | 0.45–0.65 | `K_PA`, `PRIMARY_AIR_MIN` |
| `C_PG` | 1150 J/kg/K | **ASSUMED** | 1100–1250 | `G_FG` → bed gains, `K_PA` |
| `BETA_FURNACE` | 0.45 | **ASSUMED** | 0.35–0.55 | `G_WW` → `K_COMB/K_FUEL/K_CV`, quench |
| `T_FW_C` | 150 °C | **ASSUMED** | 140–160 | duty (`m_coal_nom`) magnitude only |
| `T_FLUE_REF_C` | 200 °C | **ASSUMED** | 150–260 | `(T_bed−T_ref)` span in `K_PA` |
| `M_DRUM_METAL_KG` | 18000 kg | **ASSUMED** | 12000–26000 | `E_P` (pressure speed) |
| `M_BED_KG`, `C_BED` | 11000 kg, 1000 J/kg/K | **ASSUMED** | 8000–16000, 900–1100 | `τ_bed` only |
| `M_SH_METAL_KG`, `C_STEEL`, `_CP_STEAM` | 6000 kg, 490, 2900 J/kg/K | **ASSUMED** | 3500–10000, 470–510, 2800–3500 | `τ_ms` only |
| `K_MS_BED` | 0.35 | **ASSUMED** | 0.28–0.40 | ms sensitivity to bed temp |
| `K_MS_FLOW` | 0.45 °C/(t/h) | **ASSUMED** | 0.2–0.8 | ms fall per t/h of extra steam (Case 4 direction) |
| `K_BED_LOAD` | 2.75 °C/(t/h) | **ASSUMED** — anchored to `base.yaml load_coef_degc_per_tph` (unchanged); fingerprint 3.9 is the upper bound | 2.0–4.0 | load-following bed rise (all normal + fault episodes) |
| `Q_STEAM_NOM` | 51.5 MW | **DERIVED** — IF97, `18.61 kg/s·(h_ms−h_fw)` | — | `Q_fuel_nom`, `m_coal_nom` |
| `M_COAL_NOM` | 3.58 kg/s (12.9 t/h) | **DERIVED** | — | absolute `Q_released` scale |
| `G_FG_NOM` | 3.09e4 W/K | **DERIVED** (`m_fluegas·C_PG`) | — | `K_PA` normalisation, part of `G_BED_EFF` |
| `G_WW_NOM` | 4.80e4 W/K | **DERIVED** (`BETA_FURNACE·Q_fuel_nom/(T_bed−Tsat)`) | — | part of `G_BED_EFF` |
| `G_BED_EFF` | 7.89e4 W/K | **DERIVED** (`G_FG+G_WW`) | — | denominator of `K_COMB/K_FUEL/K_CV` and the leak quench |
| `K_COMB` = `K_FUEL` = `K_CV` | 768 K | **DERIVED** (`Q_fuel_nom/G_BED_EFF`) | ~550–1050 over input ranges | bed rise per unit relative heat imbalance (families C, D, E) |
| `K_PA` | 447 K | **DERIVED** (`PA_AIR_FRACTION·LAMBDA_EXCESS·(T_bed−T_ref)`) | ~250–650 | bed rise as PA falls (family D) |
| `PRIMARY_AIR_MIN` | 0.636 | **DERIVED** (`1−(λ−1)/(f_PA·λ)`) | — | states the E8 valid floor; nothing is driven below it |
| `E_P` | 679 J/Pa | **DERIVED** (`M_bw·cp_bw·dTsat/dp + M_metal·cp_steel·dTsat/dp + V_steam·dρ_g/dp·hfg`), `cp_bw`/`dTsat/dp` from IF97 | ~0.5–1.0e3 over `M_metal` range | drum-pressure response speed |
| `TAU_BED_S` | 356 s | **DERIVED** (`M_bed·c_bed/G_FG`) | — | bed lag |
| `TAU_MS_S` | 54 s | **DERIVED** (`M_sh·c_steel/(W_steam·cp_steam)`) | — | ms lag |
| `_H_F_ATM`, `_H_FG_ATM` | 419 kJ/kg, 2257 kJ/kg | **DERIVED** — IF97 saturation at 1 atm | — | leak flash fraction (`flash_x ≈ 0.366`) |

## Numbers that changed
| quantity | old | new | ratio | downstream |
|---|---|---|---|---|
| pressure integrator | `PRESS_GAIN = 4.0` kg/cm2 per min per rel. imbalance (ASSUMED, unsourced) | physical `E_P = 679 J/Pa`, `dp/dt = (Q_evap_supply − W_steam·hfg)/E_P` | n/a (units changed) | drum-pressure dynamics in every episode; L1 `rates.drum_pressure` and `balance.p_falling` tuned against the old gain — **not** retuned here |
| bed `K_COMB` (heat imbalance) | literal `900` | **768** (DERIVED) | 0.85× | family E bed-drift size |
| bed `K_FUEL` (fuel deficit) | literal `250`, term `heat_released − 1` | **768**, term `min(0, fuel_avail − fire_for_load)` — fires only while the cap bites | — | family C bed sag (now mostly the load-following term) |
| bed `K_CV` (high CV) | literal `450` | **768** (DERIVED) | 1.71× | family D bed peak (1.16 → ~981 °C, trips) |
| bed `K_PA` (low PA) | literal `400` | **447** (DERIVED) | 1.12× | family D low-PA bed peak (0.80 → ~948 °C) |
| leak quench | literal `−200·leak_rel` (≈ 3.0 °C per t/h) | `−Q_quench/G_BED_EFF` with saturated-water flash `x ≈ 0.366` (≈ 5.0 °C per t/h) | 1.7× | family B bed sag |
| new term | (none) | `K_BED_LOAD·(steam − 67)` load-following bed rise | new | bed follows load in normal + all fault episodes; `base.yaml load_coef_degc_per_tph` (unchanged) is the L1 analogue |
| bed lag α (per 5 s) | `0.010` (τ ≈ 500 s) | `DT/TAU_BED_S = 0.014` (τ ≈ 356 s) | 1.4× faster | bed transient shape |
| ms lag α (per 5 s) | `0.020` (τ ≈ 250 s) | `DT/TAU_MS_S = 0.093` (τ ≈ 54 s) | 4.6× faster | ms transient shape; the E12 dip/recovery |
| ms load term | `−0.6·(steam − 67)` (unsourced) | `−K_MS_FLOW·(steam − 67)`, `K_MS_FLOW = 0.45` ASSUMED | 0.75× | ms vs load (see disagreement 3) |
| firing lag α (per 5 s) | `0.05` (τ ≈ 100 s) | `DT/60 = 0.083` (τ ≈ 60 s) | 1.7× faster | fuel-side transient (unsourced both ways; documented) |
| coal GCV concept | 3400 kcal/kg ASSUMED directly | 4040 kcal/kg DERIVED from one ultimate analysis (Dulong) | 1.19× | `m_coal_nom`; **`notes_gen.base_cv` still 3400** — see disagreement 2 |

## Disagreements recorded, not resolved
1. **`e_p` derived (679 J/Pa) vs correction 1's physical band (0.8–1.2e3 J/Pa).**
   The derivation is water-sensible-dominated: `M_bw·cp_bw·dTsat/dp = 550` of the
   679, with `M_bw` from geometry and `cp_bw`/`dTsat/dp` from IF97; the metal
   term (~90) carries the only free input (`M_drum_metal`). 679 is **~1.2×**
   below the correction's low end — inside 2×, **no stop-rule**, and it is
   **not** a fit-vs-physics gap (both figures are physics). The gap that matters
   is against the value an ar1 fit to `drum_pressure.timescale_minutes = 44.53`
   would demand, `~1.3e4 J/Pa` — **19×** the derived value. Per CLAUDE.md that
   19× *is* the diagnosis: the 44.53-min timescale is inherited input slowness,
   not the plant's pressure response. **Chosen:** the derived 679 J/Pa.
   Achieved `drum_pressure` autocorrelation is reported after **sub-model 4**,
   once the OU load driver supplies the slow wander.
2. **Coal GCV: 4040 (Dulong, this stage) vs 3400 (`notes_gen.base_cv` and the
   `data/kb` coal reports, unchanged).** The correction is explicit — an
   analysis giving `air_st ≈ 5.46` implies `GCV ≈ 4000–4100`, so the ASSUMED
   3400 is inconsistent with the ASSUMED stoichiometric air. `sim.py` now uses
   4040 (DERIVED, one analysis, Dulong self-test). `notes_gen.base_cv` and the
   on-disk `records.json` coal reports still say 3400 (the feeder *calibration
   basis*). **Chosen:** move `sim.py` now; `notes_gen.base_cv` and the coal
   reports should be brought to ~4040 at the **next episode regeneration**
   (out of scope this stage — episodes not regenerated, as in sub-model 1).
   Until then `coal_cv_factor = 1.0` in the simulator corresponds to 4040 kcal/kg
   physical while the report text says 3400 — a documented, temporary skew.
3. **E12 — ms vs load: RCA Case 4 vs the fingerprint.** RCA Case 4 records ms
   **falling** on a sharp load rise (492 → 428 °C in ~8 min; mechanism: firing
   lag + swell carry-over + a *passing attemperator spray valve*). The
   fingerprint's steady-state `ms_temperature` level-regression on load gives
   **+0.31 °C/(t/h) at R² 0.028** (`delta_1h` +0.56 at R² 0.065) — i.e. **not a
   relationship**. **Modelled:** the Case 4 transient direction —
   `K_MS_FLOW = 0.45 °C/(t/h) > 0`, so more steam mass through a fixed
   superheater surface gives a transient ms dip that then recovers as the bed
   catches up (direction check 8: −2.2 °C dip → +4.6 °C recovery). The
   fingerprint's +0.31 slope is **not** imposed, because (a) R² 0.03 is not a
   fit, (b) it is a different boiler (774 °C bed, 99.6 kg/cm2), (c) our
   attemperator/spray is not modelled, so steady-state ms control is out of
   scope. Our modelled steady-state |ms~load slope| stays within the
   fingerprint's own scatter. Case 4's full −64 °C is not reproduced (the
   passing spray valve, the dominant term there, is not a driver in this sim).
4. **E8 — `primary_air` valid range.** The model raises bed temperature
   *monotonically* as PA falls (`K_PA·(1 − min(1, PA))`), with **no
   air-limited turnover**. Below
   `PRIMARY_AIR_MIN = 1 − (LAMBDA_EXCESS − 1)/(PA_AIR_FRACTION·LAMBDA_EXCESS)
   = 0.636`, primary + secondary air can no longer meet stoichiometric demand,
   and a real bed would go sub-stoichiometric (CO up, bed temp rolls over) —
   which this model does not represent. **The `primary_air` driver is only
   physical on [0.64, ~1.15].** Family D uses 0.80 and 0.84, both inside it;
   direction check 7 asserts the bound so a future schedule cannot cross it
   silently. Recorded, not "fixed" — adding an air-limited rollover would be a
   new ASSUMED coefficient with no reference.
5. **Family E02 (`heat_absorption = 0.989`) drift rate may be below the L1
   long-drift threshold.** E02 bed rises ~+12 °C over its ~180-min ramp
   ≈ 0.067 °C/min, against `configs/base.yaml rates_long.bed_temp_avg.threshold
   = 0.08 °C/min` (sustained 40 min). E01 (0.986, +20 °C) and E03 (0.985,
   +15 °C) clear it. The threshold is flagged in `base.yaml` as "expect to tune
   in phase 2" and is **not** touched here. If E02 must trigger the drift check,
   either the threshold drops in the config-retune stage or E02's severity is
   raised in a scoped stage — **not decided here**.
6. **Family C severe (`fuel_availability = 0.62`, feeder trip) bed → ~772 °C
   (−78 °C).** This is dominated by the load-following term (`steam` collapses
   to ~41 t/h → `K_BED_LOAD·(41 − 67) ≈ −72`). A −78 °C average-bed drop for a
   half-feeder trip is on the strong side; RCA does not quantify it. Direction
   is correct ("pressure and bed fall together"), magnitude is plausible but
   unverified. Recorded.
7. **Deep pressure dip on a *fast* load step.** A 67 → 77 t/h step ramped over
   **2 min** (the direction-check stimulus) drops pressure ~8 kg/cm2 before the
   firing controller (τ ≈ 60 s, proportional p-trim, no feedforward) recovers
   it. Real units with load feedforward would dip less. Gentle episode ramps
   (N03/N05 use 10 min) produce a negligible dip (< 0.5 kg/cm2). This is a
   magnitude to watch, not a direction failure; bounded by the `throttle`
   coupling (steam cannot be pulled at rated flow on low pressure).

## Blocked / needs a decision
No new stop-rule hits. Decisions deferred to later stages:
- **(a)** `notes_gen.base_cv` / `data/kb` coal-report GCV: 3400 → ~4040 at the
  next episode regeneration (disagreement 2).
- **(b)** L1 thresholds tuned against the old energy physics —
  `checks.rates.drum_pressure`, `checks.balance.p_falling` / `bed_rising`,
  `checks.rates_long.bed_temp_avg` (disagreement 5) — belong to the scoped
  config-retune stage, not this one.
- Open sub-model-1 advisor questions (drum GA / level-transmitter span; CBD
  conductivity analyser) unchanged.

## What I could not verify
- **`E_P`** rests on `M_drum_metal` (ASSUMED 18000 kg) and `cp_bw` from IF97 at
  the saturation point; the derived 679 J/Pa could not be checked against a
  measured pressure time constant for this boiler. Correction 1's independent
  physical estimate (0.8–1.2e3) is the closest available cross-check and agrees
  to ~1.2×.
- **The bed conductance split `G_FG` + `G_WW`** (`BETA_FURNACE = 0.45`) is an
  engineering lump: the in-bed water-wall heat-transfer area and coefficient are
  not known for this boiler. It was introduced because `G_FG` alone made the bed
  ~2.5× too sensitive (a 3.5 t/h leak quenched the average bed 45 °C; family E
  crossed 880). The *ratio* `G_WW/G_FG ≈ 1.55` is plausible for an AFBC bed but
  unverified.
- **`K_MS_FLOW`, `K_MS_BED`, `K_BED_LOAD`** magnitudes are ASSUMED with ranges;
  the fingerprint gives a bed~load slope (3.9, different boiler) but no
  discriminating value for the superheater terms. Confirming would need plant
  transient data.
- **Family-C and family-E bed magnitudes** (disagreements 5, 6) — the RCA cases
  describe direction and rough shape, not numbers. Verifying the −78 °C
  feeder-trip drop or the +12–20 °C fouling rise needs plant data.
- **The `_fire` / firing-controller lag (60 s)** and the pressure-trim gain
  (0.06 per kg/cm2) are controller tunings, unsourced both before and after;
  changed only to keep the faster physical `E_P` stable. Documented, not
  verified.

---

## Corrections (post-review, 2026-09-01)

### C1 — `TAU_BED_S` used the wrong conductance (physics bug, fixed)
`TAU_BED_S = M_BED_KG·C_BED / G_FG_NOM` gave 356 s, but every bed deviation gain
(`K_COMB` / `K_FUEL` / `K_CV` and the leak quench) is `Q / G_BED_EFF`. A
first-order lag's `τ = C/G` must use the **same** `G` that sets its steady-state
gain `ΔT = ΔQ/G`. Corrected to `TAU_BED_S = M_BED_KG·C_BED / G_BED_EFF` =
**139 s** — the bed lag was **2.6× too slow for its own sensitivity**.

- **Self-test 6a added**: asserts `M_BED_KG·C_BED/TAU_BED_S`,
  `Q_FUEL_NOM/K_COMB` and `G_BED_EFF` are one number (rel-tol 1e-9).
- **Mutation M10 added**: `TAU_BED_S` back to `/ G_FG_NOM` → self-test 6a fails
  (`denominator 30917 != G_BED_EFF 78903`). Caught.
- **Direction check 8 (E12) reworked**: with the faster bed the ms dip is
  briefer (bed catches up in ~5 min, not ~8), and the ~2 °C dip is now
  comparable to the OU load wander. Changed to a **paired** step-minus-no-step
  difference on 4 seeds, measured 30–150 s after the step: **dip −1.83 °C →
  recover +4.29 °C**. Still the RCA Case 4 direction. M6 (flip the ms-flow sign)
  still caught (+9.85 °C, no dip).
- All 8 direction checks, 10 self-test groups, 10 mutations pass. Steady-state
  magnitudes are **unchanged** (the gain `Q/G_BED_EFF` did not move); only the
  response speed.

**Effect on the fault families** (τ 356 s → 139 s, 4-seed means):

| metric | old (τ 356 s) | new (τ 139 s) | change |
|---|---|---|---|
| family C (`fuel_availability` 0.80) bed sag rate, first 20 min after onset | 2.00 °C/min | **2.48 °C/min** | +24 % (reaches the cooler quasi-steady state sooner) |
| family D (`coal_cv_factor` 1.16) time from onset to bed > 940 °C | ~14.1 min | **~10.6 min** | **−25 % (~3.5 min sooner)** — a detection-lead-time (T6) input |
| family E (`heat_absorption` 0.985) bed rise at +40 / +80 / +120 / +180 min | +6.0 / +9.5 / +11.9 / +12.3 °C | +6.7 / +10.4 / +11.9 / +11.7 °C | negligible at long horizons — the fouling ramp, not τ, sets the shape; still crosses nothing (max < 880) |

### C2 — `BETA_FURNACE` relabelled FITTED-TO-EXPECTATION
The reviewer is right: the `G_WW` *structure* is physics, but `BETA_FURNACE =
0.45` was **chosen to produce acceptable output**, not read from practice. Its
provenance in `sim.py` and DERIVATIONS §4 is now **FITTED-TO-EXPECTATION**,
with the two constraints stated explicitly:

1. a 3.5 t/h tube leak quenches the average bed **~15–25 °C** (RCA Case 11
   scale), and
2. family-E fouling (0.985) stays **under the 880 °C bed alarm** across seeds.

**Range where both hold: `BETA_FURNACE ∈ [0.30, 0.65]`**, nominal 0.45. Below
0.30 family E reaches the 880 alarm; above ~0.65 the leak sag drops under ~14 °C
and family D (cv 1.16) only reaches ~955 °C. Full sweep table in DERIVATIONS
§4.3. The nominal 0.45 also sits inside the physical furnace water-wall
heat-split band (~40–55 %) — a sanity check, not the source.

### What did not change
`E_P` stays the physical 679 J/Pa (correction 1 of the original review stands).
`K_PA` keeps the `G_FG` normalisation. The GCV/air_st consistency test, the
M3/M4 non-double-count proof, and the refusal to force bed/ms sd are unchanged.
