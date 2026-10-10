# Phase 0a, Session 1b: measurement hygiene: report

All numbers are **mock backend**. They measure the deterministic and retrieval layers only. They are **not agent
results**; the mock re-ranks retrieved cases and does no reasoning. Energy is `null` (no device).

## Status
PARTIAL. Step 1 (dev set) done. Step 2 (bed slope) is a finding only. Step 3 (band calibration) was run and **not adopted**
(human decision, below). Step 4 (belief saturation on the old edges) is measured. Step 5 (fix (c), display-only) is built, measured on dev and **KEPT**. The phase-reviewer ran and its fixes are committed ("Phase-reviewer findings"). The one-shot reporting run is done (Step 6 results), `results/baselines/single_v3_summary.json` is written, and the advisor list is added. Remaining: the real-model and NPU runs (Session 2), and the open list.

Commits (main, none pushed): `f2f9f5d` dev set. `232e08c` calibration + sanity scripts. `e726085` fix (c) pre-registration. `7d1f8c6` fix (c). `b5146d2` report answers. `dce395c` / `ca2814e` / `fc5157f` /
`6ac94ff` phase-reviewer fixes 1 to 4. Branch `exp/band-edges-p85`
(`dd5d41a`): the p85 edges, preserved, not for main.

## Verification
| step | result |
|---|---|
| ran the module | `episode_build --set dev`: 36/36 pass the gate on the first seed, no retries. `band_calibration`, `band_sanity` and three mock dev runs executed (outputs in the session log) |
| self-tests | pytest 46 passed / 0 failed; `bench/test_checks.py` 20/0 (21/0 on the experiment branch), `test_envelope_logging.py` 16/0, `test_orchestrator.py` 22/0 |
| independent re-derivation | dev FLAT fraction per tag at the p85 edges: script (`Orchestrator._slopes`) vs numpy `polyfit` on the raw CSVs: drum_level 1.000/1.000, feed 0.856/0.856, steam 0.862/0.862, pressure 0.861/0.861, bed 0.850/0.850, ms 0.858/0.858; relative difference 0%. Also dev Q5: evaluator 0.98 vs mean of per-episode rates recomputed from the runs file 0.98 (0%); the pooled rate is 0.87 |
| mutation check | dev seed offset 1000 → 0: caught by `test_dev_seeds_disjoint_from_reporting_and_calibration` and `test_every_fault_has_a_twin_with_identical_scenario`. `max` → `min` in `calibrate()`: caught by `test_raises_deadband_to_p85_and_keeps_multiples` and `test_never_lowers_a_deadband_that_already_meets_the_criterion`. `__pycache__` cleared between runs |

## Decisions taken
1. **[human] Item 2 skipped:** calibrate the raw bed slope; no load normalisation.
2. **[human] Deadband rule:** `max(current, p85)`, not p85 for every tag.
3. **[human] Family E** is reported, not a stop.
4. **[human] Band edges not adopted (option 1).** The keep rule, fixed before results, fails by a wide margin: dev
   tie-fair belief top-1 0.375 → 0.098, belief group 0.502 → 0.067, retrieval top-1 0.434 → 0.193, the same pattern as
   Stage 6. The edges are preserved on `exp/band-edges-p85`; `main` keeps the Stage-5 edges.
5. Gate-retry policy for dev (seed +10000, +20000, never touch severities) was prepared and **not needed**.
6. The bed "SLOW" test in `bench/test_checks.py` used 0.08 °C/min, which is FLAT under the new bed deadband (0.74). Its
   input moved to 1.0 °C/min **on the experiment branch only**; main is unchanged. This was an expected consequence of
   the edges, not a transcription error.

## Direction checks
None: no physical response was added or changed. Band edges are a descriptor vocabulary, not a plant model.

## Constants introduced
| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `DEV_SEED_OFFSET` | 1000 | design choice: keeps dev seeds off every seed already used (0–3, 100–604, 700–711, 900–911, 950–969) | any offset that keeps the disjointness test passing | dev fault seeds |
| `DEV_NOFAULT_SEEDS` | 1100–1111 | same | same | the 12 extra no-fault episodes |
| `TARGET_FLAT` | 0.85 | **user-set** criterion, fixed before any result | n/a | `band_calibration.py` only |
| p85 band edges | see branch | **FITTED** to dev no-fault, method in the script | **not adopted** | nothing on main |

## Numbers that changed
**Decision behaviour on `main` is unchanged by this session** (no threshold, band edge or case data touched; fix (c) is
display-only). What differs by design: new field `confidence_shown` on each hypothesis, `Assessment.confidence` (now the shown
value of rank 1), and the evaluator `low_conf_rate` (reads the shown value; the old definition is `low_conf_rate_decision`).
On dev the decision fields are identical across 5,850 assessments. That the 30 reporting episodes still reproduce
`single_v2_summary.json` on every other key is a **prediction** (Step 6 below) until the one-shot reporting run.

### Dev set: before and with the p85 edges (dev, mock; the edges are NOT in `main`)
| metric | dev, main edges | dev, p85 edges | delta |
|---|---|---|---|
| Q1 macro-F1 | 0.782 | 0.782 | 0 |
| Q2 top-1 / top-3 (legacy) | 0.332 / 0.453 | 0.148 / 0.238 | −0.184 / −0.215 |
| Q3 / Q5 / S7 | 1.0 / 0.98 / 0 | 1.0 / 0.98 / 0 | 0 |
| Q4 precision / recall | 0.357 / 0.882 | 0.460 / 0.853 | +0.103 / −0.029 |
| Q6 lead time (min) | 32.4 | 33.9 | +1.5 |
| library top-1 / top-3 | 0.434 / 0.593 | 0.193 / 0.312 | −0.241 / −0.281 |
| library group top-1 | 0.489 | 0.229 | −0.260 |
| library belief top-1 tie-fair | 0.375 | 0.098 | −0.277 |
| library belief group / tie-fair | 0.502 / 0.477 | 0.067 / 0.140 | −0.435 / −0.337 |
| library belief tie rate | 0.270 | 0.730 | +0.460 |
| held-out belief group / tie-fair | 0.709 / 0.700 | 0.000 / 0.000 | −0.709 / −0.700 |
| held-out low-conf rate | 0.245 | 0.340 | +0.095 |

Reference, reporting set (v2): library tie-fair belief top-1 0.438, belief group 0.574, held-out belief group 0.545. Dev
is lower on both library belief metrics. That fits the earlier selection on the reporting set, but I have not tested that
explanation.

### Why the matcher collapsed: rank-1 case, post-onset fault ticks (1,968 ticks, from the existing run logs, no reruns)
| case | belief rank-1, main → p85 | retrieval/model-order rank-1, main → p85 |
|---|---|---|
| RCA-15 (∅ group) | 11.9% → **59.4%** | 9.7% → **57.2%** |
| RCA-09 (∅ group) | 4.7% → 10.6% | 2.4% → 4.3% |
| RCA-10 (∅ group) | 0.5% → 0.1% | not in the top list |
| RCA-13 | 9.8% → 20.6% | 6.9% → 15.5% |
| RCA-07 | 28.3% → 0.0% | 33.8% → 5.4% |
| RCA-11 | 16.3% → 0.8% | 24.9% → 3.7% |
| RCA-14 | 12.5% → 0.0% | 9.5% → 3.1% |

The ∅ trio (RCA-09/10/15, empty signatures) goes from 17.1% to 70.0% of belief rank-1 ticks: with most tags reading FLAT,
FLAT-heavy cases win. On the normal-episode ticks that have hypotheses (10), rank-1 moves from RCA-07 to RCA-13. RCA-13's
signature has **5 FLAT triples out of 6** (the only movement is `bed_temp_avg UP SLOW`), so it is FLAT-heavy like the
∅ cases; that is consistent with the takeover, but I have not tested it beyond the count.

## Findings
### Item 2: bed slope history
The signature bed slope has been the **raw** 10-min slope since the first commit `e7c756d` (2026-08-31). Load
normalisation in the signature existed only as uncommitted Stage 6 scaffolding (`018fbd8` → `aa86567`, recorded in
`888b01a`); it was reverted because mock Q2 top-1 fell 0.334 → 0.173 and because a symmetric normalisation flips family
C's bed sign (C02 raw −0.25 → +0.04 °C/min, DERIVATIONS §9.1). No design document (both plan PDFs, GUIDE §3.5) specifies a
load-normalised signature bed; only the L1 drift/PATTERN checks and the multi-agent drift watcher use a load-corrected bed.
So "restore" was a mismatch with the instruction, not a regression; no code changed.

### Band calibration (step 3): tag-level separation vs matcher collapse
Criterion: deadband = max(previous, p85 of healthy 10-min |slope|), 2 s.f. rounded up; multiples kept; 12 dev no-fault
episodes, 1,260 windows. Pooled FLAT is 0.85–0.86 per tag (per-episode 0.52–1.00). Share of post-onset ticks on which the
family's headline tag reads as moving (healthy ≈ 0.15 under the new edges; 0.76–0.95 under the old):

| family | headline tag | p85 edges | Stage-5 edges | reading |
|---|---|---|---|---|
| A | drum_level | 0.52 | 0.52 | unchanged (already 100% FLAT healthy) |
| B | feed_water_flow (banded proxy) | 0.11 | 0.80 | see below |
| C | drum_pressure | 0.68 | 0.85 | separates clearly |
| D | bed_temp_avg | 0.53 | 0.97 | separates clearly |
| E | bed_temp_avg | 0.07 | 0.93 | reported only |

- **Tag level.** The new edges separate C (0.68) and D (0.53) from healthy (≈0.15) clearly, where the old edges separated
  almost nothing (healthy read as moving 0.76–0.95 of the time). Yet the matcher collapses (table above). That points at the
  uncalibrated case-signature band labels (Stage 6 "Unresolved 1"), not at the edges themselves: the case labels were read
  from PDF prose and are reachable only by the noisy, dense signature the old edges produce.
- **Family B.** The banded proxy `feed_water_flow` does not separate the fault under either set of edges: 0.80 vs a healthy
  ~0.8 under the old edges (it moves with healthy wander), and 0.11 vs 0.15 under the new ones. B's real Stage 6 headline,
  `water_balance`, comes from BALANCE facts and does not depend on `checks.bands`; it is present on 0.67 of post-onset
  ticks (B01/B02/B05 0.86, B04 0.92, B03 slow leak 0.34). The stop condition triggered for the signature proxy only.
- **Family E.** 0.07 under the new edges. Reported only; consistent with the Stage 4 finding that E needs a cumulative
  drift detector.
- `bench/band_sanity.py` reproduces this table (`--bands-yaml` for the branch config). Its moving/FLAT verdict is only
  meaningful for edges that meet the 85%-FLAT criterion.

### Glossary
- **Q5** (`Q5_fp_per_hour`) is the **mean of per-episode rates** (0.98 on dev). The pooled rate (all FP ticks over all
  hours) is 0.87. Both are correct; the difference is weighting by episode length.

## Step 4: belief saturation on dev, main (Stage-5) edges
Tool: `bench/belief_saturation.py` (read-only). Validated first on the reporting-set runs: it reproduces the Phase 0a
Check 1 figures (library 729 ticks at the clamp, 388 ties, 247 ties at the clamp = 63.7%, mean shown conf 0.619, 45.6%
at >= 0.9; held-out 203 ties, 168 at the clamp, 40.4% confident-and-wrong; median 0.728 vs 0.729, rounding). Test:
`tests/test_belief_saturation.py`; mutation `>=` -> `>` on the clamp test was caught by both of its tests.

| dev, mock, main edges | library (1,327 ticks) | held-out (273) | no-fault (10, 5 episodes) |
|---|---|---|---|
| belief rank-1 at the +4.0 clamp | 621 (46.8%) | 160 (58.6%) | 0 |
| top-of-belief ties | 301 (22.7%) | 229 (83.9%) | 1 |
| ...ties at the clamp, share of ties | 215 (71.4%) | 155 (67.7%) | 0 |
| shown rank-1 conf: mean / median | 0.685 / 0.885 | 0.765 / 0.980 | 0.775 / 0.711 |
| shown conf >= 0.9 | 48.9% | 60.8% | 20% |
| confident (>0.5) and wrong group | 233 (17.6%) | 109 (39.9%) | n/a |
| shown conf deciles 0.0-1.0 | 0, 208, 142, 46, 43, 45, 51, 58, 85, **649** | 0, 21, 21, 12, 7, 16, 7, 8, 15, **166** | 0,0,0,0,0,1,2,3,2,2 |

Additional, from the same logs. Of the ticks that sit AT the clamp (denominator: library 621, held-out 160; **not** all
scored ticks), **29.0% (180 of 621) of library and 49.4% (79 of 160) of held-out have a rank-1 case in the wrong group**.
As a share of all scored ticks (1,327 and 273) that is 13.6% and 28.9%, which is why it is below the confident-and-wrong
rows above (17.6% and 39.9%): that row counts any shown conf > 0.5, not only the clamp. The clamp is reached a median of 7 ticks after the first post-onset hypothesis
(library, 9 of 13 episodes reach it; min 2, max 39) and 7.5 ticks on held-out (4 of 4). The largest per-tick step for a
steady rank-1 is +1.20 (library) and +0.77 (held-out).

**N03-type fast climb.** `dev_N03_normal` itself is quiet (no hypotheses at all). The same climb shows on `dev_N05_normal`:
RCA-07 rank-1 with log-odds +0.90, +2.00, +3.09, +4.00 over ticks 11-14 (shown conf 0.711, 0.881, 0.957, 0.982), i.e. the
clamp in four ticks on a healthy plant; `dev_N09_normal` (the other N03-spec copy) shows RCA-07 at +0.90, +1.81 (0.711,
0.859) before its hypotheses disappear. No-fault coverage is thin: 10 ticks in 5 of 12 episodes, so these rows are
anecdotes, not rates. The reporting `ep_N03` table will come only from the end-of-session reporting run.

### Overconfidence fixes proposed (belief is NOT changed; the human chooses)
Common to every option, **fixed now, before any measurement of the fix**: measured on dev, mock, against the current
`main` as the preceding kept state. Gate G: library tie-fair belief top-1 and belief group each drop by no more than 0.01
(standing keep rule); Q1, Q3, Q5, S7 unchanged; legacy Q2 top-1 does not drop by more than 0.01; held-out reported, not
gating (one held-out case). Calibration metric defined now: **ECE** over 5 equal-width bins of shown rank-1 confidence
against whether the shown rank-1 case lies in the true case's group, on library + held-out scored ticks. Its baseline is
measured on the unchanged tree at the start of the chosen fix, before any code change.

| | what it does | constants | predicted effect (not measured) | keep rule, in addition to G |
|---|---|---|---|---|
| **(a) Decay** | multiply log-odds by (1 - lambda) each tick before the update | lambda from a half-life; DERIVED start: the 10-min signature window = 20 ticks, lambda = 0.034; ASSUMED range of half-life [10, 120] ticks (same bounds as the retirement setting) | **Weak.** A steady +1.2/tick step against a 3.4% decay still reaches +4.0 in about the same 4-5 ticks, so saturation under continuing evidence is untouched; it only shortens how long a stale hypothesis lingers. Retirement had the same character and failed the keep rule (library tie-fair -0.025), and a decay strong enough to bite would need a tuned value | library share of ticks at the clamp falls by at least 0.10 (0.468 to <= 0.368) AND confident-and-wrong share does not rise. I expect it to fail both |
| **(b) Tie-break by this tick's retrieval score** | among exactly tied log-odds, order by the current tick's retrieval score | none | Ties stop being arbitrary insertion order: library tie share (0.227) should fall toward the cases that are genuinely identical (RCA-09/10/15, RCA-14/18). **Does not touch shown confidence**: tied hypotheses still show 0.982. The tie-fair metric assumes ties are uniform, so it will not register the gain; plain belief top-1 will | plain library belief top-1 does not drop AND exact-tie share (equal log-odds and equal score) falls to at most half of 0.227 AND shown confidence values bit-identical. Fixes ties only, not overconfidence |
| **(c) Confidence from the rank-1 vs rank-2 margin** | shown conf = sigmoid(log-odds rank-1 - log-odds rank-2); sigmoid(log-odds) if only one live hypothesis | none (no free constant: it is the two-way odds ratio) | **Strongest on the measured cause.** A tie (including a clamp-vs-clamp tie) shows exactly 0.5, so the 83.9% tied held-out ticks stop being "confident" (conf > 0.5) and its confident-and-wrong share (0.399) should collapse; library should fall by the part that sits on ties (magnitude unverified; untied clamp ticks with a wide margin stay confident, e.g. 4.0 vs 1.0 shows 0.95). Belief ranking is unchanged, so belief metrics should be bit-identical. **Side effect to flag:** the verifier fires inside conf [0.35, 0.75], so ties at 0.5 enter that band and S4 (LLM invocation rate) will rise on mock | hypothesis order and belief metrics bit-identical (display only, applied after sorting); ECE strictly lower; confident-and-wrong share lower on library AND held-out; the S4 change reported with its cause (needs your OK, it changes a cost metric) |

My recommendation, for you to overrule: (c) first (lowest risk, no constants, addresses the measured shared cause), then
(b) as a separate step for the tie arbitrariness; (a) last or not at all.

## Step 5: fix (c), shown confidence from the margin: specification (written before any code)
Human choice: **(c) only, strictly display-only; the verifier trigger and every decision path keep the existing value.**

### 1. Every consumer of confidence (grep of `fieldmind/` and `bench/`)
| consumer | reads | kind | after (c) |
|---|---|---|---|
| `world_model.update_hypotheses`: `h.confidence = sigmoid(log_odds)` | log-odds | producer | unchanged |
| `world_model` retire floor: `h.confidence < RETIRE_BELOW (0.08)` | decision value | decision (retirement) | unchanged |
| `world_model.rank_hypotheses`: sort by confidence | decision value | decision (order) | unchanged |
| `world_model.belief_ranking` (telemetry): carries `confidence` | decision value | telemetry | unchanged |
| `orchestrator` claims: `"confidence": h.confidence` into `_merge` | decision value | decision (deterministic wins confidence) | unchanged |
| `orchestrator` `top_conf` -> `Verifier.should_run` band `[0.35, 0.75]` | decision value | **decision (S4)** | **unchanged** |
| `Verifier.run` prompt `conf={h['confidence']}`; `Verifier.apply` caps (<= 0.35) or revises `h["confidence"]` | decision value | decision (model input and output) | unchanged |
| `orchestrator._wm_summary` ("current hypotheses: cause(0.98)") into the Diagnostician prompt | decision value | decision (model input) | unchanged |
| `l4_diagnose` validation of the model's own `confidence` number | the model's number | validation | n/a |
| `l6_gate.approve` (actions, escalation) | none (escalation is from STATE; verified by grep: no confidence) | decision | n/a |
| `l7_memory.promote` | none | n/a | n/a |
| `Assessment.confidence` | was `top_conf` (a pre-verifier copy of the decision value); no reader in the code base | display | **switches to the shown value of rank 1** |
| `asmt.hypotheses[i]["confidence"]` | decision value (also what the evaluator read) | output | **unchanged; new field `confidence_shown` added beside it** |
| `bench/evaluator.py` `low_conf_rate` (<= 0.5) | `hyps[0]["confidence"]` | reporting | switches to `confidence_shown` (falls back to `confidence` on old runs); the old definition is kept as `low_conf_rate_decision` |
| `bench/belief_saturation.py` | confidence | reporting | reads `confidence_shown` if present; `--decision-conf` forces the old value |

Rule: only the **display** (`confidence_shown`, `Assessment.confidence`) and **reporting** consumers switch. The
`confidence_shown` field is computed in the orchestrator **after** the verifier and the gate, from the final hypothesis list,
and nothing reads it afterwards, so no decision can depend on it.

### 2. Exact formula
For each hypothesis `i` in the final shown list, with decision confidence `c_i` (its final value, after any verifier cap) and
the live belief ranking `B = wm.hypotheses not retired` (log-odds `l_j`):

- if `i` has no belief entry (a model-only idea): `shown_i = c_i`.
- otherwise let `l_i` be its log-odds, and `r_i = max(l_j for j in B, j != i)`; **if `i` is the only live hypothesis, `r_i = 0`**.
- `shown_i = min(c_i, sigmoid(l_i - r_i))`, rounded to 3 decimals like `confidence`.

Consequences, each of which gets a unit test:
- **No rank-2 hypothesis** (single live hypothesis): `sigmoid(l_i - 0) = sigmoid(l_i) = c_i`, so `shown = c`. The single-hypothesis
  climb is unchanged by construction.
- **Rank-1 ahead by margin d > 0** (rival `l_2 >= 0`): `shown = min(c_1, sigmoid(d))`; e.g. +4.0 vs +1.0 shows 0.953 (not 0.982).
  If the rival is negative, `sigmoid(l_1 - l_2) >= sigmoid(l_1) = c_1`, so `shown = c_1` (the margin never raises confidence).
- **Non-leaders** have `l_i <= r_i`, so `shown <= 0.5`.
- **Two-way tie** (equal log-odds): each shows 0.5 if its `c_i >= 0.5`, else its own `c_i` (the `min`).
- **k-way tie, k >= 3** (e.g. the empty-signature trio RCA-09/10/15 at the +4.0 clamp): every tied hypothesis has an equal rival,
  so each shows **0.5, not 1/3**. The formula is a pairwise margin, not a probability over the tied set; reading ties as
  uniform (1/k) would need a softmax over all live hypotheses (the dropped option (d)). Recorded as a known simplification.
- **Both ranks at -4.0** (`l_1 = l_2 = -4.0`, `c = 0.018`): the margin is 0, `sigmoid(0) = 0.5`, but the `min` keeps
  `shown = 0.018`. **The `min` is my addition to the approved formula; the human approved it.** Without it a disbelieved
  hypothesis would show 50%. It cannot occur among live hypotheses today: `update_hypotheses` rule 4 (the confidence floor,
  `RETIRE_BELOW = 0.08`) retires any hypothesis under confidence 0.08 **unconditionally**; it is not the stale-hypothesis
  rule (`agent.belief.retire_after_ticks`, null = off by default), which is a different path. Measured: the smallest live
  log-odds in any dev tick is -2.4425 (confidence 0.08). The `min` is handled anyway.
  **Decision taken (rule 6, conservative):** the margin can only lower the shown value, never raise it.

### 3. ECE definition and baseline (dev, current main, measured before any code change)
- Scored ticks: post-onset ticks with a non-empty hypothesis list (same as the saturation table). **Decided on library
  ticks**; held-out and the pooled figure are reported.
- 5 equal-width bins of the shown rank-1 confidence on [0, 1], the last closed at 1.0. `ECE = sum_b (n_b / N) * |mean conf_b -
  fraction correct_b|`.
- **Correct = the shown rank-1 case lies in the TRUE case's group** (`case_groups.json`). Group, not case: look-alike cases
  (RCA-14/18, RCA-09/10/15) cannot be told apart by any confidence, and the confident-and-wrong measure is group-level too.
  The case-level ECE is reported as a secondary diagnostic only.

| dev, current main (decision confidence) | library | held-out |
|---|---|---|
| ECE (group), **decided** | **0.1305** | 0.4167 |
| ECE (case), secondary | 0.1391 | 0.7647 (trivial: the held-out case is never in the library) |
| confident (> 0.5) and wrong group | **233 of 1,327 = 0.176** | 109 of 273 = 0.399 |
| low-confidence share (shown conf <= 0.5) | 0.335 | 0.260 |
| library+held-out pooled ECE (group / case) | 0.1679 / 0.2459 (1,600 ticks) | |

Library bins (conf bin: n, mean conf, group-correct): 0-0.2: 208, 0.155, 0.212; 0.2-0.4: 188, 0.266, 0.298; 0.4-0.6: 88,
0.493, 0.420; 0.6-0.8: 109, 0.709, 0.413; 0.8-1.0: 734, 0.961, 0.802. The top bin carries most of the error (0.961 vs 0.802).
A fresh dev run at current main reproduces the earlier "before" run exactly except wall-clock latency fields
(`tick_latency_ms`, envelope latencies, S1 p50/p95).

### 4. Keep/revert rule for (c): fixed now; decided on LIBRARY only
Keep (c) only if **all** hold on dev, mock, against current main:
1. **Belief metrics bit-identical**: every `belief_*` key (library, held-out, per family) and every per-episode `belief_*`.
2. **Decision values bit-identical**: in every assessment the `hypotheses` order, each `confidence`, `actions`, `escalate`,
   `state`, `triage`, `llm_invoked` and `belief_ranking` equal the current-main run (only `confidence_shown` is new and
   `Assessment.confidence` differs by design).
3. **Q1, Q2 (legacy top-1/top-3, library top-1/top-3/group/sep), Q3, Q4, Q5, Q6, S4, S7 unchanged** (exact; S1 latency excluded).
   `low_conf_rate` is exempt by design (see 5).
4. **Library ECE (group) < 0.1305 AND library confident-and-wrong-group share < 0.176** (both must fall, strictly).
5. **Reported, not used to decide:** held-out ECE, held-out confident-and-wrong share (**flagged if either gets worse**),
   `low_conf_rate` (held-out, evaluator) and the library low-confidence share. `low_conf_rate` is **expected to rise**,
   because ties at exactly 0.5 count as <= 0.5. Held-out is not gating: only RCA-06 has episodes (standing rule).
6. Unit tests for every formula case above and mutation checks pass; the whole suite is green.

### 5. Predictions, written before measuring
- **P1 (single-hypothesis climb):** (c) does not change it. `dev_N05` RCA-07 still shows ~0.711, 0.881, 0.957, 0.982 on
  ticks 11-14. The condition is exact: shown changes only if a live rival has log-odds **> 0** at that tick.
- **P2:** belief metrics, hypothesis order, decision confidences, S4 and every other Q/S key are bit-identical (by construction:
  nothing that decides reads the new field).
- **P3:** held-out confident-and-wrong falls sharply (83.9% of held-out ticks are ties, which now show <= 0.5).
- **P4 (library):** confident-and-wrong falls, but by **at most the tie share of 0.227** (I believed a tie was the only thing that
  lowers a rank-1 to 0.5; **that was incomplete**, see "Where the low-confidence ticks come from": a shown rank-1 that is not the
  belief maximum also drops below 0.5. Wide-margin untied clamp ticks do stay at ~0.95-0.98). I expect a modest ECE fall, **not** a collapse.
  It can in principle rise if the ticks moved into the 0.4-0.6 bin are right far less than half the time; the rule decides.
- **P5:** `low_conf_rate` (held-out) rises from 0.245, and the library low-confidence share from 0.335.

## Step 5 results: fix (c) on dev, mock, against the rule (every clause was fixed before the run)
Built: `world_model.shown_confidences` (pure function), called in the orchestrator **after the verifier and the gate**; adds
`confidence_shown` to each shown hypothesis and sets `Assessment.confidence` to the rank-1 shown value. Evaluator
`low_conf_rate` reads the shown value; `low_conf_rate_decision` keeps the old definition.

| clause | result | verdict |
|---|---|---|
| 1. belief metrics bit-identical | the summary differs from current main in 4 keys, all `low_conf_rate*` (below). No `belief_*` key moves, per family or per episode | PASS |
| 2. decision values bit-identical | 5,850 assessments: `hypotheses` order and every `confidence`, `actions`, `escalate`, `state`, `triage`, `llm_invoked`, `belief_ranking` equal; envelope agent / status / tokens equal (0 differences). Only `confidence_shown`, `Assessment.confidence` and latency fields differ | PASS |
| 3. Q1/Q2/Q3/Q4/Q5/Q6/S4/S7 unchanged | all equal (S4 0.337, Q5 0.98, Q2 top-1 0.332, ...); per-episode keys differ only in `low_conf_rate` and `low_conf_rate_decision` | PASS |
| 4a. library ECE (group) falls | **0.1305 -> 0.0963** (-26%) | PASS |
| 4b. library confident-and-wrong falls | **233 of 1,327 (0.176) -> 57 (0.043)** | PASS |
| 5. reported, not deciding | held-out ECE 0.4167 -> 0.051; held-out confident-and-wrong 0.399 -> 0.055 (15 of 273); neither got worse, nothing flagged. `low_conf_rate` (held-out, evaluator) **0.245 -> 0.945**; library low-confidence share 0.335 -> 0.583 | reported |
| 6. tests, mutations, suite | 61 pytest pass; `bench` scripts 20/16/22 pass; 3 mutations caught (below) | PASS |

**Verdict: KEEP (c).**

Shown rank-1 confidence on dev (library): mean 0.685 -> 0.485, median 0.885 -> 0.500, share >= 0.9 0.489 -> 0.151. Held-out
median 0.980 -> 0.500, share >= 0.9 0.608 -> 0.000.

**Predictions against the result:** P1 held **in substance, not bit-for-bit, and my first write-up of it was wrong.** I first
reported "still 0.711, 0.881, 0.957, 0.982", but that table printed the *decision* confidence under a "shown" header (fixed in
review fix 4). The shown values on `dev_N05` ticks 11-14 are 0.711, 0.881, **0.950, 0.980**: from tick 13 a second live
hypothesis (RCA-15) has log-odds +0.16 / +0.12 > 0, which is exactly the condition P1 named, so shown = min(0.957,
sigmoid(3.09 - 0.16)) = 0.950. `dev_N09` (ticks 10-11) is unchanged: 0.711, 0.859. Also, N05 is not a single *live*
hypothesis (4 to 6 are live; the others sit below 0 at ticks 11-12), so "single-hypothesis climb" means "one supported
hypothesis". The climb itself (0.71 to 0.98 in four ticks on a healthy plant) is not changed by (c). P2 held (clauses 1-3). P3 held (0.399 -> 0.055). P4 held: library
confident-and-wrong fell by 0.133, under the 0.227 tie-share bound, and ECE fell modestly, not collapsed. P5 held.

Library calibration bins after (c) (conf bin: n, mean conf, group-correct): 0-0.2: 385, 0.085, 0.195; 0.2-0.4: 165, 0.286,
0.376; 0.4-0.6: 281, 0.497, 0.612; 0.6-0.8: 133, 0.711, 0.797; 0.8-1.0: 363, 0.907, 0.981. **The display has swung from
over-confident to under-confident**: group-correct now exceeds mean confidence in every library bin. Of the 301 library tied
ticks, **83 (27.6%) have a tied set entirely inside the true group** (look-alikes such as RCA-14/18), where 0.5 throws away real
group-level certainty (the margin is pairwise and group-blind). **Corrected at close-out:** I first gave this as the cause of the
under-confidence in every bin. It cannot be: a tie shows exactly 0.5, so tied ticks sit only in the 0.4-0.6 bin. Even there, the
contribution of tied ticks to that bin's gap was not measured. The lowest bin was split at close-out and the cause is the
negative margin, not ties (see "Low-bin under-confidence: measured cause" under Step 6 results). The 0.2-0.4 bin was not split
(unexplained).

### Where the low-confidence ticks come from (library; answers a review question)
Denominator of the low-confidence share: the **1,327 scored library ticks** (post-onset, non-empty hypothesis list). Before:
444 ticks at shown <= 0.5 (0.335). After: 774 (0.583). **330 ticks newly crossed** (decision confidence > 0.5, shown <= 0.5):

| cause of the crossing | ticks | share of the 330 |
|---|---|---|
| exact tie: the shown rank-1's log-odds equal its best rival's (l == r) | 149 | 45% |
| the shown rank-1 is **not the belief maximum** (l < r) | 181 | 55% |
| anything else (l > r, or no belief entry) | 0 | 0% |

The two together account for every one of the 330. Only 149 of the 301 library tied ticks newly crossed (the rest were
already <= 0.5, or the tie is not at the shown rank-1), so the +0.248 is not "ties only" and my earlier P4 reasoning
(ties as the only route to 0.5) was wrong about the mechanism, although the library confident-and-wrong fall (0.133) stayed
under the 0.227 bound I had derived from it. **The l < r group is intended behaviour:** the shown order comes from `_merge`
(model / retrieval order, here the mock re-rank) while belief orders by log-odds, and the shown value is the margin against the
best rival, so when the displayed ranking disagrees with belief the display falls below 0.5 (e.g. dev_A01 tick 78: RCA-01 at
log-odds 0.41 vs a rival at 0.75 shows 0.415; the decision value is 0.60). On a real model that reorders, this group may be
larger or smaller; not measured.

Verification of this step:
| step | result |
|---|---|
| ran the module | full dev run with (c): 36 episodes, 5,850 assessments; saturation tool on it, and again with `--decision-conf`, which reproduces the baseline table exactly (ECE 0.1305, 233, 0.176, ...) |
| self-tests | pytest 61 passed / 0 failed; `test_checks` 20/0, `test_envelope_logging` 16/0, `test_orchestrator` 22/0 |
| independent re-derivation | library ECE (group): tool 0.0963 vs recomputed by separate code from the raw `belief_ranking` log-odds (shown value recomputed per tick, groups from `case_groups.json`) 0.0963; rel. diff 0.04% (the tool's value is printed to 4 d.p.). The recomputed shown value differs from the emitted `confidence_shown` on **0 of 1,327** ticks; confident-and-wrong 57 vs 57 |
| mutation check | (M1) verifier trigger reads the shown value: caught by `test_verifier_does_not_fire_on_a_tie_the_display_shows_as_half`. (M2) the `min` safeguard dropped: 5 tests fail (never-raises, tie-below-half, both-at-floor, retired-not-rival, shown<=decision). (M3) a hypothesis counted as its own rival: 3 tests fail (single-hypothesis unchanged, never-raises, retired-not-rival). `__pycache__` cleared between runs |

## Phase-reviewer findings and what was done
Independent review (`phase-reviewer`, range `2212031..HEAD`). No blockers. It re-ran the suite (61 passed at the time),
re-derived the dev decision-identity claim (0 differences over 5,850 assessments), the ECE / confident-and-wrong figures
(0.1305 -> 0.0963, 233 -> 57 of 1,327) and the 330 crossings (149 ties + 181 l < r + 0 other) by separate code, and ran 11
mutations in a scratch copy. Four survived; each fix is its own commit:

| finding | disposition |
|---|---|
| 1. The "nothing that decides reads `confidence_shown`" rule was tested only by one verifier test that skips without dev data; a verifier trigger reading the shown value and a `_wm_summary` change both survived | **Fixed** (`dce395c`): data-free structural test (only `world_model.py` / `orchestrator.py` mention it), an ordering test (the shown value is computed after `ver.should_run`, `ver.apply`, `gate.approve`), a prompt-summary test, a verifier-band test. Both mutations now caught with dev data absent. Still true: the grep and these tests cover decision paths by reference, so a new consumer in a new file is caught only if it names the field or function |
| 2. The evaluator change that moves held-out `low_conf_rate` 0.245 -> 0.945 had no test (a mutation reading only `confidence` survived) | **Fixed** (`ca2814e`): shown value, fallback to the decision value on old runs, `low_conf_rate_decision`, `<= 0.5` boundary; mutation and boundary mutation caught |
| 3. `belief_saturation`: `>` vs `>=` at exactly 0.5 and group-level vs case-level correctness untested | **Fixed** (`fc5157f`): both caught by new tests; stale docstring corrected |
| 4. Stale report sentence ("behaviour unchanged / reporting reproduces v2") and duplicated Status | **Fixed** (this commit) |
| 5. 170 of 1,982 dev ticks have a shown rank-1 not in `belief_ranking` (model-only / dropped) | **Verified by me and documented** (What I could not verify) |
| 6. Cause-first matching of the belief entry | **Documented**; not changed (safe on the current library) |
| 7. Misnamed test `test_retired_hypotheses_are_not_rivals` | **Renamed** to `test_only_the_hypotheses_passed_in_are_rivals` (`dce395c`) |
| 8. `band_calibration.py` hard-codes `sample_period_s=5.0` and reads the private `win._t` | **Noted, not changed**: it matches the config value and the harness today; a change in the sample period would need the script updated |
| (mine, found while fixing) `trajectories()` printed the decision confidence under a "shown" header, so my P1 check read the wrong field | **Fixed** (`6ac94ff`), P1 re-checked and corrected above |

Could not be verified by the reviewer: that the 30 reporting episodes still reproduce v2 (it was not allowed to run them);
"first seed, no retries" for 35 of 36 dev episodes (seeds are not stored in `ground_truth.json`; `dev_C01` regenerated
byte-identical from its spec seed); that the human approved the `min` safeguard and set `TARGET_FLAT` (asserted in the report;
both are recorded as human decisions in this conversation).

## Step 6: the one-shot reporting run: prediction (written and committed BEFORE the run)
**Prediction.** Running the 30 reporting episodes once at HEAD (mock, `--out results/v3_report`) reproduces
`results/baselines/single_v2_summary.json` on **every key except** the ones listed here. Any other differing key is a **failure**:
stop, report it, write no baseline.

Comparison (fixed now): flatten `summary` and `per_episode` of v2 and of the new run to key paths and compare values exactly.
Allowed differences, and nothing else:
1. Any path ending in `low_conf_rate` (held-out only: `Q2_heldout`, `Q2_by_family.C.heldout`, and `T2_root_cause` of the four
   held-out episodes C01, C03, C05, C06). Predicted direction: **up** from the v2 held-out value 0.225.
2. Any path ending in `low_conf_rate_decision` (a **new key**; its value on the held-out rows must equal the v2 `low_conf_rate`
   exactly, which is a second check).
3. `per_episode[*].S1_tick_latency_ms_p50` / `_p95`: wall-clock, never reproducible (the dev reruns differ in them too).
4. Run metadata (`commit`, `state`, `note`, ...), which differ by design. ECE and saturation are **not** part of the evaluator
   summary: they are computed by `bench/belief_saturation.py` from the run log and stored in the v3 file under a separate
   `belief_saturation` block (new).

Also predicted, from the dev result: reporting-set library confident-and-wrong and library ECE (group) both fall against the
same run read with `--decision-conf`; held-out confident-and-wrong falls. These are reported, not gating (one held-out case;
the decision on (c) was already taken on dev).

## Step 6 results: the one-shot reporting run (HEAD `cf5203f`, clean tree, mock, 30 episodes, run once)
**The prediction held.** 1,454 key paths compared against `single_v2_summary.json`, 1,384 identical, **0 unexpected
differences**. The differences are exactly the pre-registered ones:
- `low_conf_rate` (held-out): overall 0.225 -> **0.972**; C01 0.256 -> 1.000, C03 0.436 -> 0.945, C05 0.179 -> 1.000, C06 0.029 -> 0.943.
- `low_conf_rate_decision` (new): equals the v2 `low_conf_rate` exactly on all six held-out rows (0.225 overall; 0.256, 0.436, 0.179, 0.029).
- wall-clock S1 latency keys (35 paths).
Direction check P5 (`low_conf_rate` up) held. Reporting-set library confident-and-wrong and ECE both fell, held-out too (below).
Written: `results/baselines/single_v3_summary.json` (reporting `summary`/`per_episode`, plus `belief_saturation` and a `dev` block).
Mock numbers only; not agent results.

### Before / after (mock; reporting v2 -> v3, dev before -> after (c))
| metric | reporting v2 | reporting v3 | dev before (main) | dev after (c) |
|---|---|---|---|---|
| Q1 macro-F1 | 0.743 | 0.743 | 0.782 | 0.782 |
| Q2 top-1 (legacy) | 0.334 | 0.334 | 0.332 | 0.332 |
| Q2 top-3 (legacy) | 0.523 | 0.523 | 0.453 | 0.453 |
| Q3 faithfulness | 1.000 | 1.000 | 1.000 | 1.000 |
| Q4 precision | 0.380 | 0.380 | 0.357 | 0.357 |
| Q4 recall | 0.926 | 0.926 | 0.882 | 0.882 |
| Q5 FP/h (mean of per-episode rates) | 0.910 | 0.910 | 0.980 | 0.980 |
| Q6 lead time min | 31.500 | 31.500 | 32.400 | 32.400 |
| S4 LLM invocation | 0.450 | 0.450 | 0.337 | 0.337 |
| S7 deadline miss | 0.000 | 0.000 | 0.000 | 0.000 |
| library top-1 | 0.436 | 0.436 | 0.434 | 0.434 |
| library top-3 | 0.684 | 0.684 | 0.593 | 0.593 |
| library group top-1 | 0.547 | 0.547 | 0.489 | 0.489 |
| library sep_named | 0.503 | 0.503 | 0.475 | 0.475 |
| library belief top-1 tie-fair | 0.438 | 0.438 | 0.375 | 0.375 |
| library belief top-3 | 0.654 | 0.654 | 0.570 | 0.570 |
| library belief group | 0.574 | 0.574 | 0.502 | 0.502 |
| library belief tie rate | 0.283 | 0.283 | 0.270 | 0.270 |
| held-out group top-1 | 0.487 | 0.487 | 0.474 | 0.474 |
| held-out belief group | 0.545 | 0.545 | 0.709 | 0.709 |
| held-out belief tie rate | 0.765 | 0.765 | 0.838 | 0.838 |
| held-out low_conf_rate (shown) | 0.225 | 0.972 | 0.245 | 0.945 |
| held-out low_conf_rate_decision | n/a | 0.225 | n/a | 0.245 |

| ECE / confident-and-wrong | reporting, decision (v2 view) | reporting, shown (v3) | dev, decision | dev, shown |
|---|---|---|---|---|
| library ECE (group) | 0.194 | 0.129 | 0.131 | 0.096 |
| library confident-and-wrong ticks | 223 | 80 | 233 | 57 |
| library confident-and-wrong share | 0.155 | 0.055 | 0.176 | 0.043 |
| library low-confidence share (<= 0.5) | 0.449 | 0.627 | 0.335 | 0.583 |
| held-out ECE (group) | 0.400 | 0.021 | 0.417 | 0.051 |
| held-out confident-and-wrong ticks | 101 | 5 | 109 | 15 |
| held-out confident-and-wrong share | 0.404 | 0.020 | 0.399 | 0.055 |
| held-out low-confidence share | 0.228 | 0.980 | 0.260 | 0.945 |
| scored ticks library / held-out | 1442 / 250 | same | 1327 / 273 | same |

Reading the tables:
- Everything that decides is identical v2 -> v3 and before -> after (the first 21 rows); only the shown-confidence rows move.
- **Library:** ECE (group) 0.194 -> 0.129 (reporting) and 0.131 -> 0.096 (dev); confident-and-wrong 223 -> 80 and 233 -> 57.
- **Held-out (one case, RCA-06; reported, not gating):** confident-and-wrong 101 -> 5 and 109 -> 15; ECE 0.400 -> 0.021 and 0.417 -> 0.051.
  The held-out display now reads low on 98% / 94.5% of ticks, which is what a case the library has never seen should look like;
  it also means the held-out figures reflect the tie structure of RCA-06 more than any general calibration.
- **Library bins are still not calibrated** (reporting, shown): 0-0.2: n 485, mean conf 0.094, group-correct 0.318;
  0.2-0.4: 206, 0.279, 0.524; 0.4-0.6: 270, 0.503, 0.519; 0.6-0.8: 172, 0.684, 0.802; 0.8-1.0: 309, 0.930, 0.922. The top bins are
  now well calibrated, and the low bins are under-confident (group-correct well above mean confidence). **Corrected at close-out:**
  I first wrote that this was "consistent with the group-blind tie handling". That was wrong, because a tie shows exactly 0.5 and
  the lowest bin contains no ties. The measured cause is below. The 0.2-0.4 bin is unexplained (not split). Dev shows the same
  direction with a smaller gap (lowest bin 0.085 vs 0.195).
- `low_conf_rate` (evaluator, the episode-mean of per-episode rates) is 0.972 and the pooled tick share from the saturation tool is
  0.980 for the same held-out ticks: different aggregation, same data.
- Dev is lower than reporting on the library belief rows in both columns (0.375 vs 0.438 tie-fair top-1): unexplained
  (hypothesis: A2 was selected on the reporting set). Dev A-family lead times are not compared with reporting.

### Low-bin under-confidence: measured cause (close-out; reporting run, library, read-only)
Method: for each of the 485 lowest-bin ticks (shown rank-1 < 0.2), recompute `shown = min(c, sigmoid(l - r))` from the logged
`belief_ranking` (`l` is the shown rank-1's own log-odds and `r` is the best live rival's), then record which term binds. The
recompute matches the logged `confidence_shown` on all 485 ticks (0 mismatches). The bin itself reproduces exactly (n 485, mean
0.094, group-correct 0.318). It contains **0 ties**. Script: a scratch file, not committed (no code changes).

| lowest-bin ticks (reporting, library) | n | mean shown | group-correct | share of the gap* |
|---|---|---|---|---|
| (a) min binds on the rank-1's own confidence `c`, and it is below a rival (l < r) | 71 | 0.160 | 0.493 | 22% |
| (a) min binds on `c`, and it is the belief leader (l >= r) | 8 | 0.158 | 0.250 | 1% |
| (b) negative margin binds (l < r and sigmoid(l - r) < c; the _merge/re-rank disagreement) | **300** | **0.056** | **0.340** | **79%** |
| model-only (no belief entry, so shown = c) | 106 | 0.153 | 0.142 | -1% |
| all | 485 | 0.094 | 0.318 | 100% |

*Gap = sum over ticks of (group-correct − shown) = 108.2 = 485 × 0.223 (0.318 − 0.094 from rounded bin figures). Each row's share is
n × (group-correct − mean shown) / 108.2, computed per tick (unrounded).

(b) split by the belief leader's group, i.e. the rival that outranks the shown rank-1:

| (b) ticks | n | mean shown | shown rank-1 group-correct | belief leader group-correct | mean r − l | share of the gap |
|---|---|---|---|---|---|---|
| leader in a **different** group | 276 | 0.057 | 0.286 | 0.409 | 3.56 | 58% |
| leader in the **same** group (group-blind margin) | 24 | 0.047 | 0.958 | 0.958 | 3.35 | 20% |

**Cause (measured):** most of the gap comes from (b), the negative margin. On 300 ticks the shown rank-1 sits on average 3.5
log-odds below a belief rival, so the margin shows it at about 0.06. Yet it is in the true group 34% of the time. In the larger
part of (b), where the leader is in another group, the belief leader is right only 41% of the time. A 3.56 log-odds lead
(sigmoid 0.97) for a leader that is right 41% of the time is belief over-confidence carried into the display through the margin.
Group-blindness accounts for only the 24 same-group ticks (20% of the gap), and it acts through a negative margin, not through
ties. The rest of the gap (22%) is (a): the rank-1's own decision confidence is low but it is right 49% of the time.
**Unexplained:** why belief log-odds gaps are this wide relative to how often the belief leader is right. I believe it is the
known saturation and clamp behaviour, but that is untested. On dev the same split gives (b) 281 of 385 (shown 0.059, group-correct
0.164), with only 7 same-group ticks.

## Advisor questions (consolidated; new items first)
No single list existed (questions were scattered across the stage reports). New this session:
1. **C04 ticks 50-57 (physics).** In `ep_C04_feeder_trip_caught` (truth RCA-14; fuel capped at tick 30, restarted at tick 70) the
   bed temperature **rises 0.8 to 3.5 degC/min while steam falls (-1.15 to -0.08 t/h/min) and drum pressure falls FAST
   (-0.92 to -0.07 kg/cm2/min)** during a fuel-shortage fault. The load term (2.43 x steam slope) is negative there (-2.8 to -0.2), so
   load does not explain it. CLAUDE.md's known-correct direction is that capped fuel makes the bed cool. Is a bed rising against
   falling steam and pressure plausible in an AFBC during a feeder trip, or is this a simulator artifact (the known
   bed-noise calibration gap)? If it is physical, RCA-14's `bed UP MED` contradicting triple may be wrong for this plant. (Phase 0a
   open item 4, Check 2.)
2. **Case-signature band calibration changes case data.** The p85 edges separate families C and D at tag level, but the matcher
   collapses (library tie-fair belief top-1 0.375 -> 0.098 on dev; the empty-signature cases take 70% of belief rank-1), which points at the
   SLOW/MED/FAST labels in `case_library.json`. **May we relabel case bands from simulated dev episodes, or must they stay as
   translated from the RCA text?** (A relabel would make the labels describe our simulator, not the PDF; it needs a
   pre-registered rule, and it must be tested together with the edges as one unit.)

Carried from earlier reports (still open, not repeated in full): the drum GA / level-transmitter-span and K versus RCA Case 1 question
(`stage3_submodel1.md`, carried in `stage3_submodel3.md`); family E needs a cumulative drift detector (`stage4_regeneration.md`,
disagreement 1); ratify the episode-to-case map, the held-out set and the recommended metric change
(`stage5_case_library.md`); the offline coverage tool versus the live harness disagreement (`stage6_band_edges.md`, Unresolved 2);
the bed FAST-DOWN share in the A episodes, wander or physics (`phase0a_single_agent_fixes.md`, open item 3).

## Disagreements recorded, not resolved
- **Instruction vs history (item 2).** "Restore load normalisation" vs a signature that never had it. Resolved by your
  decision to skip, not by me.
- **Pooled vs per-episode FLAT.** The criterion is pooled. Per-episode FLAT at the p85 edges ranges 0.52–1.00 (load swings
  differ), so a single episode can read far more movement than 15%. Not investigated.
- **Dev below reporting** on library belief metrics (0.375 vs 0.438 tie-fair top-1; belief group 0.502 vs 0.574).
  **Unexplained (hypothesis: A2 was selected on the reporting set).** Not tested.

## Blocked / needs a decision
- None. Human choice made: (c) only, display-only, S4 must not move (see Step 5).

## Open list (added)
- **Case-signature band calibration, tested together with the edges as one unit.** Tag-level separation is good under the
  p85 edges and the matcher still collapses, so edges and case band labels must be judged together. It touches
  `data/kb/case_library.json`, so it needs a **pre-registered rule and advisor sign-off**. Not done in this session.

- **Overconfidence fix (a), per-tick log-odds decay: DROPPED** (human decision). Recorded: a decay mild enough to be
  derived (half-life = the 20-tick signature window) cannot stop a +1.2/tick climb from reaching the clamp in 4-5 ticks.
- **Lone-leader climb: rank-1 far ahead of its best rival, so the margin cannot pull the display down (e.g. dev_N05 RCA-07).**
  `dev_N05_normal`, RCA-07: +0.90, +2.00, +3.09, +4.00 over ticks 11-14 (shown 0.711, 0.881, 0.950, 0.980) on a healthy plant.
  4 to 6 hypotheses are live, but every rival sits below 0 (ticks 11-12) or just above it (RCA-15 +0.16 / +0.12 from tick 13), so (c) barely moves it.
  Open. (Renamed at close-out from "single-hypothesis fast climb", which was inaccurate.)
- **Fix (b), tie-break by this tick's retrieval score: DEFERRED to Session 2** (human decision). It changes rank-1 order, which
  is the open "does belief decide, or only break ties" question, to be decided with real-model numbers.
- **Group-aware margin.** (c) is pairwise and group-blind: 27.6% of library tied ticks are look-alikes inside the true group and now
  show 0.5. In the lowest bin, group-blindness accounts for only 20% of the under-confidence gap (24 same-group negative-margin
  ticks); 58% comes from negative margins against a belief leader in another group (close-out split). A group-aware margin
  would not address the larger part. Not built.
- **Whether ambiguity (a tie, a small margin) should trigger the verifier.** Separate later change; (c) leaves the
  trigger on the decision confidence.

## What I could not verify
- Why belief's lead over a disagreeing shown rank-1 is so wide (mean 3.56 log-odds) when the belief leader is in the true
  group only 41% of the time (lowest-bin split, close-out). Saturation/clamp is the obvious candidate; not tested. The 0.2-0.4
  bin's under-confidence (0.279 vs 0.524) was not split.
- Why dev library belief is lower than reporting (unexplained; hypothesis: A2 selected on the reporting set).
- (c) on a real model: `shown` is matched to belief by cause (then case_ref), not by rank, so a reordering model changes which
  hypothesis is judged against which rival. On the mock this already happens: **170 of 1,982 dev ticks (8.6%)** with a belief
  ranking have a shown rank-1 whose case is not in `belief_ranking` (a model-only idea belief has dropped, e.g. dev_A01 ticks 50-51,
  RCA-01); those keep the undiscounted decision confidence (the model-only rule) and cannot cross to <= 0.5. So the mock does
  **not** simply reproduce belief order. Real-model behaviour not exercised.
- Matching by `cause` first is safe today (no two of the 13 library cases share a cause string, checked by the reviewer) but
  would mis-attribute silently if two cases ever did.
- Everything beyond mock: a reasoning model may not depend on signature density the way the mock re-rank does.
- A01, A04 and A06 never reach TRIP_IMMINENT on their dev seeds (reporting twins do: 29.2, 59.8, 30.2 min). Time-to-trip
  differs for A02/A03/D01/D03 too. Dev A-family lead times are reported separately and are not compared with reporting.
