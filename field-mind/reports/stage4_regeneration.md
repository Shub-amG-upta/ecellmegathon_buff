# Stage 4 / regeneration + inherited retunes — report

## Status
DONE — all 30 episodes regenerated and passing the validation gate. Driver
severities re-anchored to the RCA case timelines; one shared load OU
supplemented with independent per-subsystem process noise (steam↔bed 0.98 →
0.88); `notes_gen.base_cv`, the L1 thresholds, the DEVIATION band and the load
coefficient all brought onto the new physics.

## Verification

| step | result |
|---|---|
| ran the generator | `python -m data.generator.episode_build --out data/episodes` → **30/30 written**, 0 rejected. `by family: A6 B5 C6 D4 E3 N6`. |
| gate on the output | `python -m bench.validate_data --episodes data/episodes` → **30/30 PASS** (1 benign WARN, `ep_E03`). |
| sim self-tests | `python3 -m data.generator.sim` → exit 0. 10 self-test groups pass; 8 direction checks OK; `_ou_recheck` OK; **11 mutations** caught (M11 new: zero the process-OU innovation → steam↔bed → +0.97 > 0.95, caught). |
| L1 unit tests | `python bench/test_checks.py` → **18 / 18**. |
| independent re-derivation (xcorr) | `sim._pearson` steam↔bed **+0.871**; OLS `sqrt(R²)` of `bed ~ steam` **+0.872** (0.1 %). R² = 0.760. |
| independent re-derivation (time-to-trip) | closed form `t_trip = 40 %span / (K_level(66)·deficit)` from the **measured** effective deficit: `ep_A01` 35.7 min vs 29.2 measured (22 %); `ep_A02` 15.6 vs 13.1 (19 %); `ep_A06` 35.5 vs 30.2 (17 %). Closed form over-predicts because it assumes a constant deficit while the level controller claws feed back early — direction correct, all < 2×. |
| mutation check | `G_SW` sign flipped → `ep_C01` → validate_data **C1 FAIL**. Restored. |

## Effect 1 — driver severities re-anchored to the RCA timelines

**Framing (per the review):** this is *re-anchoring severity to real timescales*,
not undoing a speed-up. The physics from stage 3 is unchanged. Where a case
gives a timeline the severity is solved against it; where it does not (family E,
the 8 h → ≤3 h compression of family B) the severity is recorded as a benchmark
design choice.

RCA anchors (`docs/Boiler_Failure_Case_Studies_RCA.pdf`, read with pypdf):

| family | case | RCA onset→ALARM | RCA onset→trip |
|---|---|---|---|
| A | 1 | 20 % Lo @ ~25 min | manual low-low trip @ ~35 min ("declined monotonically for 30 min") |
| B | 11 | — (controlled trip @ ~8 h) | feed 2–3 t/h over steam @ 4.5 h; conductivity falling |
| C | 6 | 66→63 kg/cm² @ 35 min; 66→58 @ 65 min | burner support @ 58, **no hard trip** |
| D | 7 | 880 °C bed @ ~90 min | 940 °C DCF trip @ ~125 min |
| E | — | never | never (no RCA timeline — design choice) |

### Before / after (min from onset; "—" = state not reached in the episode)

| episode | old→trip | new→DEVIATION | new→ALARM | new→trip | driver change |
|---|---|---|---|---|---|
| A01 fcv_seize | 15.2 | 7.2 | 19.2 | **29.2** | eff 0.77 → 0.81 |
| A02 fcv_seize_fast | 6.3 | 2.4 | 9.8 | **13.1** | eff 0.62 → 0.75 |
| A03 bfp_suction | 5.9 | 3.8 | 15.9 | **20.0** | eff 0.55 → 0.75 |
| A04 strainer_choke | 25.5 | 22.0 | 35.8 | **59.8** | eff 0.79 → 0.83 (120-min ep) |
| A05 fcv_caught | trip (mis-caught) | 8.3 | — | **— (recovers)** | eff 0.74 → 0.82, recovery onset+1100 s |
| A06 fcv_seize_repeat | 14.7 | 10.6 | 24.0 | **30.2** | eff 0.75 → 0.82 |
| B01 tube_leak | none | 9.6 | — | none | leak 3.5 → 2.2 t/h (gap ≈ +3.1) |
| B02 tube_leak_fast | none | 2.2 | — | none | leak 6.0 → 3.5 |
| B03 tube_leak_slow | none | 27.1 | — | none | leak 2.2 → 1.8 (150-min ep) |
| B04 cbd_left_open | none | 1.8 | — | none | leak 4.0 → 2.5 |
| B05 tube_leak_repeat | none | 9.9 | — | none | leak 3.5 → 2.2 |
| C01 wet_coal | none (p→48) | 10.6 | — | none (p→57.5) | fuel_avail 0.80 → 0.955 |
| C02 feeder_trip | none (p→37) | 1.6 | 4.9 | none (p→52) | 0.62 → 0.89 |
| C03 wet_coal_mild | none (p→52) | 22.1 | — | none (p→56) | 0.88 → 0.96 |
| C04 feeder_trip_caught | none | 1.0 | 2.4 | — (recovers) | 0.65 → 0.89 |
| C05 low_cv_coal | none (p→50) | 15.6 | — | none (p→56) | 0.84 → 0.95 |
| C06 wet_coal_repeat | none (p→47) | 10.8 | — | none (p→58) | 0.80 → 0.96 |
| D01 high_cv_coal | 12.1 | 26.6 | 48.8 | **120.3** | cv 1.16/ramp 12 min → 1.14/ramp 120 min (160-min ep) |
| D02 low_primary_air | none (bed 931) | 33.4 | 41.1 | none (bed ~915) | PA 0.80/ramp 8 → 0.86/ramp 70 (130-min ep) |
| D03 high_cv_severe | 5.2 | 11.3 | 15.6 | **39.8** | cv 1.24/ramp 6 → 1.16/ramp 55 (80-min ep) |
| D04 low_pa_caught | none | 16.8 | — | — (recovers) | PA 0.84/ramp 8 → 0.86/ramp 40 |
| E01–E03 | none | — | — | none | absorb 0.985–0.989 → 0.984–0.986; seeds 600/603/604 |

Episode durations were extended where the RCA trajectory did not fit
(A01 55→70, A03 50→60, B/C several, D01 70→160, D02 60→130, D03 50→80, D04 65→85,
E01 240→200, E02 200→180, E03 180→160). All 30 tier labels unchanged.

**Family A is a knife-edge, and per-seed severity is a deliberate benchmark
design choice — not a fitted quantity.** Near `eff·81.6 ≈ steam demand` the
fault is a marginal deficit, and the sub-model-4 slow load OU (σ 2.1 t/h) sets a
different baseline load per seed, so the same effectiveness gives a different
outcome per episode. Measured `d(time-to-trip)/d(effectiveness)` at the chosen
operating points:

| episode (seed) | eff | trip @ eff−0.01 / eff / eff+0.01 (min) | sensitivity |
|---|---|---|---|
| A02 fast (201) | 0.75 | 11.7 / 13.1 / 15.0 | ≈ **1.7 min per +1 pp** |
| A06 (205) | 0.82 | 24.6 / 30.2 / 40.4 | ≈ **8 min per +1 pp** |
| A01 (200) | 0.81 | 21.2 / 29.2 / *never* | steepens to ∞ at the upper edge |

So each variant's `feed_valve_effectiveness` was **chosen per scenario** to land
its time-to-trip in the RCA band (20 % @ ~25 min, trip @ ~30–40 min for the
canonical cases; faster for the "fast" variant; alarm-only for "caught"), the
same way the `duration_min`, `tier` and modality set are chosen per scenario.
It is not a regression fit and carries no R²; the number in `episode_build.py`
is a design parameter of that episode. The underlying load sensitivity is
physical — a marginal feed restriction is more or less survivable with the load
at the time — and is kept rather than suppressed.

**Family D is carried by a long ramp.** The corrected bed lag (τ 139 s) reaches
its target in minutes, so RCA Case 7's 90-min / 125-min approach is produced by
a 55–120 min driver ramp (the fuel changeover / ash-recirc depletion the case
describes), not a fast step. `D01` trips at **120.3 min** (RCA 125).

## Effect 2 — independent per-subsystem process noise

One shared `load_demand` OU coupled every tag through a single input →
steam↔bed `pearson_level` **+0.98** vs the fingerprint's **+0.87** (bed 96 %
explained by steam vs 76 % in the real trace).

Added: three **independent** single-timescale OU processes on the *effective*
driver values inside `step()` — `heat_absorption`, `coal_cv_factor`,
`primary_air` — never mutating `self.d.*`, so the fault schedule and emitted
ground truth stay exact. They enter the same energy balance as the scheduled
drivers, so **both balances still close** (verified: `sim` self-tests 5, 6a;
`_leak_run` paired mutations M3/M4; validate_data C1/C2 on all 30). The
`primary_air` disturbance is applied as a signed `− K_PA·ou_pa` term so the
`min(1, ·)` rectifier does not bias the bed upward in normal operation.

| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `TAU_PROC_S` | 25 min | ASSUMED — combustion / air-side disturbance timescale | 15–45 min | how much of the OU the τ 139 s bed lag passes |
| `SIGMA_PROC_HA` | 0.0022 | ASSUMED, calibrated to steam↔bed 0.87 | 0.001–0.004 | bed + pressure |
| `SIGMA_PROC_CV` | 0.0022 | ASSUMED, calibrated | 0.001–0.004 | released heat → bed + pressure |
| `SIGMA_PROC_PA` | 0.0035 | ASSUMED, calibrated | 0.0015–0.006 | bed target (zero-mean K_PA term) |

**Achieved** (`sim._ou_recheck` / `validate_data --suite`, 3 × 30 h no-fault):

| quantity | one shared OU | + process noise | fingerprint |
|---|---|---|---|
| steam↔bed `pearson_level` | +0.97 | **+0.88** | +0.869 |
| bed R² on steam | 0.94 | **0.76** | 0.755 |
| steam_flow sd | 2.34 | 2.06 | 2.38 |
| bed 30-min autocorrelation | 0.62 | 0.57 | 0.571 |

Stated as **achieved**, not tuned-to-hit: the σ triple was set once against the
0.87 target and the result reported. `_ou_recheck` now asserts steam↔bed ∈
[0.60, 0.95] (both bounds); mutation M11 (kill the process OU) → +0.97, caught.

## Inherited items

### notes_gen.base_cv 3400 → 4040
`data/generator/notes_gen.py`: `base_cv` literal and the two "3400" note strings
→ **4040** (`sim.GCV_KCAL_PER_KG`, Dulong on the one ASSUMED ultimate analysis,
DERIVATIONS §4.1). `records.json coal_lab_report.gcv_kcal_kg` now `int(4040·cv)`.

### DEVIATION band load-normalised + a pressure term
`episode_build.state_timeline`, DEVIATION tier:
- `|bed − 850| > 25` → `|bed − (850 + LOAD_COEF·(steam − 67))| > 25`. Same
  normalisation L1's `_load_normalised_bed_slope` applies — a raw band
  manufactures DEVIATION labels the detector is built never to raise, and a bed
  at 870 °C at high load is operating normally. `LOAD_COEF` is read from the
  config at generation time and **stamped into every `ground_truth.json` as
  `state_timeline_load_coef`** so a future re-fit cannot silently relabel data.
- **new:** `drum_pressure < 63`. RCA Case 6's combustion-deficit deviation
  point (66→63 at T+35 min); there was no pressure term before, so an
  RCA-faithful controlled sag (family C now sags to ~57 with no hard trip)
  registered as all-NORMAL and family C was unscoreable.
- `gap > 4` → `gap > 2.5`. Family B (RCA Case 11) is "feed 2–3 t/h over steam
  yet level holds"; the old > 4 band never saw it once leaks were sized to the
  RCA.
- 60 s debounce added so noise flicker across these tighter thresholds does not
  shatter the timeline.

ALARM/TRIP thresholds (bed 880/940, level 20/10, pressure 55) are **unchanged**
— real RCA setpoints.

### load coefficient re-fit (DERIVATIONS §9) — **2.75 → 2.43**

`configs/base.yaml checks.load_coef_degc_per_tph` was **2.75**; it is now
**2.43** — the **level regression** `bed ~ 850 + coef·(steam − 67)` over the
regenerated N01–N06 (**R² 0.82**), the form the DEVIATION band structurally
uses. `state_timeline_load_coef` in every `ground_truth.json` is 2.43 and the
episodes were regenerated.

**Why not the slope regression, why not 2.75:**

| quantity | value |
|---|---|
| OLS `slope(bed) ~ slope(steam)`, 5-min windows | 1.43 (R² 0.34) |
| … 10-min | 1.97 (R² 0.50) |
| … 20-min | 2.41 (R² 0.57) |
| … 40-min | 2.88 (R² 0.66) |
| … 55-min | 3.16 (R² 0.72) |
| **level regression `bed ~ steam`** | **2.43 (R² 0.82)** |
| simulator generator gain `K_BED_LOAD` | 2.75 |

The slope regression is **window-length dependent**, not
errors-in-variables-attenuated by measurement noise. Pure emit-noise OLS-slope
SE over a 40-min window is **2.3 × 10⁻⁴ °C/min** and the implied attenuation
factor `λ = var(true) / (var(true) + var(err))` is **0.99995** (0.998 even at
5 min) — measurement noise changes the fitted slope by ~0.01 %, not the 20 %
that a 2.21→2.75 correction would need. The window dependence is the **τ 139 s
bed lag**: over a short window the contemporaneous `slope(steam)` is a poor
proxy for the slightly-lagged load the bed actually tracks, diluting the
regression; over a long window the lag is a small fraction and the slope rises
past the generator gain.

There is therefore **no single correct coefficient** for the slope-based checks
(they use 5-min and 55-min windows). The **level** relationship the DEVIATION
band needs is well defined (2.43, R² 0.82), so that is the config value; the
slope-check thresholds below are set from the **measured** no-fault distribution
*at* coef 2.43, so their FP rate is calibrated whatever the residual
normalisation error. `K_BED_LOAD = 2.75` (the sim's instantaneous target gain)
is **decoupled** — it is a different quantity and its comment no longer claims a
config anchor. **Picking 2.75 because it matched the generator was circular and
is withdrawn.**

### L1 thresholds re-tuned against the new noise
Measured over **12 fresh-seeded no-fault runs (seeds 900–911, disjoint from
N01–N06** so Q5 stays uncircular). Thresholds set from the p99.7 of each
statistic; the aggregate rate was **measured**, not assumed independent.

| key | old | new | no-fault p99.7 of the statistic |
|---|---|---|---|
| `checks.load_coef_degc_per_tph` | 2.75 | **2.43** | level regression `bed ~ steam`, R² 0.82 (see above) |
| `checks.balance.level_gain_pct_per_min_per_tph` | 0.22 | **0.586** | — (matches `geometry.K_level(66)`; was an invented literal) |
| `checks.rates.bed_temp_avg.threshold` | 0.4 | **1.6** | 1.28 °C/min (10-min slope, **not** load-normalised → OU load swings it) |
| `checks.rates.drum_pressure.threshold` | −0.05 | **−0.15** | 0.12 kg/cm²/min |
| `checks.rates_long.bed_temp_avg` | 0.08 / 40 min | **0.28 / 55 min** | 0.285 (55-min load-normalised, at coef 2.43) — see finding below |
| `checks.rates_long.ms_temperature.threshold` | 0.05 | **0.20** | 0.20 |
| `checks.balance.residual_tph` | 2.0 | **1.5** | 0.13 t/h |
| `checks.balance.p_falling` | 0.05 | **0.15** | 0.12 |
| `checks.balance.bed_rising` | 0.3 | **1.3** | 1.21 (5-min load-normalised, at coef 2.43) |
| `l1_checks._patterns` bed-slope literals (code, not config) | 0.4 / 0.2 / 0.1 | **`bed_rising` / 0.5 / 0.3** | the 0.4 literal fired the "energy accumulating" pattern ~15×/h on a healthy plant |

**Achieved false-positive rate — analytic, from the 12 fresh no-fault runs:**

| check | FP ticks/hour |
|---|---|
| RATE:bed_temp_avg (`rates` 1.6 + `rates_long` 0.28) | 0.43 |
| RATE:drum_pressure | 0.24 |
| BALANCE:drum_pressure | 0.24 |
| BALANCE:bed_temp_avg | 0.14 |
| **aggregate** | **0.81 / hour** (target ≤ 2) |

Per-family L1 first-flag after onset: **A 2.5–18 min, B 3.5–27.5 min** (BALANCE
water-residual), **C 0.5–20.5 min, D 5.5–11 min**; **E01/E02/E03 never**
(finding 1). **This is NOT a harness Q5 result** — it is the analytic trigger
rate of L1 over no-fault windows. A closed-loop Q5 on held-out episodes is a
scoped follow-up (deliberately not iterated against these same episodes).

## Disagreements recorded, not resolved

1. **Family E's fouling drift is masked by the plant's own low-frequency
   wander.** (stop-rule-2 hit — recorded, not resolved by tuning.) The no-fault
   load-normalised bed slope over 55 min has p99.7 ≈ **0.285 °C/min**, against a
   pure emit-noise OLS-slope SE of **1.4 × 10⁻⁴ °C/min** — a factor of **~1,700**.
   The no-fault spread is therefore ~entirely **real bed movement** from the OU
   load + per-subsystem process disturbances (correlated, low-frequency), not
   measurement noise. Family E's own 55-min slope is **0.19–0.24 °C/min**,
   *below* the no-fault p99.7; `rates_long.bed` (threshold 0.28, set at that
   p99.7) fires on **none of E01/E02/E03**.
   - **This is not a "wrong instrument" problem.** For a linear ramp in noise the
     OLS slope *is* the matched filter (minimum-variance unbiased); a longer
     window shrinks the *measurement* SE (∝ N^−1.5) but not the correlated-drift
     term, which already dominates by 1,700× — confirmed empirically (40 → 55 min
     moved the no-fault p99.7 only 0.35 → 0.285).
   - **A cumulative-offset detector would help, for the right reason:** its
     sensitivity to a small persistent bias grows with elapsed time (it
     integrates the offset), where a fixed-window slope's SNR against correlated
     drift does not improve once the window exceeds the drift correlation time.
     Family E's *offset* from the load-normalised baseline reaches +12–20 °C over
     its full duration, well outside the no-fault envelope. Building it is an
     L1-stage change, out of scope here.
   - **Options for the advisor:** (a) accept that family E is not slope-detectable
     and add the cumulative-offset detector in the agent stage; (b) strengthen
     family E — but see finding 2, every stronger seed/severity pushed bed_max
     toward the 880 alarm. Chosen: (a). `rates_long.bed` threshold left at the
     honest no-fault p99.7 (0.28), not pushed into the distribution to claim a
     marginal detection. Aggregate FP 0.81/h.

2. **The family-E severity squeeze is *not* a two-plant variability artifact —
   checked.** Family E was softened (0.985 → 0.984–0.986) to keep bed_max under
   the 880 alarm. The 880 alarm / 850 nominal (30 °C headroom) is from the plan
   PDF (our 67 t/h boiler); the OU variability was fitted to the xinan boiler at
   a 774 °C bed. Measured on the 12 fresh no-fault runs:

   | | ours (synthetic) | xinan fingerprint |
   |---|---|---|
   | bed sd | 7.10 °C | 10.68 °C (**0.66×**) |
   | bed 1 h-block ptp, mean / p95 / max | 12.6 / 28.2 / 31.4 °C | 24.6 / 38.3 / 45.1 °C |
   | no-fault bed max | 865.5 °C (14.5 °C below the alarm) | — |
   | no-fault ticks within 5 °C of the 880 alarm | **0 / 15 840 (0.00 %)** | — |

   Our bed carries **~⅔ of the fingerprint's absolute variability** and the
   no-fault trace never approaches 880 — so the imported variability *is*
   consistent with the 30 °C band and the squeeze is physics (drift masking),
   not a mismatch. **But it is a near thing:** the no-fault 1 h-ptp p95 is
   28 °C against a 30 °C band, i.e. the load wander alone occasionally fills 93 %
   of the headroom. If the full fingerprint variability (≈ 1.5× more, per the
   `stage3_submodel4.md` sd gap) were reproduced, the no-fault trace *would*
   reach 880 and the band would be untenable — the 0.66× under-reproduction is
   load-bearing, not just a recorded gap.

3. **`load_coef` — the fit is not the generator gain, and that is now the
   config value.** OLS `slope(bed) ~ slope(steam)` is window-length dependent
   (1.4 at 5 min → 3.2 at 55 min) from the τ 139 s bed lag; the errors-in-
   variables factor from measurement noise is **0.9999**, so it explains ~none
   of the spread. The **level** regression (the DEVIATION-band form) is
   **2.43, R² 0.82** and is the value used — replacing the earlier 2.75, which
   had been chosen because it matched `K_BED_LOAD` (circular). Episodes
   regenerated; `state_timeline_load_coef` = 2.43.

5. **Family C bed cooling is now small (~8–12 °C vs the old 35 °C).** The
   pressure-integrator quasi-steady sag is ≈ `60·fuel_availability`, so an
   RCA-faithful sag to ~57 kg/cm² needs `fuel_availability ≈ 0.955`, which only
   removes ~5 % of heat and cools the *average* bed ~8–12 °C. RCA Case 6's
   bed-temperature fall is **compartment-selective**; the six-compartment
   average (our tag) would fall less. The discriminating L1 signal for family C
   is the pressure sag at flat steam (BALANCE "heat input short"), not the bed
   drop — verified to fire on all six C episodes. Recorded; the old 35 °C was
   flagged "on the strong side" in `stage3_submodel2.md` disagreement 6.

6. **`steam↔pressure` / `bed↔pressure` cross-correlation sign** is still
   negative (−0.21 / −0.12) against the fingerprint's +0.35 / +0.39. Unchanged
   from `stage3_submodel4.md` disagreement 2 — the real plant's drum-pressure
   setpoint co-drifts with load over hours; ours is fixed at 66. A slow OU on
   the pressure setpoint is the candidate fix, still deferred. The process noise
   added here does not touch this.

7. **`drum_pressure` sd 0.12 vs fingerprint 0.77** — likewise unchanged and same
   cause (fixed setpoint). Recorded in `stage3_submodel4.md`.

## Blocked / needs a decision
- **Family-E detectability (disagreement 1).** The question for the advisor:
  accept it and add an L1 cumulative-offset drift detector in the agent stage,
  or revisit family E. Not a data-generation fix.
- **The 0.66× bed-variability under-reproduction is load-bearing (disagreement
  2).** If a later stage closes the `stage3_submodel4.md` bed-sd gap, the 30 °C
  DEVIATION/alarm band and family E both have to be revisited — flagged so it is
  not closed silently.
- **A closed-loop harness Q5** on held-out normal episodes (fresh seeds again,
  or more generated normals) to confirm the analytic 0.81/h — deliberately not
  done here to keep the retune uncircular.

## What I could not verify
- **Family A per-seed severities** reproduce the RCA *rate* (20 % @ ~25 min,
  trip @ ~30–40 min) but the RCA *deficit* (4–5 t/h) is not matched — the
  measured effective deficit is ~1.9 t/h for the canonical cases. This is the
  pre-existing §3.7 K-over-determination, carried, not introduced here.
- **`TAU_PROC_S = 25 min` and the σ triple** are ASSUMED — the fingerprint has
  no per-subsystem disturbance spectrum to fit them to. They are pinned only by
  the single steam↔bed target; the (τ, σ) pair is a ridge, not a point (τ 20 or
  35 min gave the same +0.87 with a rescaled σ).
- **Family E's "crosses nothing" guarantee** holds for the three chosen seeds
  — bed_max **876.8 / 874.6 / 871.2** (E01 / E02 / E03) against the 880 alarm —
  but is **seed-sensitive and E01's 3.2 °C margin is thin**: seeds 602 / 608 /
  611 at the same severity reach bed_max 880–884, which is why the seeds were
  fixed. The generated episodes are safe (gate C6 passes); the guarantee is not
  robust to an arbitrary seed, and it is bound up with the 0.66× bed-variability
  under-reproduction (disagreement 2).
