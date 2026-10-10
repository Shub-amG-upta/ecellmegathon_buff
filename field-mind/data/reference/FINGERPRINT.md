# Reference-data fingerprint

`fingerprint.json` is a statistical portrait of the real boiler trace in
`xinan_completed_data.csv` — 86,400 rows, 5-second sampling, 120 hours of
continuous operation from an AFBC-class unit at ~60 t/h, 9.77 MPa, 537 °C main
steam, 774 °C bed. It is the calibration target for `data/generator/sim.py`.

Regenerate with `python3 data/reference/build_fingerprint.py` (run from the
repo root; needs numpy + pandas, host only). Every number is **measured** from
the real trace — none is derived, cited or assumed. This file says what each
one means and which simulator knob it pins.

## What was measured, and against which column

Column mapping is the verified one from `CLAUDE.md`. Only four of our six tags
have a real counterpart:

| our tag | their column | unit used here |
|---|---|---|
| `steam_flow` | `ZZQBCHLL.AV_0#` | t/h |
| `bed_temp_avg` | `TE_8313B.AV_0#` | °C |
| `ms_temperature` | `TE_8332A.AV_0#` | °C |
| `drum_pressure` | `PTCA_8322A.AV_0#` | kg/cm²(g) — raw column is MPa, multiplied by 10.19716 |

`drum_level` and `feed_water_flow` have **no reference column**. Their
simulator noise/drift parameters (`sim.py` `noise["drum_level"]`,
`noise["feed_water_flow"]`, `K_LEVEL`) stay uncalibrated by this fingerprint —
flag that in any writeup rather than borrowing another tag's numbers.

The pressure conversion assumes the tag reads gauge pressure. If it is
absolute, subtract ~1.03 kg/cm² from the reported `mean`; every noise, drift,
autocorrelation and correlation number is unaffected because they are all
differences. `native_pressure_mpa` keeps the raw-MPa stats so nothing is lost.

## Operating-point caveat — absolute levels do not transfer

The reference unit is **not our boiler**. It runs lower load (~60 vs 67 t/h),
much higher pressure (9.77 MPa ≈ 99.6 kg/cm²(g) vs the sim's 66), a cooler bed
(774 vs 850 °C) and hotter main steam (537 vs 495 °C). Use `level.mean`,
`min`, `max`, `p05`, `p95` only as a **shape** reference (coefficient of
variation, percentile spread). Do not move `NOMINAL` in `sim.py` toward these
means. What *does* transfer is everything scale-free: noise magnitudes relative
to signal, autocorrelation, drift-per-hour, cross-tag correlation, and the
load-following slopes.

## `sampling`

Confirms the record: `dt_seconds` is exactly 5.0 (min = median = max),
`n_gaps_gt_1p5x` = 0, `n_duplicate_timestamps` = 0, 86,400 rows spanning
120.0 h. No resampling or gap-filling was needed. `sample_period_s: 5.0` in
`configs/base.yaml` matches.

## Per-tag numbers (`tags.<tag>`)

### `level` — the variability envelope
- `std_full_5day` — standard deviation over the whole 120 h. This is the
  **natural drift over 5 days**: how far the tag ranges across a multi-day
  record. Real: steam 2.38 t/h, bed **10.68 °C**, ms 4.33 °C, pressure
  0.77 kg/cm². Constrains the amplitude of whatever slow stochastic driver
  the sim uses. The sim currently has none — its only slow term is the
  deterministic `swing = 2·sin(t/1800)` — so its bed sd is ~2.6 °C
  (`CLAUDE.md`), roughly **4× too tight**.
- `cov_pct` — `std_full_5day` as a percent of the mean; the scale-free version,
  safe to carry across to our operating point. Bed 1.38 %, ms 0.81 %,
  pressure 0.77 %, steam 3.98 %.
- `min` / `max` / `p05` / `p50` / `p95` / `range_full` — the spread. Shape
  reference only (see caveat above).
- `net_change_end_minus_start` — drift from first sample to last; all four are
  small (|Δ| < 2.7), i.e. the record starts and ends at a similar state
  despite wandering in between.
- `quantiser_step` — telemetry resolution: 0.01 for flow and both
  temperatures, 0.01 MPa (= 0.102 kg/cm²) for pressure. The sim rounds tags to
  3 decimals, i.e. finer than the real instrument. For pressure the quantiser
  step (0.102) is **larger** than the per-sample increment std (0.047): real
  pressure noise *is* essentially the quantiser, not a Gaussian term.

### `measurement_noise` — the per-sample increment and what it is made of
- `per_sample_increment_std` — std of `x[t] − x[t−1]`. This is the headline
  **per-sample measurement-noise** figure, and it matches the values recorded
  in `CLAUDE.md` (bed 0.273, ms 0.067, steam 0.188). But it is **signal +
  noise**, not pure noise — see the next field.
- `increment_lag1_acf` — autocorrelation of those increments at lag 1. Tells
  you the *colour* of the sample-to-sample motion:
  - pure white measurement noise on a flat signal → ≈ −0.5
  - random walk → ≈ 0
  - smooth drift dominating → strongly positive
  Real: bed **+0.87**, ms **+0.55**, steam ≈ 0.0, pressure −0.08. So bed and
  main-steam temperature are *smooth slowly-moving signals with very little
  white noise*; flow and pressure are closer to random-walk / quantiser-limited.
  The sim adds i.i.d. Gaussian noise every sample, which produces increment
  lag-1 acf ≈ −0.5 — the wrong sign. This is why the sim "jitters around a
  tight mean" while the real tags "barely move sample-to-sample but wander
  widely over hours" (`CLAUDE.md`).
- `hf_noise_est_2nd_diff` — `std(x[t] − 2x[t−1] + x[t−2]) / √6`, an estimator
  that cancels any locally-linear trend and so isolates the **true white
  measurement noise**. This is the number that should set the `noise[tag]`
  dict in `sim.py`:

  | tag | `hf_noise_est_2nd_diff` (real) | `sim.py` `noise[tag]` (current) |
  |---|---|---|
  | `bed_temp_avg` | **0.056 °C** | 1.2 |
  | `ms_temperature` | **0.026 °C** | 0.8 |
  | `steam_flow` | **0.109 t/h** | 0.35 |
  | `drum_pressure` | **0.028 kg/cm²** (or model the 0.102 quantiser) | 0.05 |

  The bed and main-steam noise terms in the sim are **~20–30× too large**.
- `per_sample_increment_mad_std` — `1.4826 · MAD` of the increments, a robust
  alternative to `per_sample_increment_std`. It is **0.0** for `steam_flow`
  and `drum_pressure` because more than half of consecutive samples are
  identical (quantiser-limited); useful as evidence that those two tags are
  not Gaussian-noisy.
- `noise_to_5day_drift_ratio` — `per_sample_increment_std / std_full_5day`.
  The one-line statement of the calibration gap. Real: bed **0.026**, ms
  0.015, pressure 0.061, steam 0.079. The sim's ratio for bed is ~0.67
  (`CLAUDE.md`: per-sample 1.716 / episode sd 2.56). The sim puts almost all
  of a tag's variance at the per-sample scale; the real boiler puts almost
  none there.

### `autocorrelation_level` and `ar1` — the relaxation timescales
- `autocorrelation_level` — autocorrelation of the level series at lags from
  5 s to 24 h. Real values stay above 0.95 out to 1 min for every tag, above
  0.4 at 1 h for bed and steam, and decorrelate (cross zero) somewhere between
  6 h and 12 h. This is the **lag-1 and lag-N autocorrelation** asked for.
- `ar1.phi` — lag-1 autoregressive coefficient (`Σx[t]x[t−1] / Σx[t−1]²` on the
  demeaned series). All four are ≥ 0.997.
- `ar1.timescale_minutes` — `−5 s / ln(phi)`, the e-folding time implied by
  that phi:

  | tag | real τ | `sim.py` first-order lag | sim τ |
  |---|---|---|---|
  | `steam_flow` | **27 min** | (driven directly by `load_demand` + swing) | — |
  | `drum_pressure` | **45 min** | `PRESS_GAIN` integration | fast |
  | `bed_temp_avg` | **255 min** | `st["bed_temp_avg"] += (target − …) * 0.010` | ≈ 8 min |
  | `ms_temperature` | **707 min** | `st["ms_temperature"] += (target − …) * 0.02` | ≈ 4 min |

  The sim's bed and main-steam lags relax **30–170× faster** than the real
  unit. The lag gains (`0.010`, `0.02` per 5-s step) need to drop by roughly
  that factor, or the tags need to be driven by a slow stochastic process with
  these mean-reversion times. Note the AR(1) τ is inflated by the slow common
  drift and should be read as an order-of-magnitude target, not a fitted
  constant.
- `ar1.innovation_std` — std of the AR(1) residual `x[t] − phi·x[t−1]`. With
  phi ≈ 1 this nearly equals `per_sample_increment_std`; kept for completeness.

### `drift_by_horizon` — how far a tag moves over a fixed window
For each horizon (5 s … 24 h), the std / mean-abs / rms of `x[t+L] − x[t]`
over all `t`. `drift_by_horizon["1h"].increment_std` is the **natural drift
over 1 hour**:

| tag | 1 h drift (std of Δ) | 1 min drift | 24 h drift |
|---|---|---|---|
| `steam_flow` | 2.37 t/h | 0.65 | 3.16 |
| `bed_temp_avg` | **11.54 °C** | 2.27 | 14.53 |
| `ms_temperature` | 5.25 °C | 0.66 | 6.43 |
| `drum_pressure` | 1.05 kg/cm² | 0.22 | 1.12 |

Drift keeps growing out to ~12 h then flattens (mean reversion). These
numbers constrain the **amplitude and bandwidth of the slow driver** the sim
needs: a process that moves bed temperature ~11 °C in an hour and ~15 °C in a
day, not the sim's fixed ±2 t/h / ±(a few °C) sinusoid.

### `within_1h_block_ptp` — what a rate/MAD check actually sees
Peak-to-peak of the level inside consecutive 1-hour blocks (720 samples),
summarised as mean / p50 / p95 / max. This is the swing a windowed check in
`fieldmind/agent` sees on a *normal* hour:

| tag | mean p2p | p95 p2p | max p2p |
|---|---|---|---|
| `bed_temp_avg` | 24.6 °C | 38.3 °C | 45.1 °C |
| `ms_temperature` | 10.6 °C | 17.3 °C | 26.1 °C |
| `steam_flow` | 5.3 t/h | 7.6 t/h | 10.8 t/h |
| `drum_pressure` | 2.6 kg/cm² | 4.5 kg/cm² | 6.3 kg/cm² |

Directly relevant to the `checks.rates`, `checks.rates_long` and
`checks.validity.max_step` thresholds in `configs/base.yaml`, and to
`CLAUDE.md`'s point that family E's ~40 °C drift is a 15-sigma signal against
the current sim's variability but only ~4-sigma against the real unit's:
a real normal hour already swings the bed by ~25 °C.

## `cross_tag_correlation`

Pearson correlation matrices over the four tags, computed three ways:

- `pearson_level` — on the raw level series. Dominated by the shared slow load
  trend, so it is the right thing to compare against the sim's steady-state
  coupling.
- `pearson_increment_1min` / `pearson_increment_1h` — on `x[t+L] − x[t]`.
  Removes the common slow trend and isolates co-movement of the *dynamics*.

Key real values (level / 1 h increments):

| pair | level r | 1 h-incr r | sim mechanism |
|---|---|---|---|
| `steam_flow` ↔ `bed_temp_avg` | **+0.87** | **+0.79** | bed_target load term + `load_coef` |
| `bed_temp_avg` ↔ `ms_temperature` | +0.35 | +0.45 | `target_ms += (bed−850)*0.35` |
| `steam_flow` ↔ `ms_temperature` | **+0.17** | +0.25 | `target_ms −= (steam−67)*0.6` |
| `steam_flow` ↔ `drum_pressure` | +0.35 | +0.27 | throttle coupling / `PRESS_GAIN` |
| `bed_temp_avg` ↔ `drum_pressure` | +0.39 | +0.38 | shared `heat_released` |
| `ms_temperature` ↔ `drum_pressure` | +0.29 | +0.38 | (indirect) |

The load→bed link is strong and real; everything else is a weak positive
(0.17–0.45). In particular **main-steam temperature is nearly decoupled from
steam flow** in reality (level r = 0.17), whereas the sim's
`−(steam−67)*0.6` term makes that coupling strong — see load-following below.

## `load_following`

- `load` — stats of the load proxy (`steam_flow`): mean 59.8 t/h, sd 2.38,
  range **50.7–70.9 t/h**. There is a genuine load excursion in the record
  (down to ~50 t/h), which gives enough leverage to fit the bed-temperature
  coefficient but not enough to pin the weaker ms/pressure ones.
- `coefficients.<tag>` — OLS of each tag on `steam_flow`, three ways:
  - `level_regression` — `tag ~ a + b·steam_flow` on the raw series.
  - `delta_1h_regression` — `Δ₁ₕtag ~ b·Δ₁ₕsteam_flow`; the dynamic gain,
    robust to slow confounds.
  - `delta_1min_regression` — same at 1 min; mostly noise, low R², kept for
    contrast.

| tag | level slope | level R² | Δ1h slope | Δ1h R² |
|---|---|---|---|---|
| `bed_temp_avg` | **+3.90 °C / (t/h)** | 0.75 | +3.85 | 0.62 |
| `ms_temperature` | +0.31 °C / (t/h) | 0.03 | +0.56 | 0.06 |
| `drum_pressure` | +0.11 kg/cm² / (t/h) | 0.12 | +0.12 | 0.07 |

What this constrains:

- **`configs/base.yaml` → `checks.load_coef_degc_per_tph`** (currently `2.75`,
  its comment says "fitted by least squares on the NORMAL episodes … 2.75
  °C/TPH"). The real reference boiler gives **3.90 °C/(t/h)** with R² = 0.75,
  consistent between the level fit and the 1-hour-difference fit. The sim's
  NORMAL episodes are producing a load response ~30 % shallower than the real
  unit. (Also compare `sim.py`'s `250·(heat_released − 1)` bed term, which is
  the implicit load gain there.)
- **`sim.py` `target_ms` load term `−(steam − 67)·0.6`.** Real main-steam
  temperature has essentially no linear load dependence (slope 0.31, R² 0.03).
  The sim's −0.6 t/h⁻¹ term is far stronger than anything in the reference
  data and is probably why sim ms-temperature tracks load too tightly.
- **`drum_pressure` load dependence is weak and noisy** (R² 0.12), as
  expected for a tightly pressure-controlled drum. This only loosely bounds
  `PRESS_GAIN` / the `throttle` coupling; do not fit a precise pressure-vs-load
  slope from this record.

## Summary of the calibration gaps this fingerprint exposes

1. **White measurement noise is 20–30× too large** on `bed_temp_avg` and
   `ms_temperature` (`noise[tag]` should be ~0.056 / ~0.026, not 1.2 / 0.8).
2. **Noise colour is wrong.** Real increments are positively autocorrelated
   (smooth drift); the sim's i.i.d. term gives negative increment
   autocorrelation. The sim needs a slow stochastic driver, not just a bigger
   or smaller white term.
3. **Relaxation is 30–170× too fast.** The bed (`*0.010`) and main-steam
   (`*0.02`) first-order lags imply τ ≈ 8 / 4 min against real τ ≈ 255 /
   707 min.
4. **No multi-hour wander.** Real tags drift ~11 °C (bed) per hour and have a
   5-day sd of ~10.7 °C; the sim's only slow term is a deterministic ±2 t/h
   sinusoid, giving an episode sd ~4× too small.
5. **Load response is ~30 % shallow** for bed temperature (3.90 vs the
   configured 2.75 °C/TPH) and **too strong** for main-steam temperature
   (real slope ≈ 0, sim term −0.6).
6. `drum_level` and `feed_water_flow` cannot be calibrated from this record at
   all.
