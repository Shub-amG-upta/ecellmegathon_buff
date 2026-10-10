# Multi-agent Phase 2: progress

Updated and committed after every commit. Mock backend only; no accuracy claim.

| commit | what | state | check |
|---|---|---|---|
| 0 | human decisions, pre-registered dev gate, expected-change statement | done | no measurement or code before it |
| 1 | Phase 1 close-out: hard-stage timing script committed and re-run | done | `bench/time_single_hard.py`; mean 0.276 -> 0.299 / 0.290 ms, p50 0.252 -> 0.256 / 0.257; max does not reproduce (0.778 -> 1.808 / 1.609, unexplained, one tick); over 200 ms: 0 |
| 2 | fixed placement in the scheduler | done | dev, mock: fixed vs earliest finish 0 differences, prompt logs byte-identical (sha `6e9b748a`, same as Phase 1); fixed vs Phase 1 multi and single 0 differences; jobs on lanes: diagnostician npu 1,982, verifier cpu 192; 15 new tests, 169 pass; 5 mutations caught |
| 3 | real tokenizer counts; 60-token answer check. **STOP and report** | done, **stop rule tripped** | `bench/token_count.py`, 4 tokenizers pinned by sha256; Phase 1 dev diagnostician prompts: mean 1,866 to 1,894, max 2,103 to 2,150 real tokens, 1,980 of 1,982 over on every tokenizer; llama.cpp cross-check 0 of 81 texts differ on 3 tokenizers; 28 new tests, 197 pass; 8 mutations caught. **Worst-case ID answer is 89 / 105 / 111 / 105 tokens against a cap of 60** |
| 4 | text reader (notes), record-facts by code, note-fact coverage check | done, **stop rule tripped** | dev, mock: decisions 0 differences vs Phase 1; diagnostician and verifier prompts byte-identical (sha `6e9b748a`); single unchanged; 259 text-reader calls = 259 notes, 0 for records; text prompts max 392 to 414 tokens; 24 new tests, 223 pass; 21 mutations caught (2 after strengthening tests). **Coverage: A03 and B03 (tier B) keep their note content only in part** |
| 5 | compact diagnostician with per-section switches (`schema`, `rules`, `cases`, `notes`, `world`, `case_order`), B' expansion in the gate, guard | done (pre-registered `7bf26a2`) | dev, mock: all off vs commit 4 0 differences, prompt log byte-identical (2,433 calls); all on: diagnostician prompts max 878 / 890 / 914 / 907 tokens (+60 < 1,280 on all four), mean 750 to 773 (Phase 1: 1,866 to 1,894); parse failures, repairs, bad lines, guard firings, cap drops all 0; 31 new tests, 254 pass; 24 mutations caught (3 after strengthening tests); phase-reviewer findings fixed or recorded in the report. Decisions with switches on not compared (G1a/G1b, commit 7) |
| 6 | compact verifier with per-section switches (`ver_schema`, `ver_rules`, `ver_claims`), pass/fail by line number, "not judged" never a pass | done (pre-registered `522c4a3`) | dev, mock: all off vs commit 5 0 differences, prompt log byte-identical; verifier sections only vs all off 0 decision differences, `ver_calls` 192 both, cap 30; all on: verifier prompts max 518 to 535 tokens (+30 < 1,280; before: 1,887 to 1,905), mean 413 to 430; not judged 0, invalid 0, incomplete verdicts 0; 22 new tests, 276 pass; 22 mutations caught; phase-reviewer findings fixed or recorded |
| 7 | dev gate and report. **STOP and report** | dev gate run (plan `05c4b30`), **stop rule tripped** | G1a: `model_only` 3,170 / rank-1 changes 580 / verifier calls 327 all match; confidence unchanged **20 vs 19 predicted**; 0 differences outside the allowed fields. G1b, comparator check, G2, G3, G4, G5, G6 pass. Reporting run not made |

## Stops hit
1. **After commit 3 (planned stop, and the answer-cap stop rule tripped).** The plan's ID-only answer format
   needs 89 to 111 tokens for a legal 3-case answer (72 to 84 for the plan's own example) against a cap of 60;
   the verifier's needs 33 to 41 against 30. Options and their measured sizes are in
   `reports/multi_phase2_prompt_shrink.md`, "Blocked / needs a decision". Commits 4 to 7 wait on the choice.
2. **Before commit 4 (the human's condition: stop if (a) or (b) changes the gate).** Format B' is decided and
   recorded as an amendment. Answering question (a) showed G1 cannot be 0 differences: with no model confidence,
   `model_only` confidences, the rank-1 confidence on about 580 dev ticks and the verifier trigger (192 calls ->
   140 or 327) must move on the mock. Question (b): the retrieval budget does not differ between mock and
   llamaserver; the 2,484 figure is not reproduced here. Two questions are open in
   `reports/multi_phase2_prompt_shrink.md`, "Blocked / needs a decision". No code changed.
3. **Resolved by the human (amendment 2).** Option (ii) for the confidence; G1 split into G1a (schema switch
   only, predicted counts committed first) and G1b (all switches, 0 differences against G1a). Item 3 answered:
   the 1,154 cases are retired by the belief floor (0.08) in the same tick they are retrieved.
4. **After commit 4 (the note-fact coverage stop rule).** Two tier-B dev episodes that need notes, A03 and B03,
   keep their discriminating note content only in part: "bfp A suction pr on lower side" can only be written as
   BFP_A LOW, and the local gauge-glass reading becomes drum_level NORMAL. Nothing is lost outright; the build
   stopped rather than judge "partly" itself. The coverage table, the kind enum and three options are in the
   report. Commits 5 to 7 have not started and no G1a run has been made.
5. **Resolved by the human (amendment 3, 2026-10-05).** Option (c): keep the note-fact schema; the partial
   losses are a recorded limit, measured later with the real model. Next: commit 5.

## Second machine (Mac, 2026-10-05): environment reproduced before commit 5
Fresh clone; `.venv` on Python 3.13.7 with `requirements.txt` plus `tokenizers` 0.23.2 and `jinja2` 3.1.6;
episodes regenerated with `data.generator.episode_build --set report` (30/30) and `--set dev` (36/36);
tokenizers fetched and checked against `bench/tokenizers/manifest.json`; `data/experience/` absent (empty store).
No committed episode checksum exists, so the episodes were checked through the runs below. Mock backend.

| check | result |
|---|---|
| test suite at `fa6ee08` | 223 passed |
| dev, `--arch single` vs `--arch multi --mode lockstep` | `bench/gate_phase2.py` strict: 36 episodes, 5,850 assessments, 0 differences; summary 0 differences |
| reporting, multi lockstep vs `results/baselines/multi_p1_summary.json` | summary and per-episode: 0 differences outside timing keys; extra keys only `Q3_rel`, `n_rel_citations`, `T3_faithfulness.rel` (added in commit 4, pre-registered as extra) |
6. **Commit 7, gate G1a (2026-10-05).** One pre-registered count differs: `model_only` confidences unchanged 20
   against 19. Code shown to follow the worded rule on 3,170 of 3,170; recomputing option (i) from the same data
   gives the committed 11, option (ii) gives 20. The 3 October computation was not committed. Reporting run waits on
   the human's decision (options in the report).
