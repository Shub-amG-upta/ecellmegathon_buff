# Multi-agent accuracy recovery: report

## Status
STEP 1 BUILT ON THE MOCK, NOT MEASURED; THREE PRE-BOARD RISKS CLOSED. Nothing has been measured with the real model:
the QIDK is not attached to this machine. `bench/board_session.sh` (preflight, baseline 3, replay and model checks,
single-agent reference) is ready for the board laptop. Step 2 is not started.

**No accuracy claim is made in this work until the final gate (human decision 6) passes.** Mock numbers measure the
deterministic and retrieval layers only. All plant data is synthetic.

## Why this work exists
On the four board runs in `outputs/` (reporting set, Llama 3.2 3B on the NPU, Gemma 3 1B on the CPU, grammar on,
lockstep) the multi-agent's published first cause is in the true group on 0.59 / 0.12 / 0.79 / 0.03 of scored ticks
(A01 / B01 / C01 / D01), against 0.55 / 0.55 / 0.98 / 0.82 for the deterministic belief alone. Causes found: the
model's order is published above belief (the single agent's `merge`), the answer starts with a group letter that
drives the pick, the prompts' worked examples are echoed, the compact case lines lost their meaning, the true case is
often not shown, stale answers are reused, and the Gemma 1B verifier and text reader add noise.

## Answers before anything (no code)

**a. A01, B01, C01, D01 were REPORTING episodes** (`data/episodes/ep_*`, the 30-episode reporting set), not dev.
The report will record: the reporting set has been looked at for this diagnosis (outputs zips, and the earlier
Phase 4b pre-grammar runs on A01/B01); every decision in this work is made on dev only; the 30 reporting episodes
are not run in this work.

**b. Real-model single-agent results on dev: only 3 episodes, from older code.** The Phase 0b campaign
(`results/board/B/llama32-3b/round{1,2,3}.dev_{A01,B01,C02}.*`, summary in `reports/campaign_watch.md` and
`results/board/C/model_choice.json`): Llama 3.2 3B, NPU lane, single agent, answer cap 256, no grammar. Library
group top-1 0.507 (mock floor 0.501), raw citation faithfulness 0.828. Those run files predate the harness's
`belief_supports` key, so **Q3_rel cannot be computed from them**. There is **no full-dev single-agent real-model
run**. Decision 6 needs one (see "Single-agent reference" below). The reporting-set single runs in
`outputs/single_agent_llama32-3b.zip` are not usable for decisions.

**c. Board time per multi-agent dev episode.** Measured on the reporting set, lockstep, fast.yaml: wall 194 s
(A01, 70 min episode), 234 s (B01, 90 min), 84 s (C01, 90 min), 420 s (D01, 160 min): about 2.5 to 2.8 s per
episode-minute for fault episodes, plus 60 to 180 s chip cooling between episodes. Dev durations are 45 to 200 min,
so a fault dev episode is about 2 to 7 min of board time. Estimates (not measured): quick set about 45 to 60 min;
full dev about 2.5 to 3 h including cooling. E and N episodes make few or no model calls. Every number from here
on is re-measured, not assumed.

## Measurement sets (named before any run)
- **Quick set (10):** first two dev episodes per family A to D that are scored for root cause, by name order —
  `dev_A01_fcv_seize`, `dev_A02_fcv_seize_fast`, `dev_B01_tube_leak`, `dev_B02_tube_leak_fast`,
  `dev_C01_wet_coal` (held-out target RCA-06), `dev_C02_feeder_trip`, `dev_D01_high_cv_coal`,
  `dev_D03_high_cv_severe` (D02 and D04 have no root case) — plus `dev_N01_normal`, `dev_N02_normal`.
- **Full dev set:** all 36 in `data/episodes_dev`. Used only at the three STOP points.
- **Reporting set:** not run.

## Metric definitions (fixed now)
- **Published group top-1** = evaluator `group_top1` over library episodes (headline), held-out reported
  separately; per-tick share of scored ticks whose published rank-1 is in the true group.
- **Belief-alone** = the same metric on belief's own order (`rank_hypotheses`, insertion-order ties, as a
  belief-only publication would show it) = evaluator `belief_group`; `belief_group_tiefair` reported beside it.
  Belief takes no model input, so it is computed from a mock run of the same code, no board needed (verified by
  checking it equals the board run's `belief_ranking`-based value on the quick set at Step 0).
- **Answer quality in place of format failures** (grammar makes those zero): Q3_rel, order echo (share of answers
  whose ranked case lines are 1,2,3… in listed order), share of answers equal to a prompt example, `x`/`n`
  self-contradictions (until removed).

## HUMAN DECISIONS (committed in the report before any measurement)
1. Belief's order is the default; a checked model answer moves a case only by a capped amount and breaks ties among
   belief's leaders. **The plan gives no number** — see "Decision 1: STOP" below.
2. Verifier off until it passes the known-wrong-answer test (`bench/verifier_wrong_answer.py` exists).
3. Model text reader off. Diagnostician gets the relevant raw notes in a data fence, selected and capped as the
   single agent does (`NotesStore.search`, `k_notes`, the single agent's `<<< >>>` block). Note-injection tests must
   pass. Code-only note reader is a later comparison.
4. Two-model setup (Llama 3B NPU, Gemma 1B CPU) is a deliberate change to the plan for the demonstration. Accuracy
   runs use Llama 3B only.
5. Keep rule per step: keep if quick-set published group top-1 does not fall by more than 0.01 against the previous
   kept step, else revert. Every prompt + answer cap < 1,280 tokens on all four tokenizers (`bench/token_count.py`).
6. Final gate (full dev, real model): published group top-1 >= belief-alone − 0.01; and Phase 2 limits against the
   single agent's real-model result: group top-1 drop <= 0.05, Q3_rel drop <= 0.02, above the 0.501 floor.

## Decision 1: STOP — the plan has no number for the cap
Quote, `docs/multi_agent_plan.pdf` p.13 ("Blackboard and messages"): *"Model answers never write to belief
directly. The belief agent takes a checked answer as evidence and moves each case's log-odds by a capped amount.
This keeps the lesson from the old `_merge` bug: a model answer nudges belief, it never replaces it."* p.12 table:
belief is written by "Retriever and belief only"; p.14 failure table: diagnosticians' disagreement "Both go into
belief as separate evidence". No value for the cap is given anywhere in the plan. Per your instruction I do not
pick; two options:

- **Option A — persistent capped evidence (the plan's literal reading).** The retriever-and-belief agent (owner of
  `belief`) adds the gate-checked answer as evidence after the deterministic update: rank-1 case +δ, rank-2 +δ/2,
  others 0; at most one model nudge per case per tick; total per-case model contribution clamped to ±Δ over the
  episode (kept as a separate per-case counter so it can be reported and removed). δ = 0.35 = `STEP_SUPPORT`
  (CITED, `world_model.py`: the model's pick counts as one matched movement triple, no more); Δ = 1.2 = the existing
  per-tick bound in `update_hypotheses` (CITED). Published order = belief order after the nudge.
  Reasons for: it is what p.13 says, it accumulates agreement across ticks, both sides add evidence separately
  (p.14). Against: a persistent model term is the `_merge` lesson in slower form; a model that is consistently wrong
  (D01: B-group 78 of 96 answers) can move a 0.5 case past a 0.98 one over a few ticks unless Δ is small; it writes
  into a `fieldmind/agent` dataclass's log-odds from `fieldmind/multi` (allowed: the multi retriever owns `belief`,
  but it diverges from the single agent's belief).
- **Option B — per-tick tie-break only, nothing written to belief.** Published order = belief order; the model may
  reorder only cases whose log-odds are within ε of belief's leader (belief's "leaders"), and may not lift any case
  from outside that set. ε = 0.35 (`STEP_SUPPORT`: one movement triple of evidence; cases closer than that are not
  separated by the evidence belief has). Recomputed every tick, so a wrong model cannot drift belief.
  Reasons for: cannot make belief-alone worse by more than the ticks where belief's leaders are tied or near-tied
  (bounded and countable in advance); simplest to test; belief stays identical to the single agent's. Against: not
  the plan's "evidence into log-odds"; no cross-tick accumulation; the model has no effect when belief is confidently
  wrong (A01/B01 tie rates 0.15/0.49 on the reporting runs suggest that is where most room is anyway).

Either way the merge happens in a new gate/belief function in `fieldmind/multi` (the single agent's `merge` is kept
for the Phase 1 parity path). **Build stops here until you choose A or B (or give δ/Δ/ε).** Step 0 does not depend
on the choice and can proceed if you allow it.

## Single-agent reference for decision 6 (needs your decision)
Decision 6 compares against the single agent's real-model result on dev, which does not exist (b). Options: run
the single agent, Llama 3B, full dev, current code (with `belief_supports`), once before the final gate. Estimate
(from campaign call times 11.4 s mean, ~2,000 diagnosis + ~200 verifier calls on dev): about 7 to 9 h of board time.
I'll put it in the plan as the run before the final STOP and flag it; tell me if you want it scheduled differently
(e.g. overnight right after Step 0).

## Step 0 — no behaviour change (one commit + runs)
- `scripts/benchmark_multi_lockstep_all.sh`: pass `--log-prompts`; run file then holds every prompt and raw reply
  (envelopes and text-reader records), already gzip-saved. Add `--episodes-dir` and an episode-list argument. Add
  `bench/replay_multi.py` that reads a saved run and re-applies a merge rule offline (from the per-tick
  `multi.compact[].answer`, `case_ids`, `fact_ids`, `belief_ranking` incl. `log_odds`).
- Config for accuracy runs: `configs/accuracy.yaml` overlay = fast.yaml with **both lanes on Llama 3B** (decision 4;
  same GGUF on both lanes, plan rule 3). Decision taken (conservative): the Step 0 baseline already uses Llama on
  both lanes, so the verifier and text reader in Step 0 are Llama 3B, not Gemma 1B; recorded as a difference from
  the outputs runs.
- Belief-alone on the full dev set from a mock multi run with the same overlay (belief has no model input).
- Board: current multi-agent, unchanged code, quick set, Llama 3B. Chip temperature logged before/after each.
- Tests: prompts/replies present in saved runs; replay of an unchanged rule reproduces the run's published ranking
  tick for tick (0 differences) — mutation-checked.
- **STOP: show belief-alone (full dev), current multi (quick set), and the single-agent reference status.**

## Steps (one commit each, quick set on the board after each, keep rule 5)
1. **Merge rule (decision 1, A or B).** First offline replay on the four saved outputs runs (reporting set: shown
   for the diagnosis only, not used to decide) and on the Step 0 quick-set replies; then board. Report: ticks where
   the model's nudge changed rank 1, and how many of those changes were right. Prediction committed before the board
   run (from the replay); a contradiction stops the work.
2. **Remove `g`.** Answer format and grammar (`compact.py` prompt template, `grammar.diagnosis`, `expand_diag_answer`);
   code derives the group from the first ranked case's letter. Token check.
3. **Remove `x` and `n`.** Same files; `unexplained` then comes only from the gate's own rule (ALARM/CRITICAL facts
   not cited), `notes used` dropped from telemetry.
4. **Remove the worked examples** from `diag_compact.txt` (and `ver_compact.txt` is moot while the verifier is off
   until step 8). One extra quick-set run with `case_order: shuffled`: report the share of answers that are just the
   listed order (order echo).
   **STOP: full dev run; table of steps 0–4 beside belief-alone.**
5. **Case lines.** Short name per case (a few words from its title or cause) and a check that can be compared with the
   facts (proposed: the case's expected moving triples in the fact vocabulary, e.g. "expects: bed temperature rising,
   heat accumulating, steam flat"). **Show all 13 new lines and stop before running.** Stored as a reviewed table
   (`data/kb/case_short.json`), not generated at run time, so the case library itself is untouched.
6. **Case slots.** Belief's top cases (top 2) always shown; all-FLAT cases (RCA-09/10/15, group B) take at most one
   slot. Report how often the true case is missing from the prompt, before and after (mock dev count first, then
   board).
7. **Reuse.** Reuse an answer only while the side's evidence fingerprint AND belief's top case are unchanged, and
   never when older than N ticks. Propose N from the data (distribution of reused-answer ages and of ticks between
   belief rank-1 changes on dev) and **stop for your decision before measuring.**
8. **Decisions 2 and 3.** Verifier off (`agent.verifier: never` in the accuracy overlay; known-wrong-answer test run
   and reported first); model text reader off (`compact.text_reader: false`); diagnosis prompt gets raw relevant
   notes in a data fence via the single agent's selection, with record-facts kept. Note-injection tests (existing
   `test_text_reader` injection tests adapted + a new one: injected note text appears only inside the fence and the
   answer cannot cite it as a fact). Token check (raw notes cost more tokens).
   **STOP: full dev run against the final gate (decision 6); every number per episode beside the single agent and
   belief-alone.**

Per step: tests in `tests/` for the new rule, mutation checks with `PYTHONDONTWRITEBYTECODE=1` and `__pycache__`
cleared; every number that moves gets a stated cause or "unexplained"; a contradicted committed prediction stops the
work; `--arch single` on dev still equals `results/baselines/single_v3_summary.json`-era behaviour (mock, 0
differences) as a regression check.



## AMENDMENT 1 to the pre-registration: HUMAN DECISIONS for Step 1 (committed before any replay of a merge rule)
1. **Merge rule.** Option B, `tiebreak` (0.35 log-odds band, recomputed every tick, nothing written to belief) is
   PRIMARY. Option A, `nudge` (+0.35 to the model's rank 1, +0.175 to its rank 2, at most 1.2 per case per episode)
   is a pre-registered SECONDARY arm. **A is adopted over B only if**, on the dev quick set replayed from baseline 3,
   A beats B on published group top-1 by at least 0.03 AND does not lose to B by more than 0.05 on any single
   episode. Otherwise B stands.
2. Both are implemented in the gate behind `multi.merge_rule: {belief_only, tiebreak, nudge}` (plus `model`, the
   earlier behaviour, kept as the default in `configs/base.yaml` so nothing else changes). `configs/accuracy.yaml`
   defaults to `tiebreak`. Unit tests: tiebreak never moves a case outside the band; nudge respects the per-episode
   cap; belief_only reproduces the Step 0 belief baseline exactly. Per tick the run logs whether the published order
   differs from belief's and why.
3. The replay is verified on the mock dev run only (correctness, not selection). Merge rules are **not** replayed on
   the `outputs/` reporting runs for any decision.
4. Report which GGUF path and sha256 `bench/board.py` expects for the CPU lane.
5. `bench/board_session.sh`: (a) baseline 3, (b) replay check of the saved run with 0 differences, (c) the
   single-agent full-dev reference with cooldown and temperature logged, resumable per episode; stops before (c) if
   (a) or (b) fails.
6. Step 2 is not started.

Design detail fixed with this amendment (mine, recorded for review): the `nudge` offsets are kept in a gate-owned
board section `model_evidence` and added to belief's log-odds only when ranking for publication; they are not written
into the Hypothesis objects, so belief's own update, retirement and the single agent's belief stay unchanged and the
offset can be reported and removed. A reused (cached) side answer gives no new nudge; only an answer made this tick
does. Under `nudge` the published confidence is sigmoid(log-odds + offset).

---

# Results

## Step 0: no behaviour change
What was built (one commit):
- `scripts/benchmark_multi_lockstep_all.sh` now passes `--log-prompts` by default (`LOG_PROMPTS=0` turns it off) and
  takes `OVERLAY`, `CPU_MODEL`, `NPU_MODEL`, `EPISODES_DIR` from the environment. Every prompt and raw reply
  (diagnostician, verifier, text reader) is saved in the gzipped run file.
- `configs/accuracy.yaml`: `configs/fast.yaml` with Llama 3.2 3B on BOTH lanes (decision 4). It differs from fast.yaml
  only in the CPU lane's model.
- `bench/harness.py` writes `belief_order` (belief's live hypotheses in insertion order) per assessment, both arches,
  so a replay breaks confidence ties exactly as `rank_hypotheses` does. Nothing in `fieldmind/` reads it.
- `bench/replay_multi.py`: re-applies a merge rule to a saved run's answers offline. Rules: `current` (today's gate)
  and `belief` (belief-alone). The model's answers are not regenerated, so only rules that change what is done
  with an answer can be replayed; a prompt change needs the board.

### Baseline 1: belief-alone, full dev (36 episodes), from a mock run with the accuracy overlay
Belief takes no model input, so this is the number a belief-only publication gives with any backend.

| | library (13 episodes) | held-out (4 episodes) |
|---|---|---|
| **belief_group (belief-alone, decision 6 reference)** | **0.502** | **0.709** |
| belief_group_tiefair | 0.477 | 0.700 |
| belief_top1_tiefair | 0.375 | n/a |

Per episode (belief_group): A01 0.018, A02 0.469, A03 0.237, A05 0.000, A06 0.034, B01 0.469, B02 1.000,
B03 0.375, B05 0.695, C02 0.909, C04 0.600, D01 0.784, D03 0.939; held-out C01 0.725, C03 0.933, C05 0.367,
C06 0.811. The library value equals the Phase 1 dev value (0.502): the belief layer has not changed.

**Recorded, not acted on:** on dev, belief is almost never right on family A (A01 0.018, A05 0.0, A06 0.034), unlike
reporting A01 (0.55). Any merge rule that keeps belief's order as the default cannot fix that; only the model or a
belief fix (open item for `main`) can. Option B of decision 1 in particular can only act inside belief's near-ties.

The same mock run's published group top-1 is 0.572 library / 0.474 held-out. **That is not an agent result**: the
mock re-ranks retrieved cases by retrieval score.

### Baseline 2: the replay is faithful
| run | ticks compared | differences from what was published | of those, on a belief tie the run could not resolve |
|---|---|---|---|
| mock, full dev, accuracy overlay (has `belief_order`) | 5,850 | **0** | 0 |
| outputs A01 (board, reporting set) | 130 | 2 | 2 |
| outputs B01 | 175 | 2 | 2 |
| outputs C01 | 172 | 0 | 0 |
| outputs D01 | 310 | 1 | 1 |

The board runs predate `belief_order`; their 5 differences are all ticks where belief's top 4 holds a confidence tie,
broken by insertion order, which those files did not save. Replayed group top-1 (per tick) of the four outputs runs,
current rule against belief rule: A01 0.594 / 0.547, B01 0.125 / 0.555, C01 0.791 / 0.977, D01 0.031 / 0.816 (the
figures of the diagnosis, reproduced). These are reporting-set runs, shown for the diagnosis only, not used to decide.

### Baseline 3: current multi-agent, quick set, Llama 3.2 3B on the board — NOT RUN
The QIDK is not attached to this machine. Command for the board laptop (results to their own folder):

```
OVERLAY=configs/accuracy.yaml CPU_MODEL=Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf \
EPISODES_DIR=data/episodes_dev OUT=results/accuracy_fix/board_step0 \
scripts/benchmark_multi_lockstep_all.sh dev_A01_fcv_seize dev_A02_fcv_seize_fast dev_B01_tube_leak \
  dev_B02_tube_leak_fast dev_C01_wet_coal dev_C02_feeder_trip dev_D01_high_cv_coal dev_D03_high_cv_severe \
  dev_N01_normal dev_N02_normal
```
`bench/board.py` must list the Llama GGUF as allowed on the CPU lane (it checks the sha256; not verified here).

### Single-agent reference (decision 6): does not exist on full dev
See answer (b). Needs a full-dev single-agent board run with the current harness (estimate 7 to 9 h); not run.

### Verification
| step | result |
|---|---|
| ran the module | mock full dev with the accuracy overlay, 36 episodes, exit 0; replay on that run and on the four outputs runs |
| self-tests | `tests/test_replay_multi.py` 7 passed; full suite 416 passed |
| independent re-derivation | belief-alone two ways: evaluator `belief_group` 0.502 (library, scored window), and the Phase 1 dev report's value 0.502 from a different code version; equal. Replay of the current rule against the run's own published ranking: 0 differences over 5,850 ticks |
| mutation check | 7 mutations, 7 caught (R2 only after the test gained a backend whose verifier fails a claim) |
| regression | `--arch single`, dev, mock: 0 decision differences against the Phase 1 single run (new keys and prompt text excluded) |

| mutation (`PYTHONDONTWRITEBYTECODE=1`, `__pycache__` cleared) | caught by |
|---|---|
| replay ignores insertion order on ties | `test_replay_of_the_current_rule_reproduces_the_published_ranking` |
| replay skips the verifier | same (after adding `FailingVerifierMock`) |
| replay ignores reused answers | same |
| replay uses 0.5 instead of 0.3 for a case belief does not hold | same |
| harness `belief_order` includes retired hypotheses | `test_belief_order_is_live_hypotheses_in_insertion_order` |
| runner drops `--log-prompts` | `test_board_runner_passes_log_prompts_by_default` |
| accuracy overlay keeps Gemma on the CPU lane | `test_accuracy_overlay_runs_one_model_on_both_lanes` |

### Numbers that changed
None. Step 0 changes no behaviour; the run files gain `belief_order`.

### What I could not verify
- The board run (baseline 3) and anything about Llama 3B on the CPU lane (speed, `bench/board.py` allow-list).
- That replayed outputs runs are exact on tied ticks (5 ticks, insertion order not saved).

## Step 1: merge rules (built; verified on the mock dev run only)
`multi.merge_rule: model | belief_only | tiebreak | nudge` (`fieldmind/multi/merge_rules.py`, applied by the gate's
`apply_rule` on every non-QUIET tick, after the side answers are checked and before the verifier). `model` is the
earlier behaviour and stays the default in `configs/base.yaml`; `configs/accuracy.yaml` sets `tiebreak`;
`run_demo.py --merge-rule` and the runner's `MERGE_RULE` override it. Lockstep only (real time refuses a rule other
than `model`); needs `multi.split`. Each non-QUIET tick logs a `rule` record: the rule, the cases the model ranked,
whether the published top 3 differs from belief's top 3, and why (for `tiebreak` also belief's leaders and which
model cases were inside and outside the band; for `nudge` the offsets).

Implementation detail found while verifying: the rules compare log-odds rounded to 4 decimals (the precision run
files save). Without it, unrounded sums such as -0.7875 + 1.2 and 0.4125 differed in the last bit and the offline
replay disagreed with the gate on 2 of 5,850 ticks; with it, 0.

### Verification on the mock dev run (correctness, not selection; amendment item 3)
Mock numbers are the deterministic and retrieval layers only; the mock "model" re-ranks retrieved cases by score.
Nothing below is used to choose a rule.

| rule | replay of the same rule vs the run's published ranking (5,850 ticks) | ticks the published top 3 differs from belief's | mock library group top-1 | mock held-out |
|---|---|---|---|---|
| belief_only | 0 differences; also 0 against the Step 0 `belief` replay | 0 of 1,982 | 0.508 | 0.709 |
| tiebreak | 0 differences | 258 of 1,982 ("model reordered belief's leaders"); 1,139 "agrees", 583 "no model case in the band", 2 "no answer" | 0.517 | 0.681 |
| nudge | 0 differences | 952 of 1,982 | 0.579 | 0.722 |

`belief_only` gives 0.508 where the evaluator's `belief_group` gives 0.502. Cause, checked: the evaluator reads
`belief_ranking` (log-odds order) and the published list is `rank_hypotheses` (confidence rounded to 3 decimals,
ties in insertion order); their rank 1 differs on 13 of 1,982 ticks. Both are "belief alone"; the final gate
(decision 6) compares the multi-agent's published metric with `belief_group`, as pre-registered, so on the mock dev
set a belief-only publication would already clear that bar by 0.006 from this definitional difference alone. Recorded.

Why replaying a different rule on baseline 3's answers is exact for group top-1: the diagnosis prompt shows belief's
own top 3 (from the belief section), which no rule writes to, and the reuse fingerprint does not read the published
ranking, so the model's answers are the same under every rule. What differs is the verifier: its trigger reads the
published rank-1 confidence, so under another rule it would run on other ticks and judge other claims. The verifier
only caps confidences and never reorders, so group top-1 is unaffected; confidence-based numbers (ECE, low-confidence
rates) from a cross-rule replay are not exact and will not be reported as such.

### Tests
`tests/test_merge_rules.py`, 19 tests: belief_only equals `initial_claims` of `rank_hypotheses`; tiebreak reorders only
inside the band, never lifts a case from outside it (4 cases), band edge inclusive at 0.35, takes tied cases beyond
belief's top 3, keeps belief's confidence; nudge steps (+0.35 / +0.175, once per case per tick), the per-episode cap
(1.2, also as an ordering outcome: 1.79 + 1.2 stays below 3.0), confidence of the sum; on a dev episode for each rule:
every non-QUIET tick has exactly one rule record, `differs_from_belief` matches the published list, and the replay
reproduces the run; nudge leaves belief's own ranking identical to belief_only's; unknown rule and rule without
split are refused. `tests/test_replay_multi.py` pinned to `merge_rule: model` (it checks the unchanged gate).

| mutation (`PYTHONDONTWRITEBYTECODE=1`, `__pycache__` cleared) | caught by |
|---|---|
| tiebreak band ignored (model may lift any case) | 6 tests, incl. `test_tiebreak_never_lifts_a_case_from_outside_the_band[*]` |
| band exclusive at its edge | `test_tiebreak_band_edge_is_inclusive_at_0_35` |
| band 0.5 | 5 tests |
| nudge cap removed | `test_nudge_respects_the_per_episode_cap`, `test_nudge_offsets_live_in_their_own_section_and_belief_is_untouched` |
| rank 2 gets a full step | `test_nudge_rank1_and_rank2_steps_once_per_case_per_tick` |
| more than one nudge per case per tick | same |
| belief order ties by log-odds | 3 episode tests |
| nudge written into belief's log-odds | 2 tests |
| `differs_from_belief` never set | 2 tests |
| reused answers nudge again | episode test (nudge) |
| rule skipped on ticks with no model job | 3 episode tests |

Full suite: 435 passed, 1 failed: `tests/test_campaign.py::test_kill_minus_9_mid_episode_then_resume_is_identical`.
**It is intermittent and not caused by this step**: with this step's changes stashed it failed 2 of 3 runs, with them
in 3 runs each it failed twice and passed once, with and without the changes. Recorded as open; its
cause (the kill timing of the resume test) was not investigated.

## Answer 4: what `bench/board.py` expects for the CPU lane
`start_lane` refuses any file whose name is not in `CANDIDATES` or whose sha256 **on the board** differs. The lane
does not matter to the check; the CPU lane loads the same file as the NPU lane:

- path on the board: `/data/local/tmp/llm/Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`
- sha256: `5aa3ece50ab33d09a7181888a75f8755f924c662dc99626e7f45440adfeadcdb`
- check on the board laptop: `adb shell sha256sum /data/local/tmp/llm/Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`
- CPU lane command: `llama-server -m <that path> --port 8081 -c 4096 -np 1 --device none -ngl 0 -t 6 -fit off
  --cache-ram 0 -lv 4`, with `LD_LIBRARY_PATH` and `ADSP_LIBRARY_PATH` set. Not verified here: two 3B servers at once
  (about 1.9 GB each) on the board's memory, and the CPU lane's speed with the 3B.

## `bench/board_session.sh` (amendment item 5)
Start it at the board laptop: `bench/board_session.sh`. Output under `results/accuracy_fix/board_session/`.
- **(a)** baseline 3: `scripts/benchmark_multi_lockstep_all.sh` with `OVERLAY=configs/accuracy.yaml`, Llama 3B on both
  lanes, `MERGE_RULE=model` (unchanged gate), dev quick set, prompts and replies saved, lanes restarted per episode,
  placement checked from the lane logs, chip temperature before and after each episode with the cooldown protocol.
- **(b)** every quick-set run file exists, every model call in it has its prompt and reply, `belief_order` is present,
  and `bench/replay_multi.py --rule model --check` reproduces every run with 0 differences.
- **(c)** single agent, NPU lane, Llama 3B, all 36 dev episodes, `--log-prompts`, lane restarted per episode, same
  cooldown and temperature log (`temps_single.jsonl`), lane log lines kept per episode.
- Resumable: an episode with a saved summary is skipped in (a) and (c). If (a) or (b) fails it exits before (c).

Checked on the laptop with `BACKEND=mock` (board steps skipped): (a) saved all 10, (b) all 10 replay with 0
differences, (c) ran on two episodes; a second start skipped every saved episode; a corrupted run file made (b) stop
with exit 1 before (c). Two portability fixes to the runner were needed for that check and change nothing on the
Fedora board laptop: `systemd-inhibit` is used only when present, and empty bash arrays are expanded safely.

## Pre-board risks (closed before the board session)

### Risk 1: does the merge rule feed back into the model's inputs? No, except the verifier (by design)
Traced in the code, every input of every model call and of the call-or-reuse decision:

| model call / decision | built from (board sections) | reads the published ranking? |
|---|---|---|
| diagnostician prompt (`agents/diagnostician._compact_job`, `compact.build_diagnosis`) | `facts`, `retrieval` (cases, notes, records), `notefacts`, `recordfacts`, `belief` (world line: belief's own top 3 by confidence), `trust`, `findings` (the long world summary, `wm_summary`, also reads belief, not claims); side split from facts and findings | **no** |
| answer grammar (`grammar.diagnosis`) | the prompt's line map | no |
| text-reader prompt (`agents/text_reader`) | one raw note + the fixed vocabulary; P2/P3 from this tick's facts | no |
| call-or-reuse (`DiagnosticianAgent.fingerprint`, `gate._reuse`) | side signature, open findings, note-facts, cases shown; the cached payloads in `side_answers` | no (confidence in a cached payload comes from belief) |
| retrieval and belief (`agents/retriever`) | facts, signature, case library | no |
| triage | findings, residuals, trust, status (reset every tick in `begin_tick`) | no |
| **verifier prompt** (`compact.build_verification`) | **the published claims (top 3)** and the tick's facts | **yes, by design** |

The sections the gate writes from the published ranking (`diagnosis`, `verdict`, `assessment`) have no reader that
builds a prompt or a reuse decision (grep of every `bb.read` in `fieldmind/multi`). The experience store is never
written by the multi path. So under every rule the diagnostician and text reader are asked exactly the same thing;
only the verifier is not. The verifier caps confidences and never reorders, so **A-vs-B group top-1 from the replay of
baseline 3 is exact; nothing confidence-based from a cross-rule replay is reported.** No fix needed. (If you prefer
the verifier judged on belief's order under every rule, that is a separate change; with decision 2 it is switched
off at step 8 anyway.)

Test `tests/test_rule_prompt_identity.py`: three dev episodes under all four rules on the mock, with a verifier that
fails claims and runs on every eligible tick. Tick for tick, every diagnostician and text-reader prompt and every
call-or-reuse record is identical across rules, while the published rankings do differ (asserted) and the verifier
prompts differ (asserted, documenting the exception). Mutation check: a prompt that shows the last published rank 1
(read from the `verdict` section) and a reuse fingerprint that reads it are both caught (3 tests each).

### Risk 2: does the session's resume share code with the intermittent campaign test? No
`tests/test_campaign.py::test_kill_minus_9_mid_episode_then_resume_is_identical` exercises `bench/campaign.py`'s own
resume: a per-call cache (`CallLog`, cache hits replayed from the live call log), a job manifest with attempt counts,
and `atomic_write`. `bench/board_session.sh` uses none of it: no import of `bench.campaign` in the session, its runner
(`scripts/benchmark_multi_lockstep_all.sh`), `run_demo.py` or the harness's episode loop. Its resume is per episode in
bash: an episode counts as done only when its summary exists, and the summary is the last file written (after the
gzipped run, and after the model check on the board); an interrupted episode's live tick file is renamed
`.interrupted-<time>` and the episode is re-run from tick 0 with no cache. The only code both use is the episode
replay itself (`bench/harness.run_episode`), which is not part of either resume. The campaign test's intermittency
(failed 2 of 3 runs with and without this work's changes) stays open and was not investigated.

New test `tests/test_board_session.py` (the real script, `BACKEND=mock`): SIGKILL of the whole process group in the
middle of `dev_D01_high_cv_coal` (after 30 of its ticks); the restart skips the finished episodes, redoes D01 from the
start (final run has all its ticks, the half file kept apart), and passes (b). Then a half-written single-agent
episode (run file but no summary, a broken tmp file) is redone, not skipped. Then a saved run whose ranking was
tampered with makes (b) fail and the session stop before (c). Mutations caught: (a) treating a live tick file as
finished; (c) treating a run file as finished; appending to the interrupted tick file; ignoring the replay check's
result (caught after the tampered-run case was added).

### Risk 3: preflight and silent fallback
`bench/board_preflight.py`, run by the session before (a) on the board:
- starts both lanes with the 3B (NPU :8080 `--device HTP0 -ngl 99`; CPU :8081 `--device none -ngl 0`), each through
  `bench/board.start_lane`, which refuses a file whose name or on-board sha256 is not the recorded one;
- each lane must answer `Reply with the single word OK.` with status ok, a non-empty answer, the server-reported model
  equal to `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`, and the reply labelled with its lane;
- placement from each server's own startup log: NPU every layer offloaded and a nonzero HTP0 buffer; CPU 0 layers and
  no HTP0 buffer; both logs name the 3B file;
- logs `/proc/meminfo` (MemTotal, MemFree, MemAvailable, swap) and each llama-server's VmRSS by port to
  `preflight_<time>.json`; aborts if either RSS or MemAvailable cannot be read;
- stops both lanes (the runner restarts them per episode) and exits 3 with every problem listed if anything fails.
During the runs, the fallback check is repeated on the saved data: after (a), every model call of every quick-set run
must report the 3B model and its placed lane (diagnosticians npu, verifier and text reader cpu) with no failed call;
in (c), each single-agent episode is checked the same way (all npu) before its summary is written, so a bad episode is
neither kept nor skipped on resume.
Tests `tests/test_board_preflight.py` (6) on the pure checks; 8 mutations caught (model, backend, partial offload, CPU
lane with an HTP0 buffer, run lane, run model, text-reader calls skipped, CLI exit code), one after a test case was
added. **Not verified here:** the preflight against a real board (no board attached); that `pidof` and the
`/proc/<pid>/cmdline` port extraction work on the QIDK's Android shell; memory headroom for two 3B servers.

### Follow-up checks (before the push)
**The verifier cannot change which answer is merged or its place.** From code: the gate merges the side answers (or
applies the merge rule) BEFORE the verifier runs in the tick (`orchestrator.tick`). `apply_verdict` folds a verdict
with the single agent's `Verifier.apply`, which only caps a failed claim's confidence at 0.35, flags it
`failed check`, sets a revised rank-1 confidence (always None in the compact format) and adds an `unexplained` line;
it never removes, replaces or moves a hypothesis, and nothing re-sorts after it (`finalize` publishes the list as
is). The `verdict` section it writes has no reader. So the cross-rule replay of group top-1 stays exact.
Test `tests/test_verifier_never_reorders.py`: under tiebreak and nudge, on two dev episodes, a verifier that fails
EVERY claim and one that fails only the FIRST claim (failing all cannot reveal a demotion, since all move together),
each on every eligible tick, against the same run with the verifier off: published order identical tick for tick,
every failed claim's confidence = min(its unverified confidence, 0.35). Mutations "a failed claim is demoted to the
end" and "a failed claim is dropped" are caught, by the fail-first case only (the fail-all case alone let both
through; recorded as the reason for the second pattern).

**Preflight memory logging is non-fatal.** `collect_memory` reads `/proc/meminfo` and each server's RSS; if `pidof`,
`/proc` or adb fails it records `rss: null` (and/or `meminfo: null`), prints a warning and the preflight continues.
The model, lane, placement and HTP0 checks stay fatal. Tests: missing `pidof`, adb failing, normal output; and the
whole preflight on a fake board: no `pidof` gives exit 0 with `rss: null`, a lane serving the Gemma file gives
exit 1. Mutations "memory failure fatal again" and "model check not fatal" are caught.

Full suite after these follow-ups: 453 passed, 1 failed (the intermittent campaign test, unrelated; see risk 2).

## Blocked / needs a decision
1. **The board session.** At the board laptop (Fedora, QIDK on USB), from the repo root:

   ```
   git fetch origin && git switch multi-agent-fix && git pull     # after this branch is pushed
   source .venv/bin/activate
   adb devices                                                     # the QIDK must be listed
   adb shell sha256sum /data/local/tmp/llm/Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf
   #   expect 5aa3ece50ab33d09a7181888a75f8755f924c662dc99626e7f45440adfeadcdb
   ls data/episodes_dev | wc -l                                    # expect 36; else regenerate the dev set
   .venv/bin/python -m pytest -q tests/test_board_preflight.py tests/test_board_session.py
   bench/board_session.sh 2>&1 | tee logs/board_session_$(date +%Y%m%dT%H%M%S).log
   ```
   Same command again resumes. Exit 3 = preflight failed, nothing measured; exit 1 = (a), (b) or a (c) episode failed
   (the message says which). Bring back `results/accuracy_fix/board_session/` and the log.
2. Then: the A-vs-B adoption check on the replay of baseline 3, and the step-1 board run of the adopted rule.

## Open, not fixed here
- `tests/test_campaign.py::test_kill_minus_9_mid_episode_then_resume_is_identical` is intermittent on unchanged code.
- The items under "Recorded as open" in the pre-registration.
