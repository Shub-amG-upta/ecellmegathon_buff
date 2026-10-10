# Stage 5 / real RCA case library — report

## Status

DONE — the 10 invented cases in `data/kb/case_library.json` are replaced by
13 of the 18 structured RCA cases from `docs/Boiler_Failure_Case_Studies_RCA.pdf`
(AFBC captive boiler, the correct asset class). 5 cases are held out in
`data/kb/holdout.json`, absent from the library. Episode ground truth
(`root_cause_id` **and** `correct_action_ids`) is re-pointed onto the real cases
via `data/kb/episode_case_map.json` (PROVISIONAL — advisor must ratify the map
**and** the hold-out split together). Known bugs 3 (`records.json` never reached
the agent) and 4 (`query.txt` never read) are fixed.

**Stage-5-review corrections applied (this pass):**
- **RCA-01 signature was wrong, now fixed.** The PDF (Case 1 §2.1/§3) says feed
  flow reads *4–5 TPH less than steam* and *"no further increase in feed flow"*
  — feed is **stuck below steam, not falling**. Changed `feed_water_flow`
  `["DOWN","MED"]` → `["FLAT","-"]`. Against the deployed 13-case library RCA-01
  now retrieves better: under the real `CaseLibrary.match` (contra +
  provenance) on the aggregated post-onset signature, **A05 rank 1, A01 rank 3**;
  A02 and A06 stay rank 7–8. The exact count in top-3 is
  aggregation-method-sensitive (a plain weighted-Jaccard on the same aggregate
  puts 3/4 in top-3), so the honest claim is **it improves rank and reaches
  top-3 for 2 of 4, not that it fixes retrieval**. The `["DOWN","MED"]` triple
  was pure denominator inflation — a triple the simulator never produces.
- **The FLAT-weight (0.35) is not the cause.** Sweeping it 0.35 → 0.5 → 0.7 →
  1.0: 0.5 marginally helps (A06 rank 2→1), 1.0 *hurts* (A02 rank 4) because the
  case's unmatched FLAT triples then inflate the denominator at full weight.
  Left at 0.35. The real limiter is the simulator's per-tick noise adding ~8
  spurious movement triples to every episode signature, capping any weighted-
  Jaccard near 0.1–0.25.
- **T4 fixed the same way as T2.** `correct_action_ids` were stale (invented-
  case action sets). Re-pointed by mechanism to the mapped case's `actions`;
  `[]` for the 7 not-applicable episodes. Mock `Q4_action_recall`
  **0.757 → 0.926**, precision 0.203 → 0.38, over `Q4_n_episodes_scored: 17`.

**Headline finding (item 1): the simulator does not reproduce most of the
plant's documented failure modes.** Of the 13 library cases, **3 are clearly
reproduced** by an episode with a usable signature (RCA-11 water-wall leak,
RCA-07 bed over-temperature, RCA-16 BFP NPSH loss), **RCA-01** (FCV seizure) is
**borderline** after the signature fix (2 of 4 FCV episodes top-3 under the real
matcher), **RCA-14** (feeder loss) is weak, and **7 are exercised by no episode
at all** (3 of those have signatures the six tags structurally cannot carry).
The episodes were built around invented failure modes; the plant's real ones are
largely different. **Not adjusted** — no simulator change was made; the RCA-01
signature edit corrects a transcription error against the PDF, it does not tune
to the simulator.

## Verification

| step | result |
|---|---|
| ran the module | `python run_demo.py --all --backend mock` → 30/30 episodes, no crash. Q1_macro_f1 **0.743** (unchanged), Q3_faithfulness **1.0** (unchanged), Q5_fp/h **0.91** (unchanged), S7_deadline_miss **0.0** (unchanged). Q2 `top1 0.334 / top3 0.505`, `Q2_n_episodes_scored: 17`. Q4 `precision 0.38 / recall 0.926`, `Q4_n_episodes_scored: 17`. Not-applicable (both): `[A04, B04, D02, D04, E01, E02, E03]`. Mock — not an agent result. |
| regenerated episodes | `python -m data.generator.episode_build --out data/episodes` → 30/30 written. `python -m bench.validate_data` → **30/30 PASS** (1 benign WARN, `ep_E03`, pre-existing). |
| self-tests — L1 | `python bench/test_checks.py` → **18 / 18**. |
| self-tests — sim | `python3 -m data.generator.sim` → exit 0; 11 mutations still caught, 8 direction checks OK (Stage 5 touched no simulator code). |
| key-note integrity | all 15 tier-B/C fault episodes retain their distinct key note after `KEY_NOTES` was re-keyed from `root_cause_id` to the episode-name stem (A04→strainer, B04→CBD, D02→PA damper verified individually). |
| records / query wiring | `Diagnostician.build_prompt` renders `OPERATOR QUESTION` and `PLANT RECORDS` (coal lab report, maintenance history, boiler-water conductivity trend) — verified on `ep_C05`: conductivity summarised `833 uS/cm RISING` at +1 h, time-filtered to `now_s`. |
| independent re-derivation | see below — two routes to "how many library cases does the simulator reproduce". |
| mutation check | see below. |

### Independent re-derivation — library-case coverage

Two independent routes to *"which of the 13 library cases does a regenerated
episode actually reproduce"*:

- **Route A — offline weighted-Jaccard against the deployed 13-case library.**
  Aggregate each episode's L1 `build_signature` triples over its post-onset
  window (persistence ≥ 0.15 of ticks), rank the 13 **library** cases (the 5
  held-out cases are not in the matcher) with the `CaseLibrary` weighted-Jaccard.
- **Route B — live harness, mock backend, per-episode T2 top-1** (a different
  code path: per-tick `CaseLibrary.match` on the live signature, k = 4, mock
  re-rank).

| library case | reproduced? | Route A rank (episodes) | Route B top-1 | agreement |
|---|---|---|---|---|
| RCA-07 bed over-temp | **yes** | 1 (D01, D03) | 0.77–0.88 | both: reproduced |
| RCA-11 water-wall leak | **yes** | 1 (B01, B02), 2–3 (B03, B05) | 0.40–0.68 | both: reproduced |
| RCA-16 BFP NPSH loss | partial | 1 (A03) | 0.40 | both: partial |
| RCA-01 FCV seizure (after fix) | borderline | A05 rank 1, A01 rank 3, A02 rank 8, A06 rank 7 (real match, aggregated sig) | 0.13–0.23 | improved from old 6/13/8/14; still noise-limited |
| RCA-14 feeder loss | weak | 1 (C02, tied w/ RCA-18) | 0.30 | both: weak |
| RCA-03/04/05/09/10/13/15 | n/a — no episode | — | — | — |

Routes A and B agree on every yes/partial/no verdict. RCA-01 after the
signature fix is **borderline**: real `CaseLibrary.match` on the aggregated
signature puts A05 at rank 1 and A01 at rank 3 but A02/A06 at rank 7–8 (a plain
weighted-Jaccard variant is more generous, 3/4 in top-3 — the number is
aggregation-sensitive); the live per-tick path (Route B) stays noise-limited.
The defensible headline count is **3 library cases clearly reproduced**
(RCA-07, RCA-11, RCA-16), **RCA-01 borderline**, **RCA-14 weak**.

### Mutation check

1. **`data/kb/episode_case_map.json`** — mis-point `ep_B01_tube_leak` from
   `RCA-11` to `RCA-07`, regenerate, run. T2 top-1 **0.68 → 0.109**, top-3
   **0.953 → 0.156**. Caught by the harness T2. Restored → 0.68 / 0.953.
2. **`data/kb/case_library.json`** — flip `RCA-11` `bed_temp_avg` from
   `["DOWN","MED"]` to `["UP","MED"]`, re-run Route A. `ep_B03_tube_leak_slow`
   rank **3 → 4** (raw 0.21 → 0.14); `ep_B02` raw 0.26 → 0.33 (now matches the
   episode's spurious bed-UP noise instead). Caught, but weakly.

   **Follow-up the review asked for — does the leak-quench (`bed_temp_avg DOWN`)
   signal survive the matcher?** Contribution breakdown of RCA-11 vs each B
   episode: `bed_temp_avg|DOWN|MED` **matches** for B01 (ep weight 0.36),
   B03 (0.66), B05 (0.46) — the Stage-3/4 leak-quench correction *does* produce a
   matchable `bed|DOWN` triple for the slower leaks. It does **not** match B02
   (the fast leak — the bed drop is too fast/noisy to land in the MED band).
   RCA-11's retrieval is carried by, in order: `water_balance|SURPLUS|MED`
   (matches all 4, ep weight 0.66–1.0), `drum_pressure|DOWN|SLOW` (all 4,
   0.56–0.92), `bed_temp_avg|DOWN|MED` (3 of 4), `drum_level|FLAT|-` (weak).
   RCA-11's headline triple `feed_water_flow|UP|MED` matches **only B02** — the
   simulator's feed-flow response to a leak is otherwise too weak/noisy. So the
   weak mutation catch is because flipping `bed` to UP lets it match the noise,
   not because `bed|DOWN` fails to match; the leak-quench signal is present, it
   is just the third-strongest of four contributors and absent on the fast leak.

## Direction checks

The signatures are transcribed from the PDF's stated trajectories, not derived,
so "direction" here is *does the case signature match the mechanism the PDF
describes*. One row per library case; PROVISIONAL where the six-tag projection
drops the discriminating signal (recorded in `discriminating_evidence`).

| case | PDF mechanism | six-tag signature direction | OK / note |
|---|---|---|---|
| RCA-01 | FCV seizure → feed *stuck below* steam, heat side untouched | level DOWN, **feed FLAT**, bed/steam/pressure FLAT, water_balance DEFICIT | OK — `feed FLAT` corrected this pass (PDF: "no further increase in feed flow"); it is also the six-tag separator from RCA-16 |
| RCA-03 | HP-heater loss → firing up → superheat up | ms_temp UP, steam/bed FLAT, pressure DOWN slow | OK; spray-flow-vs-demand discriminator is not a tag |
| RCA-04 | passing spray valve + load step → wet steam | ms_temp DOWN fast, steam UP, level UP (swell) | OK; spray-flow-at-zero-demand is not a tag |
| RCA-05 | load rejection + relief fail-to-open | pressure UP fast, steam DOWN fast, level UP | OK; PRDS demand-vs-position is not a tag |
| RCA-07 | high-CV coal + lost ash recirc → bed runaway | bed UP fast, ms UP, pressure UP slow, HEAT_ACCUMULATING | OK |
| RCA-09 | deflagration after defeated purge | all FLAT | PROVISIONAL — entire signature is furnace-pressure trace + DCS log |
| RCA-10 | light-up flame failure | all FLAT | PROVISIONAL — light-up phase, not a running-plant signature |
| RCA-11 | fireside erosion → tube split | feed UP, level FLAT, bed DOWN, pressure DOWN slow, water_balance SURPLUS | OK |
| RCA-13 | APH cold-end corrosion → air-to-gas leak | bed UP slow (weeks), rest FLAT | PROVISIONAL — real signal is ESP-inlet O2 UP + APH-outlet gas temp DOWN |
| RCA-14 | MCC single-phasing → all feeders trip | bed/pressure/ms DOWN fast, steam DOWN, HEAT_SHORT | OK |
| RCA-15 | throttled PA branch damper → back-fire | all FLAT | PROVISIONAL — single-compartment, six-tag averages do not move |
| RCA-16 | low deaerator → BFP cavitation | feed DOWN fast, level DOWN fast, pressure DOWN slow, water_balance DEFICIT | OK |
| RCA-18 | PA fan loss + hidden standby fail | bed/pressure/ms DOWN fast, steam DOWN, HEAT_SHORT | OK |

## Item 1 — coverage, both directions (the report headline)

### Direction A — which regenerated episodes reproduce a real case

| episode(s) | mapped case | mapping | reproduces the case signature? |
|---|---|---|---|
| A01, A02, A05, A06 (FCV seizure) | RCA-01 | library_exact | **Borderline (after the signature fix).** `feed_water_flow` FLAT is now correct per the PDF. Real `CaseLibrary.match` on the aggregated signature: A05 rank 1, A01 rank 3, A02 rank 8, A06 rank 7 (improved from old 6/13/8/14). A01/A06 also attract RCA-16 (same cluster) and the all-FLAT RCA-15. Live per-tick top-1 0.13–0.23 (noise floor). |
| A03 (BFP suction) | RCA-16 | library_exact | Partial — the water-deficit shape matches (rank 1 vs the 13-case library, live top-1 0.40); the deaerator/vibration discriminators are non-tags. |
| A04 (feed strainer choke) | — (null) | not_applicable | No standalone real case (Case 1 H2 / Case 16 contributory only). |
| B01, B02, B03, B05 (tube leak) | RCA-11 | library_exact | **Yes.** `water_balance SURPLUS` (0.66–1.0 of post-onset ticks) + `drum_pressure DOWN SLOW` + `bed DOWN MED` (3/4). Rank 1–3, live top-1 0.40–0.68. |
| B04 (CBD left open) | — (null) | not_applicable | Case 1 H3, not a standalone case; on six tags **identical to RCA-11** (see item 2). |
| C01, C03, C05, C06 (wet / low-CV coal) | RCA-06 | **held-out** | Weak signature produced (`drum_pressure DOWN` + faint `HEAT_SHORT`); RCA-06 is held out → these are the generalisation test, top-1 = 0.00. RCA-06 shares the heat-loss cluster with library case RCA-14 — see the hold-out reframe below. |
| C02, C04 (feeder trip) | RCA-14 | library_near | Weak-moderate — total-heat-loss shape matches (top-1 0.30) but is indistinguishable from RCA-18 (see item 2). |
| D01, D03 (high-CV coal) | RCA-07 | library_exact | **Yes.** `bed UP FAST` (0.68–0.78) + `ms UP` + `HEAT_ACCUMULATING`. Rank 1, live top-1 0.77–0.88. |
| D02, D04 (low primary air) | — (null) | not_applicable | Low-PA-raising-bed is Case 7 H2, not a standalone case (Case 8 is the opposite polarity). Signature *does* resemble RCA-07 — mapping to RCA-07 would score-game, so null. |
| E01, E02, E03 (fouling drift) | — (null) | not_applicable | Progressive evaporator fouling has no standalone real case, and the regenerated episode's signature matches **nothing** (rank 9–99 against every case) — consistent with the Stage-4 finding that family E is masked by the plant's own low-frequency wander. |

**Episodes that reproduce a library case with a usable signature: 8 of 24**
(4 × RCA-11, 2 × RCA-07, A03→RCA-16, and A05→RCA-01 rank 1) plus A02/A06→RCA-01
top-3 and C02/C04→RCA-14 weak → **12 of 24** map cleanly-enough to score in a
top-3 sense. 4 map to a held-out case by design (RCA-06). 7 are
`root_cause_id: null` — no real case for the mechanism.

### Direction B — which of the 13 library cases has a matching episode

| reproduced (episode exists, signature matches) | RCA-07, RCA-11, RCA-16 clearly; RCA-01 borderline (2/4 eps top-3 after fix); RCA-14 weak | **3 clear + 1 borderline + 1 weak of 13** |
|---|---|---|
| **no episode exercises it** | RCA-03, RCA-04, RCA-05, RCA-09, RCA-10, RCA-13, RCA-15 | **7 of 13** |

Of the 7 with no episode: **RCA-09, RCA-10, RCA-15** have signatures the six
tags **structurally cannot carry** (furnace pressure, flame scanner,
per-compartment PA flow); **RCA-03, RCA-04, RCA-05** are steam-side transients
no driver in the generator produces (HP-heater loss, passing spray valve, TG
load rejection); **RCA-13** needs flue-gas O2 / APH gas temperature.

### What this means

The 11 invented cases matched the old simulator *by construction*. The real 18
were written by engineers about a real plant, and **only 4 (+1 weak) of the 13
in the library correspond to something the simulator can produce**. The
generator's
fault set (FCV / strainer / tube-leak / CBD / wet-coal / feeder-trip / high-CV /
low-PA / fouling) overlaps the plant's documented failure families (water-side,
steam-side, bed, furnace-safety, pressure-parts, auxiliary) only at
tube-leak = Case 11, bed-over-temp = Case 7, BFP = Case 16, feeder-loss ≈ Case
14. This is a real finding about the benchmark's external validity and is **not
closed by tuning**. Options are in *Blocked / needs a decision*.

## Item 2 — cases undiscriminable on the six tags

Pairwise weighted-Jaccard of the *moving* (non-FLAT) signature triples, plus a
physical check. Four clusters collapse; the separating observation for each is a
non-tag quantity, recorded in the cases' `discriminating_evidence`.

### Cluster T — total heat loss (4-way): RCA-06 ≈ RCA-08 ≈ RCA-14 ≈ RCA-18

All four: `bed_temp_avg DOWN`, `drum_pressure DOWN`, `steam_flow DOWN`,
`ms_temperature DOWN`, `energy_balance HEAT_SHORT`. Identical `(tag,direction)`
set for all four; `RCA-14` vs `RCA-18` are **byte-identical** (moving-Jaccard
1.00). The only six-tag separator is the magnitude band (RCA-14/18 FAST,
RCA-06 MED, RCA-08 mixed), which the simulator's noisy per-tick slopes flip
constantly — `ep_C02` retrieves RCA-14 and RCA-18 at the *same* score (0.26).
Separators (all non-tags): simultaneous six-VFD phase-loss codes (14); motor
winding-temp-vs-current divergence + standby auto-start fail + PA header
collapse (18); compartment-selective bed fall + saturated feeder speed + wet
coal (06); economiser O2 rising > 6 % + high PA-to-coal ratio + unburnt coal in
the bed drain (08). RCA-14 and RCA-18 are both in the library — this is the pair
the agent will actually face.

### Cluster W — water-side deficit, level falling: RCA-01 ≈ RCA-16

After the RCA-01 signature fix the shared moving-triples are `drum_level DOWN
FAST` and `water_balance DEFICIT`. RCA-01 has `feed_water_flow FLAT` (stuck below
steam, does not respond); RCA-16 has `feed_water_flow DOWN` (pump failing to
deliver, unsteady) + `drum_pressure DOWN SLOW` — so the `feed_water_flow`
direction *is* a genuine six-tag separator between them (FLAT vs DOWN), when the
simulator produces it. It doesn't reliably: the FCV episodes show
`feed_water_flow FLAT` for A02/A05/A06 but nothing persistent for A01. RCA-16
still out-ranks RCA-01 on A01/A06 because RCA-16 carries more triples that match
the noise. Separators (non-tags): FCV position feedback vs demand (01);
deaerator level + BFP suction pressure / vibration / bearing temp, and the
standby pump replicating the fault on start (16). This is the "Case 1 vs …"
known pair.

**A band-specificity finding in the matcher.** `RCA-11`'s
`contradicting_signature` was `water_balance|DEFICIT|MED`, but the FCV episodes
produce `water_balance|DEFICIT|FAST` — a *different* triple, so the contradiction
did **not** fire and RCA-11 (a leak, opposite `water_balance` sign) still
out-ranked RCA-01 on a feed-deficit episode. Fixed by listing both bands
(`|MED` and `|FAST`) in the `contradicting_signature` of RCA-01, RCA-11 and
RCA-16. The underlying issue — `CaseLibrary.match` compares exact `(tag,dir,band)`
triples, so a band mismatch silently defeats a contradiction — is an L3-matcher
change, out of scope here; noted for the agent stage.

### Cluster L — water ingress / loss, level held: RCA-11 ≈ RCA-12 ≈ CBD-left-open

`(tag,dir)`-Jaccard ~0.7 (RCA-11 vs held-out RCA-12). All: `feed_water_flow UP`,
`drum_level FLAT`, `water_balance SURPLUS`. `ep_B04_cbd_left_open` produces this
signature and retrieves **RCA-11 at rank 1 (raw 0.30)** despite its true cause
being a blow-down valve, not a leak — the reason B04 is `root_cause_id: null`.
The only six-tag separator is `bed_temp_avg`: DOWN (local quench) for RCA-11,
FLAT for RCA-12 and for CBD — and the sim's bed noise makes even that
unreliable. Separators (non-tags): furnace hissing + unstable draught + local
bed drop (11); post-SH flue gas temp falling + ID fan current rising (12);
nothing distinctive for CBD except that conductivity falls for *all three*
(per Case 1 §6, a large CBD loss also lowers conductivity).

### Cluster S — swell: RCA-02 ≈ RCA-05 (partial)

`(tag,dir)`-Jaccard 0.60. Both: `drum_level UP`, `drum_pressure UP`,
`steam_flow DOWN`. Partially separable on six tags — RCA-02 has
`ms_temperature DOWN FAST` (carry-over) which RCA-05 lacks, and RCA-05's
`drum_pressure UP FAST` dominates. Full separation needs the TG-trip signal.
Both are cross-family (RCA-02 held out, RCA-05 in library).

### Cluster ∅ — invisible on six tags: RCA-09 ≈ RCA-10 ≈ RCA-15 ≈ RCA-17

All four have an **empty** moving-signature — nothing the six tags carry. They
are mutually indistinguishable *and* indistinguishable from NORMAL. This is by
construction: their signals are furnace pressure trace shape (09), flame
loss timing + scanner signal (10), single-branch PA flow at normal header
pressure (15), ID bearing-temp-vs-current divergence + furnace pressure after
the ID trip (17). RCA-09, RCA-10, RCA-15 are in the library with an all-FLAT
signature and a `discriminating_evidence` that says explicitly "not observable
in these six tags — go read X".

## Recommended metric change (for the advisor — NOT implemented)

**Nine of the thirteen library cases sit in an undiscriminable cluster** — RCA-01
& 16 (W), 05 (S), 09/10/15 (∅), 11 (L), 14 & 18 (T). Only RCA-03, 04, 07, 13
stand outside one on the six tags, and 3 of those 4 have no episode. Of the
episode-backed library cases, RCA-11 (L), RCA-14 (T) and RCA-01/16 (W) are all
in a cluster; only RCA-07 is not. So on six tags the agent can retrieve the
*cluster* but not always the *case*, and `Q2 top-1` is structurally capped well
below 1 — the wrong headline metric for this benchmark. Recommendation, with the
cluster table as the evidence:

| # | metric | definition | why |
|---|---|---|---|
| a | **cluster top-1** | is the true cause in the same undiscriminable cluster as the agent's rank-1 hypothesis? Cluster membership is a fixed lookup (the item-2 table). | credits the agent for getting to the right region on the evidence that exists; does not punish it for a distinction the six tags cannot support. |
| b | **discriminator accuracy** | did the agent name the observation that separates the cluster, scored (string / keyword match) against the mapped case's `discriminating_evidence`? | this is the *actual* skill the six-tag limit demands — "I cannot separate these two; go measure X". It is the T3-style mechanical check applied to the discriminator field. |
| c | **top-3 — keep as-is** | unchanged. | already forgiving of the cluster; the current `C3_top3_ge_0.85` constraint stays meaningful. |

`Q2 top-1` would be retained as a diagnostic but demoted from the headline; the
headline pair becomes **(cluster top-1, discriminator accuracy)**. The
`discriminating_evidence` field is already populated for all 13 cases with the
go-observe items, so (b) is implementable without new data.

### Hold-out reframe — describe it as what it actually is

The intent was "held-out cases test retrieval of an unseen mechanism". What the
split actually produces for the one episode-backed held-out case, **RCA-06**:
RCA-06 (coal hang-up) is in **cluster T** with the library case **RCA-14**
(feeder loss). So the four family-C episodes do not test "retrieve an unseen
case" — they test **honest uncertainty within a known cluster**: the agent has
RCA-14, the episode resembles it, and the correct behaviour is *"this looks like
RCA-14 (feeder loss) but I cannot separate a fuel hang-up from an electrical
feeder trip on these six tags — go check compartment-wise bed spread, feeder
speed vs response, and coal moisture"*. That is a **better** test than the one
intended — it exercises the discriminator-accuracy metric (b) directly — but it
must be reported as a within-cluster uncertainty test, not as unseen-case
retrieval. The other four held-out cases (RCA-02, 08, 12, 17) have no episode
and function only as negative controls (confirming they are not retrievable).

## Constants / structural values introduced

| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| library size | 13 | plan open-question 4 (advisor); "13 in / 5 out of 18" | fixed by the task | which cases retrieval can surface |
| hold-out set | RCA-02, 06, 08, 12, 17 | chosen to span 5 distinct PDF families (water-side / steam-side / bed / pressure-part / auxiliary) | PROVISIONAL — advisor | only RCA-06 is episode-backed, and it is in cluster T with library case RCA-14 → the 4 family-C episodes test within-cluster uncertainty, not unseen-case retrieval (see reframe) |
| `case_id` scheme | `RCA-NN` == PDF "Case N" | the PDF's own numbering | fixed | provenance traceability |
| conductivity-trend threshold | ±15 µS/cm | ASSUMED — a visible move against the ~814 baseline and the sub-model-3 no-fault drift (~1 %/day, `stage3_submodel3`) | 10–25 | the RISING/FALLING/flat label in the records view (leak-vs-blowdown cue) |
| operator-query variant used | line 2 of `query.txt` (the "specific" one) | `notes_gen.build_queries` structure | the 3 variants are a future sweep axis | which question frames the L4 prompt |
| RCA-01 `feed_water_flow` triple | `["FLAT","-"]` (was `["DOWN","MED"]`) | Case 1 §2.1/§3 — feed "reads 4–5 TPH less than steam", "no further increase" | n/a — direction is FLAT or it is wrong | RCA-01 retrieval; the Case 1 vs Case 16 six-tag separator |
| `contradicting_signature` band lists | `water_balance` DEFICIT/SURPLUS given at both `|MED` and `|FAST` for RCA-01, RCA-11, RCA-16 | the matcher compares exact triples; a `|FAST` residual bypassed a `|MED`-only contradiction | n/a | keeps the leak-vs-deficit contradiction firing regardless of residual band |

No simulator constant, L1 threshold, config value, episode severity or
ground-truth *format* was changed. The RCA-01 signature edit is a transcription
correction against the PDF, not a tune to the simulator (verified: raising the
FLAT weight, which would favour the corrected signature, does not improve its
rank — see Status).

## Numbers that changed

| quantity | old | new | why |
|---|---|---|---|
| `case_library.json` cases | 10 invented | 13 real (RCA-* = PDF Case *) | the stage |
| fault episodes re-pointed to a **library** case (`root_cause_id` + `correct_action_ids`) | — | 13 (A×5, B×4, C×2, D×2) | `episode_case_map.json` |
| fault episodes re-pointed to a **held-out** case | — | 4 (C01/C03/C05/C06 → RCA-06) | generalisation / within-cluster uncertainty test |
| fault episodes set `root_cause_id: null` + `correct_action_ids: []` (T2/T4/T6 not-applicable) | 0 | 7 (A04, B04, D02, D04, E01–E03) | no real case for the mechanism |
| mock `Q2_top1` / `Q2_top3` | 0.246 / 0.524 (old lib, all 24 scored) | **0.334 / 0.505** over **17** scored episodes | library swap + RCA-01 signature fix + not-applicable exclusion; mock, not an agent result |
| mock `Q4_action_precision` / `Q4_action_recall` | 0.202 / 0.875 (stale `correct_action_ids`, 24 scored) | **0.38 / 0.926** over **17** scored episodes | `correct_action_ids` re-pointed by mechanism to the mapped case's `actions` |
| RCA-01 rank for FCV episodes (real `CaseLibrary.match`, aggregated sig, 13-case library) | 6 / 13 / 8 / 14 (old `feed DOWN MED`) | **3 / 8 / 1 / 7** (A01/A02/A05/A06) — improved, still noise-limited | `feed_water_flow` triple corrected to FLAT |
| `Q1_macro_f1`, `Q3_faithfulness`, `Q5_fp/h`, `S7_deadline_miss` | 0.743 / 1.0 / 0.91 / 0.0 | **unchanged** | state / faithfulness / FP / deadlines do not depend on the library |

Downstream that depends on this: T2 (root cause), T4 (actions), T6 (lead time),
T7 (cross-episode memory — `repeat_of` chains still intact), the retrieval prompt
(now also carries records + operator question).

## Disagreements recorded, not resolved

1. **The simulator reproduces 4 (+1 weak) of 13 documented failure modes.**
   Stated in full above. Both re-derivation routes agree. **Not closed** — no
   simulator change was made. The generator was built around invented faults;
   the plant's real failures (steam-temperature transients, furnace safety, APH
   corrosion, single-compartment events) are mostly outside its scope.

2. **RCA-01 (FCV seizure): signature was wrong, now corrected; retrieval still
   limited by the noise floor.** The old `feed_water_flow ["DOWN","MED"]` was a
   transcription error — Case 1 §2.1/§3 describes feed *stuck below steam*
   ("no further increase in feed flow"), which is FLAT. Corrected: real
   `CaseLibrary.match` on the aggregated signature gives A05 rank 1, A01 rank 3,
   A02 rank 8, A06 rank 7 (was 6/13/8/14) — **improved, still only 2/4 in
   top-3**. A01's regenerated signature is degenerate (`drum_level` DOWN *and*
   UP both persistent, no `water_balance` triple); the live per-tick top-1 stays
   0.13–0.23 because ~8 spurious movement triples per tick cap the weighted-
   Jaccard. **The FLAT weight (0.35) is not the cause** — a 0.35→1.0 sweep does
   not improve RCA-01's rank (it worsens it, by inflating the denominator with
   unmatched case-FLAT triples). Left at 0.35.

3. **The hold-out split, described accurately.** Only **RCA-06** of the 5
   held-out cases has an episode. RCA-06 is in cluster T with library case
   RCA-14, so the four family-C episodes test **honest uncertainty within a
   known cluster** ("resembles RCA-14, cannot separate on six tags, go
   observe X"), not retrieval of an unseen mechanism. This is a *better* test
   than intended and it maps directly onto the recommended discriminator-
   accuracy metric — but it must be reported as within-cluster uncertainty. The
   other 4 held-out cases are negative controls only. Alternative considered and
   rejected: holding out an episode-backed case (RCA-07/11/14/16) guts a larger,
   cleaner part of the scoreable set.

4. **The signature mutation check is weak, and why.** Flipping RCA-11's `bed`
   direction moved one episode's rank by one place. Follow-up (Mutation check §2)
   confirmed the leak-quench `bed_temp_avg|DOWN|MED` **does** match 3 of 4 B
   episodes — the weak catch is because flipping to UP lets it match the
   simulator's spurious `bed|UP` noise, not because `bed|DOWN` fails. RCA-11
   retrieval is carried mostly by `water_balance|SURPLUS` and
   `drum_pressure|DOWN|SLOW`; `bed|DOWN` is the third contributor and is absent
   on the fast leak (B02). Related to cluster T: against the sim's noise floor
   any single case triple has limited discriminating power.

5. **`correct_action_ids` re-pointed by mechanism (this pass).** Set to the
   mapped case's `actions` for the 17 scoreable episodes, `[]` for the 7
   not-applicable. Mock `Q4` recall 0.757 → **0.926**, precision 0.203 → **0.38**
   over 17 episodes. `root_cause_text` is left describing the invented mechanism
   where it differs from the mapped real case (e.g. `ep_C02` "Coal feeder 2
   tripped on overload" vs RCA-14 "MCC single-phasing") — that text is the
   episode's own description; re-wording it is part of the advisor review.

## Blocked / needs a decision

1. **Ratify `episode_case_map.json` and the hold-out split together.** One
   decision: the map decides which episodes score T2/T4, the split decides which
   of those test within-cluster uncertainty. Options:
   - (a) accept the map as written — 13 episodes score against a library case,
     4 against held-out RCA-06 (cluster-T uncertainty test), 7 not-applicable;
   - (b) also re-word `root_cause_text` on the re-pointed episodes so they read
     as the real case (e.g. `ep_C02` text → "MCC single-phasing");
   - (c) drop or re-label the 7 not-applicable episodes (A04 strainer, B04 CBD,
     D02/D04 low-PA, E01–E03 fouling) since the real RCA set has no standalone
     case for their mechanisms, or give A04/D02/D04 a single mechanism-
     appropriate `correct_action_ids` (ACT-007 / ACT-019) and score T4 only.

2. **Adopt the metric change (Recommended metric change section).** Q2 top-1 is
   structurally capped by the clusters. Recommendation: headline on
   **(cluster top-1, discriminator accuracy vs `discriminating_evidence`)**,
   keep top-3, demote top-1 to a diagnostic. Not implemented — needs advisor
   sign-off because it changes what the benchmark reports.

3. **External validity of the benchmark (item 1).** The simulator reproduces
   3 clearly (+ RCA-01 borderline, RCA-14 weak) of 13 documented failure
   modes. Options: (a) accept that
   FieldMind tests the agent on a *subset* of the plant's failures and say so in
   the write-up; (b) extend the generator with the missing mechanisms the six
   tags *can* carry (Case 3 HP-heater loss, Case 4 passing spray valve, Case 5
   load rejection — all steam-side, all within the six-tag space); (c) narrow
   the library to the cases the simulator can produce (defeats the point of
   using the real RCA set). Scope decision for the advisor.

4. **Undiscriminable clusters (item 2).** Four clusters collapse on six tags.
   RCA-14 ≈ RCA-18 (byte-identical) and RCA-11 ≈ RCA-12 ≈ CBD matter for
   episodes in the set. Options: (a) score T2 at cluster granularity (see the
   metric recommendation); (b) add a seventh+ tag (furnace draught, economiser
   O2, per-compartment bed spread) — but plan §1 says "do not add a seventh
   tag"; (c) leave the discriminators as go-observe items and accept lower
   top-1. No change made.

## What I could not verify

- **The real cases' signatures against a real plant.** They are transcribed
  from the PDF's stated timelines (§2.1) and evidence lists (§3, §6.1). The PDF
  itself says the incidents are "representative composites … worked examples for
  training and method, not the verified record of any particular plant event"
  (§3). The signatures are as faithful to that text as the six-tag vocabulary
  allows; several (RCA-09, 10, 13, 15, 17) lose their real discriminating signal
  in the projection and carry only a go-observe `discriminating_evidence`.
- **Agent-level effect of the records / query wiring.** Verified structurally
  (both render into the L4 prompt, records time-filtered to `now_s`). The mock
  backend does not reason over prompt text, so its T2/T4 are unchanged by
  records/query; the effect is only measurable on `gemini`/`litert`, which were
  not run here.
- **T8 (text ablation) on the re-keyed notes.** The 15 tier-B/C key notes are
  present after the `KEY_NOTES` re-key, but the mock backend's T2 is
  note-insensitive (it re-ranks cases, not notes), so the ablation collapse can
  only be confirmed on a reasoning backend.
- **Whether `RCA-06` is the right single episode-backed hold-out.** It makes
  family C (6 episodes) a within-cluster-uncertainty test; that is a large
  fraction of the fault set. The advisor may prefer a smaller sacrifice.
- **The RCA-01 rank improvement under a reasoning backend.** The Route-A
  aggregated-signature analysis (2/4 FCV episodes top-3 under the real matcher,
  3/4 under a plain weighted-Jaccard) is the cleaner measure; the live per-tick
  harness (Route B) still shows low top-1 because mock only re-ranks the top-4
  retrieved and the per-tick signature is noisier than the aggregate. Whether a
  reasoning backend, given the corrected
  case in its retrieved set plus the FCV key note, actually selects RCA-01 is
  not measurable on mock.
- **The recommended (cluster top-1, discriminator accuracy) metrics.** Proposed,
  not implemented; the discriminator-accuracy scorer (keyword match of the
  agent's `discriminator` field against the mapped case's `discriminating_
  evidence`) has not been written or calibrated.
