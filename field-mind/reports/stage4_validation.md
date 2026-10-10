# Stage 4 / validation gate — report

## Status
DONE — `bench/validate_data.py` written and wired into `episode_build`. A failing
episode is never written. All 30 regenerated episodes pass.

## What was built

`bench/validate_data.py`:

- **`validate_episode(spec, rows, gt, diag=…)`** — the gate, called by
  `data/generator/episode_build.build_episode` after the rows/notes/ground-truth
  are built in memory and **before** any file is written. Returns a
  `ValidationReport`; `build_episode` writes nothing and prints the FAIL reasons
  if `.failed`.
- **`run_suite()`** — the long no-fault statistical checks (promoted from
  `sim._ou_recheck`): `steam_flow` sd, bed 30-min autocorrelation, and the
  **steam↔bed `pearson_level` acceptance gate [0.77, 0.95]**.
- CLI: `--episodes DIR` (re-validate on disk against `catalogue()`, exit 1 on any
  FAIL), `--suite`, `--selftest`.

### Checks

| id | what it asserts | independent of the sim? |
|---|---|---|
| C1 mass closes | `slope(drum_level − swell) == K_level(p)·(feed − steam − bd − leak)` recomputed from the **emitted columns** over steady windows (tight FAIL band) + a transient sign-agreement check. Dropout-tag windows skipped. | yes — recomputes from emitted CSV + `G_sw` + `K_level` |
| C2 energy closes | diag `dp/dt` re-integrates to the emitted pressure trace (≤0.15 kg/cm²); no-fault pressure stationary; per-family direction of the pressure move | partly — the re-integration is outside `step()` |
| C3 p ↔ Tsat | every row: `Tsat(drum_pressure)` in 240–315 °C, and `bed_temp_avg` & `ms_temperature` both `> Tsat + 5` | yes — IF97 steam tables only |
| C4 noise stats | emitted 2nd-difference white-noise estimate within [0.25×, 4×] of `fingerprint.hf_noise_est_2nd_diff` per tag; suite adds the steam↔bed gate | yes — fingerprint.json |
| C5 physical range | every tag inside `configs/base.yaml checks.validity.physical_range` (FAIL); a tighter operating envelope → WARN | yes |
| C6 family E | E episodes cross **no** limit (bed 880/940, level 20/10, pressure 55, ms 550) and the timeline is ⊆ {NORMAL, DEVIATION} | yes |
| C7 timeline sane | contiguous, covers [0, dur], valid states; `trip_t` set **iff** a TRIP_IMMINENT segment exists; a non-caught fault (not family E) actually develops | yes |
| C8 deterministic | a second `BoilerSim(spec).run()` is byte-identical; on-disk timeseries == a fresh run | yes |

## Verification

| step | result |
|---|---|
| ran the module | `python -m bench.validate_data --episodes data/episodes` → **30/30 passed**, exit 0. One benign WARN (`ep_E03`: C1 residual 0.20 %/min in 1/52 steady windows — at the boundary, not a FAIL). |
| self-tests | `python -m bench.validate_data --selftest` → **7/7 passed**. Builds one known-good episode and six broken ones (bed driven below saturation, tag out of physical range, spurious level drift, family-E bed +40 °C over the alarm, `trip_t` with no TRIP_IMMINENT segment, a fault family with no schedule) — each broken one FAILs the expected check. |
| suite | `--suite` → PASS. steam sd 2.06 (fp 2.38), bed 30-min acf 0.57 (fp 0.57), steam↔bed +0.88 (fp +0.87). |
| independent re-derivation | steam↔bed cross-correlation: `sim._pearson` gives **+0.871**; an independent route (OLS `bed ~ a + b·steam`, `sqrt(R²)`) gives **+0.872** — 0.1 % difference. R² = 0.760, i.e. "bed 76 % explained by steam" (the real-trace target). |
| mutation check | (a) gate vs emitted-row corruption: `--selftest` above, 6/6 caught. (b) gate vs a sim physics-constant corruption: `G_SW_PCT_PER_KGFCM2` sign flipped → regenerate `ep_C01` (large pressure sag → large swell term) → **C1 FAIL, caught**. `VOL_PER_PCT_M3 ×0.5` → C5 FAIL (level leaves range), also rejected. |

## Direction checks
n/a — the gate asserts closure/consistency, not physical sign. The sim's own 8
direction checks still pass (see `sim` self-test output).

## Constants introduced
| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `_STATE_DEBOUNCE_S` (episode_build) | 60 s | ASSUMED — annunciator on-delay; the shortest that removes threshold flicker from the timeline | 30–120 s | `state_timeline` segment boundaries only |
| `ENVELOPE` (validate_data) | steam 25–95, p 40–80, bed 650–1050, ms 380–600 t/h·°C | ASSUMED — operating-envelope WARN band, wider than any real excursion in the 30 episodes | — | WARN severity only, never FAIL |
| C1 steady-window band | 0.20 %/min residual | FITTED — ~1.5× the no-fault 5-min residual p99.7 (0.13) | — | which episodes the gate rejects for mass non-closure |
| C4 per-tag noise band | [0.25×, 4×] FAIL, [0.4×, 2.6×] WARN of `hf_noise_est_2nd_diff` | ASSUMED — generous because a 45-min episode is a short sample | — | gate rejection on noise mis-scale |

## Numbers that changed
| quantity | old | new | note |
|---|---|---|---|
| `build_episode` return / write order | writes files, then returns gt | builds in memory → validates → writes only on PASS | a REJECTED episode leaves no directory |
| `state_timeline` | commits a segment on every per-sample state change | 60 s debounce before a transition commits | `ep_B03` went from ~230 one-second segments to 6 |

## Disagreements recorded, not resolved
1. **C1 and C2 share the sim's own modules** (`K_level`, steam tables), so a
   corruption of a constant *both* the sim and the reconstruction use is
   invisible to the magnitude checks (it stays self-consistent). C1's transient
   **sign** check and C3 (independent IF97) still catch sign / calibration
   errors; a shared-magnitude error would need an external reference the project
   does not have. Recorded.
2. **`ep_E03` sits on the C1 steady-window boundary** (0.20 %/min residual in
   1 window of 52). The residual is the shrink-swell term the L1-style water
   balance in C1 does not fully model during the very slow pressure creep of a
   fouling episode; it is within the WARN band, not a FAIL. If family-E severity
   is ever raised this may tip to FAIL and the C1 tolerance or the swell handling
   would need revisiting.

## Blocked / needs a decision
None from the gate itself. The gate **surfaced** two decisions that were made in
`stage4_regeneration.md` (the DEVIATION-band pressure term, and family-E
detectability).

## What I could not verify
- **C2's per-family pressure-direction expectations** are asserted as WARN for
  D/N/E and FAIL only for "family C must sag" — the milder direction rules are
  heuristics, not derived bounds, and a legitimately unusual seed could trip a
  WARN. None did across the 30 episodes.
- The **suite** runs on 3 × 30 h synthetic no-fault runs, not against the real
  xinan trace (different boiler / operating point — CLAUDE.md §9).
