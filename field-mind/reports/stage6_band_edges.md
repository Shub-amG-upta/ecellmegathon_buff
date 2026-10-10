# Stage 6 / BAND_EDGES retune — report

## Status

**PARTIAL — plumbing landed, band VALUES reverted to Stage 5.**

`BAND_EDGES` moved out of `fieldmind/agent/l1_symbolize.py` into
`configs/base.yaml` `checks.bands` (it had been missed by the Stage-4 threshold
retune and was a duplicated constant that could rot). The wiring is now strict:
`CheckLayer` requires `cfg["bands"]`, `build_signature` refuses an empty bands
dict, `direction_and_band` raises on a partial one instead of silently falling
back to the module mirror, and `test_checks.py` asserts the mirror equals the
config.

A criterion-based **re-derivation** of the edge values was attempted and
**reverted**: it is better on every intrinsic measure but dropped the
acceptance metric (`mock Q2_top1 0.334 → 0.173`). A follow-up 2×2
(*The 2×2* section) then tested whether removing band specificity from the
matcher recovers it — it does not (best of the four cells is the status quo,
0.334; direction-only + criterion bands = 0.240). So **everything is reverted**:
the band values in the config are the Stage-5 set, `orchestrator._slopes` is
back to the raw slope, the matcher is band-specific. Baseline metrics restored
exactly (`Q2_top1 0.334`, `Q2_top3 0.505 → 0.507`, `Q1 0.743`, `Q4 0.38 /
0.926`, `Q5 0.91`, `S7 0`; the +0.002 on `Q2_top3` is from the separate
orchestrator-bug fixes, not the bands).

What survived: `BAND_EDGES` moved out of `fieldmind/agent/l1_symbolize.py` into
`configs/base.yaml` `checks.bands` (it had been missed by the Stage-4 threshold
retune and was a duplicated constant that could rot), with strict wiring —
`CheckLayer` requires `cfg["bands"]`, `build_signature` refuses an empty bands
dict, `direction_and_band` raises on a partial one, and `test_checks.py` asserts
the module mirror equals the config.

The analysis below is kept as the evidence for the advisor decision in
*Unresolved* 1; it is **not** acted on here.

## The four deciding numbers (for the attempted re-derivation)

Measured with the criterion-based bands
(`drum_level [0.03,0.15,0.50]`, `feed/steam [0.20,1.2,4.0]`,
`drum_pressure [0.03,0.15,0.60]`, `bed_temp_avg [0.50,2.5,8.3]` on the
**load-normalised** slope, `ms_temperature [0.15,0.75,3.0]`).

### 1. No-fault FLAT fraction per tag — target ≥ 0.85, none pinned at ~0.99

| tag | deadband | no-fault FLAT frac |
|---|---|---|
| `drum_level` | 0.03 | 0.998 |
| `feed_water_flow` | 0.20 | 0.848 |
| `steam_flow` | 0.20 | 0.856 |
| `drum_pressure` | 0.03 | 0.955 |
| `bed_temp_avg` (load-norm) | 0.50 | 0.908 |
| `ms_temperature` | 0.15 | 0.857 |

All ≥ 0.85. `drum_level` is 0.998 but that is legitimate — its healthy 10-min
slope is genuinely tiny and family A still clears it (below). For comparison the
**Stage-5 values** give no-fault FLAT `bed 0.065 / feed 0.275 / steam 0.281 /
pressure 0.258 / ms 0.324` — a healthy plant coded as moving on 4 of 6 tags
every tick (~7.7 spurious movement triples per no-fault episode vs ~0.6 here).

### 2. Per-family headline-tag clearance — fraction of post-onset ticks the family's headline tag reads "moving"

| family | headline | clearance | per-episode |
|---|---|---|---|
| N (no-fault) | — | — | FLAT ≥ 0.85 every tag (table 1) |
| A | `drum_level` | **0.68** | A01 .88 / A02 .65 / A03 .59 / A04 .66 / A05 .60 / A06 .69 |
| B | `water_balance` (pseudo) | **0.81** | B01 .83 / B02 .80 / B03 .78 / B04 .84 / B05 .83 |
| C | `drum_pressure` | **0.68** | C01 .77 / C02 .91 / C03 .71 / C04 .74 / C05 .37 / C06 .81 |
| D | `bed_temp_avg` (load-norm) | **0.65** | D01 .69 / D02 .65 / D03 .83 / D04 .42 |
| E | `bed_temp_avg` (load-norm) | **0.16** | E01 .14 / E02 .20 / E03 .14 |

A/B/C/D headlines clear for the bulk of their post-onset window. **D01 goes
from 0 % (the p99.7 attempt) to 69 %** — the point of the exercise. **Family E
stays below (0.16)** — consistent with the accepted Stage-4 finding that family
E is not slope-detectable and needs a cumulative-offset detector; this is
**not** a new escalation. `ep_C05_low_cv_coal` (0.37) is the one weak fault
headline; it is carried by `energy_balance HEAT_SHORT` and maps to held-out
RCA-06 anyway.

### 3. Family C sign check — raw vs load-normalised bed slope

RCA-06 / RCA-14 describe `bed_temp_avg DOWN` (heat-input deficit cools the
bed). In family C the fuel cap sags pressure, the turbine throttles, and
`slope(steam) < 0`, so the normalisation term `−coef·slope(steam)` is
**positive** and lifts the bed slope:

| episode | mean `slope(steam)` | RAW bed slope (mean / frac DOWN / frac UP) | NORM bed slope (mean / DOWN / UP) |
|---|---|---|---|
| `ep_C01_wet_coal` | −0.048 | −0.135 / 0.14 / 0.14 | −0.019 / 0.19 / 0.18 |
| `ep_C02_feeder_trip` | −0.122 | **−0.253** / 0.22 / 0.20 | **+0.044** / 0.19 / 0.24 |
| `ep_C05_low_cv_coal` | −0.065 | −0.211 / 0.15 / 0.00 | −0.053 / 0.07 / 0.05 |

**The normalisation inverts the family C bed sign** (C02: raw −0.25 → norm
+0.04, `frac UP` now exceeds `frac DOWN`). This is the same inversion
`_load_normalised_bed_slope` structurally avoids by only ever comparing to a
positive rise threshold. It did not change retrieval in the coverage run
because family C is carried by the `energy_balance HEAT_SHORT` pseudo-triple
(unaffected by normalisation) and `drum_pressure` (clearance 0.68). Recorded in
`data/physics/DERIVATIONS.md §9.1`. **The case signatures were not adjusted** —
the PDF describes raw bed temperature and is the reference.

### 4. Q2_top1 vs 0.334 — the acceptance bar

| | Stage-5 bands (raw bed) | criterion bands (load-norm bed) |
|---|---|---|
| **`mock Q2_top1`** | **0.334** | **0.173** |
| `mock Q2_top3` | 0.505 | 0.313 |
| RCA-01 rank, 4 FCV episodes (aggregated match) | 3 / 8 / 1 / 7 | **1 / 1 / 1 / 1** |
| live mock T2 top-1, B01–B05 (RCA-11) | 0.40–0.68 | 0.02–0.24 |

**0.173 < 0.334 → the band values are reverted to Stage 5.** The intrinsic
signal is better (RCA-01 fixed, noise triples gone, D01 visible) but the mock
backend's per-tick top-1 depends on the denser noisy signature: with a truthful
sparse signature a lone real triple, or a single balance pseudo-triple, loses
the k = 4 retrieval + mock re-rank to an all-FLAT case like RCA-15. The mock
re-ranks retrieved cases and does no reasoning (CLAUDE.md), so this is a
mock-harness artifact — but `Q2_top1` on mock is the stated acceptance bar and
a device demo is imminent, so the values revert.

## The 2×2 — is band specificity carrying real signal?

**Hypothesis tested:** the SLOW/MED/FAST labels in the case signatures were
assigned by reading PDF prose ("rose sharply" → FAST), never calibrated against
what magnitude the plant produces. If so, `Q2_top1 = 0.334` holds only because
the stale deadbands (20–70× too small) push episodes into MED/FAST where they
happen to agree with equally-uncalibrated case labels — and band specificity
would be noise on both sides that should come out of the matcher.

**Test:** a `band_specific` flag in `CaseLibrary.match` / `_collapse_bands` that,
when off, collapses SLOW/MED/FAST to one "moving" bucket per `(tag, direction)`
before the weighted-Jaccard (FLAT keeps its 0.35 weight). Run the 2×2 on the
mock backend, all 30 episodes. *(Experiment scaffolding — the flag, the
`bed_slope_load_normalised` flag, `_collapse_bands` — was reverted after
measurement; only the numbers are kept.)*

### `mock Q2_top1 / Q2_top3`

| | band-specific match | direction-only match |
|---|---|---|
| **stale bands (Stage-5)** | **0.334 / 0.507** | 0.243 / 0.422 |
| **criterion bands (+ load-norm bed)** | 0.173 / 0.313 | 0.240 / 0.378 |

### RCA-01 rank on the four FCV episodes — offline aggregated-signature match, 13-case library

| cell | A01 | A02 | A05 | A06 | live mock T2 top-1 (A01/A02/A05/A06) |
|---|---|---|---|---|---|
| stale + band-specific | 3 | 8 | **1** | 7 | 0.21 / 0.13 / 0.16 / 0.23 |
| stale + direction-only | 9 | 7 | 3 | 7 | 0.02 / 0.09 / 0.00 / 0.13 |
| **criterion + band-specific** | **1** | **1** | **1** | **1** | 0.30 / 0.55 / 0.13 / 0.74 |
| criterion + direction-only | 4 | **1** | **1** | **1** | 0.33 / 0.36 / 0.08 / 0.66 |

### Verdict

**Direction-only + criterion bands = `Q2_top1` 0.240 — does not beat 0.334.**
Per the pre-registered rule, **everything is reverted** (Stage-5 band values,
raw bed slope, band-specific matcher) and this table is the evidence left for
the advisor.

What the 2×2 actually shows:

- **The stated hypothesis is not supported.** Direction-only matching *lowers*
  `Q2_top1` in **both** band regimes (stale 0.334 → 0.243; criterion 0.173 →
  0.240). Band granularity is therefore **not** pure noise — under the Stale
  bands it carries real aggregate discriminating signal, and the best of the
  four cells is the status quo (stale + band-specific).
- **The hypothesis's mechanism is partly real, though.** Direction-only
  *rescues* the criterion bands (0.173 → 0.240) — it stops penalising the
  criterion config for slow faults (B/C/D) landing in `SLOW` when their case
  label says `MED`/`FAST`. It just does not recover enough to reach the
  baseline.
- **RCA-01 (FCV) retrieval is genuinely best under criterion + band-specific:**
  offline rank **1/1/1/1** (raw 0.15–0.61) vs the status quo's 3/8/1/7, and the
  highest live per-tick top-1 of any cell (A06 0.74). The criterion bands fix
  the FCV cluster; what drags their aggregate `Q2_top1` down is families B/C/D
  losing the *noise-driven* matches the stale bands gave them (documented in
  *Unresolved* 1).
- So the advisor question is sharper than "keep or drop bands": the criterion
  bands **improve the one clean fault family and degrade the mock aggregate**,
  and no matcher variant tried recovers the aggregate. Resolving it needs a
  retrieval metric that is not a mock per-tick top-1 count.

## What landed (kept)

| change | file | why it stays |
|---|---|---|
| `checks.bands` block (Stage-5 values) | `configs/base.yaml` | the edges are now in the one config file a sweep/retune touches; Stage 4 missed them because they were only in code |
| `CheckLayer.bands = cfg["bands"]` (strict, no default) | `l1_checks.py` | a missing block is a config error, not a silent fallback |
| `build_signature(…, bands)` required; raises on empty | `l1_symbolize.py` | the runtime never uses the mirror |
| `direction_and_band(…, bands)` raises on a partial dict | `l1_symbolize.py` | a duplicated constant that can diverge is how `BAND_EDGES` went stale |
| `BAND_EDGES` mirror = device fallback only; test asserts it equals config | `l1_symbolize.py`, `bench/test_checks.py` | keeps the two copies from drifting |
| `orchestrator._slopes` docstring records the reverted normalisation attempt | `orchestrator.py` | so the next person does not re-try it blind |
| DERIVATIONS §9.1 — raw vs load-normalised bed in the symbolizer, family C sign inversion | `data/physics/DERIVATIONS.md` | the finding outlives the revert |

## Verification

| step | result |
|---|---|
| L1 unit tests | `python bench/test_checks.py` → **20 / 20** (banding: small-move-is-SLOW, true-stillness-FLAT, fast-level-FAST, mirror==config, `direction_and_band` raises on a partial bands dict). |
| full harness | `python run_demo.py --all --backend mock` → **30/30**, `Q1 0.743`, `Q2_top1 0.334`, `Q2_top3 0.505`, `Q3 1.0`, `Q4 0.38 / 0.926`, `Q5 0.91`, `S7 0.0` — **identical to the pre-Stage-6 baseline** (`stage5_case_library.md`). |
| mutation check (tests) | changed `l1_symbolize.BAND_EDGES["drum_pressure"]` `0.60 → 0.61` → `test_checks.py` "mirror equals config" **FAILS**; restored → 20/20. |
| sim self-tests | `python -m data.generator.sim` → exit 0, 11 mutations caught (Stage 6 touched no simulator code). |
| validation gate | `python -m bench.validate_data --episodes data/episodes` → 30/30 PASS (bands do not enter the gate). |
| independent re-derivation (of the criterion table) | no-fault deadband candidates measured over two disjoint seed sets (700–711, 950–969) as well as 900–911; the criterion-based bed deadband (0.5, load-norm) holds 0.88–0.91 no-fault FLAT across all three and family D clearance 0.61–0.69. Not used (reverted). |

## Constants

No constant changed value. `checks.bands` now holds the **Stage-5** band edges
verbatim; provenance is unchanged from the original `l1_symbolize.BAND_EDGES`
(a coarse hand-set vocabulary — see *Unresolved* 1).

## Numbers that changed

| quantity | before | after |
|---|---|---|
| location of the signature band edges | hard-coded dict in `l1_symbolize.py` | `configs/base.yaml checks.bands` (+ asserted mirror) |
| `direction_and_band` on a partial/empty bands dict | silent fallback to the module dict | raises |
| every Q/S metric | — | **unchanged** (baseline restored) |

## Unresolved — written into the report per the working agreement

1. **The deadbands were not the problem; the case signatures' band LABELS are.**
   (Advisor item.) The 2×2 rejected the "drop band matching" idea — bands carry
   real information — but it also isolated where the aggregate loss comes from.
   Criterion bands give **RCA-01 offline rank 1 on all four FCV episodes** (was
   3 / 8 / 1 / 7): they fix the single most important case in the library. The
   aggregate `Q2_top1` falls anyway because **~9 of the 13 library case
   signatures carry `SLOW`/`MED`/`FAST` labels extracted from narrative prose**
   ("rose sharply" → `FAST`, "gradual drift" → `SLOW`) that **no episode can
   reach under honest deadbands** — the stale deadbands only "matched" them by
   also firing on healthy wander (~7.7 noise triples per no-fault episode). The
   fix is one of:
   - **re-derive each case's band labels from what the simulator actually
     produces for that mechanism** (measure the post-onset slope percentiles per
     tag for the episode(s) that instantiate the case, assign the label from the
     band those percentiles fall in) — this touches `case_library.json`, so it
     needs advisor sign-off and a note that the label then reflects *our
     simulator*, not the PDF; **or**
   - **make band matching soft** — credit an adjacent-band pair partially
     (`SLOW`↔`MED` and `MED`↔`FAST` at, say, 0.5) instead of exact-triple only,
     so a fault that lands one band off its label is not scored as a total miss.
   Either removes the dependence on an uncalibrated granularity without dropping
   the (real) directional + magnitude signal.

2. **HIGH PRIORITY — the offline coverage tool disagrees with the live
   harness, and every Stage 5 / Stage 6 coverage conclusion rests on the
   offline tool.** (Record only — not investigated this pass.)
   `mock Q2_top1 = 0.173` over 17 scored episodes is ≈ **3** correct episodes.
   But the offline tool reports **4 FCV episodes at RCA-01 rank 1** under the
   same criterion bands, which alone would be `4/17 ≈ 0.235` — and that is
   before any other family contributes. The two tools are measuring **different
   objects**: the offline tool aggregates the signature over the *whole
   episode* (the union of every triple ever seen post-onset, persistence ≥ 0.15)
   and matches once; the live agent matches **per tick** on that tick's
   signature, k = 4, then the mock re-ranks. A per-episode union signature is
   denser and more stable than any single tick's, so the offline rank is
   systematically more favourable. **This needs settling before the Stage 5 and
   Stage 6 coverage conclusions are trusted** — including "RCA-01 borderline",
   the cluster table's episode→case retrievals, and every "reproduces / does not
   reproduce" verdict, all of which are offline-tool outputs. The likely
   resolution is to make the offline tool replay per-tick and report the
   per-tick top-1 / top-3 the harness would, keeping the aggregated view only as
   a secondary diagnostic.

3. **If the criterion bands are ever adopted, bed must be load-normalised only
   in the rising direction** (DERIVATIONS §9.1). As measured, normalising the
   family C bed slope flips it from DOWN (matches RCA-06/14) to ~flat/UP. A
   one-sided normalisation (`min(0, raw)` style, or require `steam_flow` flat)
   avoids it; a symmetric one does not.

4. **`ms_temperature` is not load-driven** (no-fault `slope(ms) ~ slope(steam)`
   R² = 0.02) — normalising it does nothing, so it would stay raw. Recorded so
   the asymmetry with bed is not read as an oversight.

## Next

Orchestrator known bugs 1 and 2 are fixed in a separate commit
(`reports/stage6_orchestrator_bugs.md`). The band edges are left at the
Stage-5 values pending the advisor decision in *Unresolved* 1; the 2×2 above
is the evidence for that conversation. No further band iteration —
experiment scaffolding (`band_specific` / `bed_slope_load_normalised` flags,
`_collapse_bands`) was reverted after measurement.
