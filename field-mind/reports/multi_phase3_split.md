# Multi-agent Phase 3: split by plant side: report

## Status
DONE on the mock (commits 0 to 5). Two side diagnosticians behind `multi.split`, calls only on changed evidence
behind `multi.split_on_change`, and a known-wrong-answer verifier tool; every commit-0 rule passes on dev (one
laptop-timing outlier explained and re-measured, commit 5). The plan's real check needs the board, after Phase 2's
real-model numbers are accepted. The 30 reporting episodes were not run. Branch `multi-agent-p3`, made from
`multi-agent` at `a672af7`.

**Mock only.** The mock backend re-ranks retrieved cases and does no reasoning, so its numbers measure the
deterministic and retrieval layers only; nothing here is an accuracy result. All timings are laptop timings.

## HUMAN DECISION: start Phase 3 before Phase 2 is closed (2026-10-05)
The brief says not to start Phase 3 until Phase 2's mock work and its real-model test are done. The human
overruled it. Reason: board time is the bottleneck; Phase 3 is built on the mock while the QIDK runs Phase 2, on a
separate branch so the board run stays pure Phase 2. Conditions recorded with it:
- Phase 3 is not run on the board until Phase 2's real-model numbers are in and accepted.
- Phase 3 reuses Phase 2's pieces by import (prompt sections, switches, answer format B', note-facts, the compact
  verifier), so a Phase 2 fix flows into Phase 3. Estimated rework if the board forces a Phase 2 change: 1 to 3
  hours in the likely case, about a day if the answer format itself must change.
- Open from Phase 2 and not decided: gate G1a (one count 20 against a predicted 19). It does not block this work.

## HUMAN DECISION: the six design choices
The human said "start" without choosing; by the working rule (CLAUDE.md, "choose the more conservative option"),
the option marked (a) in the plan put to the human is taken for each. The human can change any of them.
1. **Merging the two answers:** the side with the more severe facts first (tie: water first), then the other side's
   cases not already named; a case named by both appears once with both sides' citations. The combined list goes
   through the single agent's `merge`, unchanged.
2. **Steady ticks (commit 3):** while a side's evidence is unchanged, its last checked answer is re-used (the plan's
   "accepted if that side's evidence hasn't changed since"); no new call.
3. **Verifier lane:** fixed on the CPU, as decided for accuracy gates; "whichever lane frees first" only by switch.
4. **Model answers moving belief:** not in Phase 3. Belief stays exactly as in Phase 2 (answers reorder the shown
   claims through `merge`, as today). Open item: the plan's capped belief nudge, as its own change later.
5. **No side has evidence but triage is WATCH or above:** both sides are asked.
6. **Working style:** build through, phase-reviewer after each commit, stop at the end of commit 5; a short status
   (done, left, estimate) after each task.

## Sides (DESIGN, from the plan's agent table, p.5)
| side | tags | pseudo-tags | lane (`multi.fixed_placement`, already in config) |
|---|---|---|---|
| water | `drum_level`, `feed_water_flow`, `steam_flow` | `water_balance` | NPU (`diag_water`) |
| heat | `bed_temp_avg`, `drum_pressure`, `ms_temperature`, `steam_flow` (as load) | `energy_balance` | CPU (`diag_heat`) |

- A fact belongs to every side whose tag set holds one of its tags (`steam_flow` is in both, as the plan's "steam
  flow as load"). A BALANCE fact naming feed and steam flow is water; the other BALANCE facts are heat (the same
  split as `world_model._fact_ids`).
- A case belongs to the sides its MOVING (non-FLAT) signature triples touch: water only RCA-01; heat only RCA-03,
  07, 13, 14, 18; both RCA-04, 05, 11, 16; none (all-FLAT) RCA-09, 10, 15. A both-sides or no-side case is shown to
  both. `family` in the case library is not used (it is for coverage analysis only).
- A side is **active** on a tick at WATCH or above when it has a non-INFO fact or an open finding on its side.
- Retrieval and belief are NOT split: one retrieval on the full signature and one belief update, exactly as Phase 2.

## Each side's prompt (reuses the Phase 2 compact sections)
The Phase 2 all-on prompt, restricted to the side: its facts (at most 8 lines, the plan's budget, most severe
first), its cases from this tick's retrieval (at most 4), its note-facts (a note-fact goes to a side when one of its
subjects is a tag of that side; others go to both; at most 4) and the record-facts (at most 4, to both), belief top 3
as today, plus **one line about the other side**, written by code: that side's most severe fact detail, or "steady".
Answer format B', cap 60, line map per job, expanded by the gate as in Phase 2.

## Commits
| # | what | behaviour change |
|---|---|---|
| 0 | this plan | none |
| 1 | side definitions as pure functions + tests | none (nothing calls them yet) |
| 2 | two side diagnosticians (`multi.split`, default off), merge rule, verifier after the merge | by construction, switch on |
| 3 | call only on change + re-use on steady ticks (`multi.split_on_change`, default off); side-aware stale rule | by construction, switch on |
| 4 | known-wrong-answer verifier tool (forced wrong rank 1; verdicts recorded) | none (a tool) |
| 5 | mock dev run, report | — |

## Predictions and pass rules (dev, 36 episodes, mock, lockstep, fixed placement; written before any code)
| check | rule |
|---|---|
| commit 1 | Phase 2 all-on dev run: 0 differences and byte-identical prompt log against the `a672af7` run |
| switches off (commits 2, 3) | the same, against the `a672af7` all-on run |
| split on: deterministic layer untouched | `state`, `triage`, `facts`, `belief_ranking`, `belief_supports` identical to the Phase 2 all-on run on every tick (belief and retrieval are not split) |
| split on: side prompts | every side prompt holds only its side's facts and cases (checked on every dev call); prompt + 60 < 1,280 on all four tokenizers |
| split on: health | parse failures 0, out-of-range lines 0, invalid verifier answers 0 |
| split on, on change on | the same, plus: no side job on a tick where that side's evidence is unchanged and an answer is cached |
| single agent | unchanged (G4 rule of Phase 2) |
| timing | S7 0; P0 over 200 ms 0 (laptop) |
| tests | suite green; every new test mutation-checked (`PYTHONDONTWRITEBYTECODE=1`) |
| reported, not gated | model calls per tick (mean, max), diagnosis calls per side, simulated diagnosis latency mean / p95 (placeholder lane rates: an expectation, not a result), mock group top-1 overall / family B / one-side families (a retrieval-layer number) |

The plan's real check (group top-1 at least Phase 2's, separately on cross-side faults; diagnosis latency; does the
verifier disagree with a known-wrong answer) needs the board, after Phase 2's real-model numbers.

Any rule that fails: stop that item, record it, adjust nothing; continue only on work that does not depend on it.

---

# Results by commit

## Commit 1: side definitions (`fieldmind/multi/sides.py`). KEPT.
Pure functions; nothing calls them yet. Mock, dev.

| check | rule | measured | result |
|---|---|---|---|
| Phase 2 all-on dev run unchanged | 0 differences, prompt log byte-identical vs the `a672af7` run | 0 differences, summary 0; `cmp` identical | PASS |
| tests | green, mutation-checked | `tests/test_sides.py` 8 passed; full suite 284 (276 before); 13 / 13 mutations caught (one after strengthening the other-side test) | PASS |

#### Deviations from commit 0, recorded
1. **Case-side list (erratum).** Commit 0 listed RCA-14 and RCA-18 as heat-only. Under the rule written in the same
   section (a case belongs to the sides of its MOVING triples; `steam_flow` belongs to both sides) they move
   `steam_flow` and so go to both. The list had been computed by a quick script that left `steam_flow` out of the
   water side. The rule is followed: water only RCA-01; heat only RCA-03, 07, 13; both RCA-04, 05, 11, 14, 16, 18
   and the all-FLAT RCA-09, 10, 15. Nothing had been measured, so no number depends on the old list.
2. **BALANCE rule changed during the build (a bug found by a test, not a tuning choice).** Commit 0 said "a
   BALANCE fact naming feed and steam flow is water; the other BALANCE facts are heat" (the split
   `world_model._fact_ids` uses). L1 also emits "water balance SUSPENDED - untrusted tag(s): drum_level", tagged
   only with the untrusted water tags, which that rule sent to heat. The rule is now: a BALANCE fact (or finding)
   is water when ALL its tags lie in L1's water-balance tags `{feed_water_flow, steam_flow, drum_level}`
   (`l1_checks._balances`, `water_tags`), otherwise heat. **Open item for `main` (look only, not changed):**
   `world_model._fact_ids` maps the same SUSPENDED fact to `energy_balance`, so a case expecting an energy-balance
   triple can be given it as support.

3. **PATTERN rule added after the phase review (a bug, not a tuning choice).** L1's three PATTERN facts list the
   context tags their condition reads, not only the side they describe (`l1_checks._patterns`): "energy
   accumulating in bed" (bed, steam, pressure) and "water side only" (level, bed) were both put on both sides by the
   tag rule. They now take the side they name, from a table cited to `l1_checks._patterns` (heat, water; "heat
   side steady" heat); an unknown pattern falls back to its tags, and a test fails if L1 emits a pattern the table
   does not hold. The same review also led to: the fingerprint counts only the side's own checked note-facts and
   includes each open finding's severity (an escalation is a change); `primary_side` counts open findings; the
   other-side line is cut at the fact's first ':' (as `l1_symbolize.headline` does), so the long water-balance
   detail no longer overruns the plan's ~30-token line; ids order F2 before F10; a FLAT triple in the weighted
   spelling (`"tag|FLAT|-"`) is not movement.

| check (after the review fixes) | measured |
|---|---|
| tests | `tests/test_sides.py` 10 passed; full suite 286 passed |
| mutations | 23 / 23 caught (`PYTHONDONTWRITEBYTECODE=1`, `__pycache__` cleared each time) |
| Phase 2 all-on dev run | unchanged (nothing calls `sides.py`; the run above stands) |

#### Finding: on dev, cross-side ticks are rare, and a tube leak almost never moves the heat side
Non-QUIET dev ticks by active side, from the facts alone (findings are not in the run file), with the corrected rules:

| family | water only | both | heat only |
|---|---|---|---|
| A (water-side control) | 364 | 0 | 1 |
| **B (tube leak)** | **602** | **3** | 0 |
| C (coal) | 6 | 20 | 385 |
| D (primary air / CV) | 0 | 0 | 590 |
| E (fouling drift) | 0 | 0 | 1 |
| N (normal) | 0 | 0 | 10 |
| all | 972 | **23** | 987 |

**Correction of record:** a first version of this table (before the PATTERN fix) gave 291 "both" ticks, mostly in
families A and D, and this report briefly said the cross-side results would be read on those ticks. 268 of the 291
were artefacts of the PATTERN mis-siding (the "water side only" pattern made the heat side active, and "energy
accumulating" made the water side active through `steam_flow`). The phase reviewer found it; the corrected count is
23.

What this means, recorded, not resolved: the plan's case for the split ("when both sides move, as in a tube leak,
both run at the same time", p.6) has 23 dev ticks to show on (20 in family C, 3 in family B). Most ticks need one
side only, which is the plan's "common case" (one short call), so the split's main effect on these episodes is
shorter prompts per call, not parallel calls. The family-B cross-side check that PROMPTS.md asks for would rest on 3
ticks. Whether the simulated tube leak should cool the bed enough for L1 to see it, or L1's heat checks are too
coarse, is a question for the advisor. No data, threshold or check was changed.

#### Phase-reviewer findings (commit 1) and what was done
| finding | done |
|---|---|
| PATTERN facts sided by their context tags; ~268 of 291 "both" ticks were artefacts | **fixed** (deviation 3), tested, mutation-checked; table corrected above |
| the report's first cross-side statement rested on that artefact | **corrected** above |
| `all(fact_sides(f))` on dev facts cannot fail; the "tube leak moves both" test passed on a heat-ACCUMULATING fact, the opposite of a cooling bed | **replaced** by a test that every dev PATTERN tag set is in the table and that "water side only" never activates heat |
| fingerprint's `resolved` filter untested; note-fact ids not filtered by side; escalation not a change | **fixed and tested** |
| `primary_side` ignored findings; other-side line uncapped; ids ordered as text; weighted FLAT counted as moving | **fixed and tested** |
| RCA-14 / RCA-18 (fuel-cap faults where steam falls as a consequence) go to both sides only because `steam_flow` is a water tag | **recorded**: showing them to both is the inclusive reading of "steam flow as load", not a statement of the plan's intent |
| most note-facts name equipment only, so they go to both sides | **recorded**: the side filter on note-facts does little on this vocabulary |
| `world_model._fact_ids` maps the SUSPENDED water fact to `energy_balance` | already an open item for `main` (deviation 2) |

## Commit 2: two side diagnosticians (`multi.split`). KEPT.
Mock, dev (36 episodes, 5,850 ticks), lockstep, fixed placement, every compact section on. **The mock does no
reasoning: none of the numbers below is an accuracy result.**

What was built: `DiagnosticianAgent.make_jobs` makes one job per active side (`diag_water` -> NPU, `diag_heat` ->
CPU); each side's prompt is the Phase 2 compact prompt restricted to the side (its facts, at most 8 lines; its cases
from the shared retrieval; its raw notes or note-facts; record-facts to both), with a side header and one
code-written line about the other side (`compact.build_diagnosis` gained three optional inputs whose defaults leave
the Phase 2 prompt unchanged). The gate's `fold_sides` checks each side's answer on its own (expansion, staleness,
failed call, invented citations by the single agent's rule), combines the accepted ones
(`compact.combine_side_payloads`, decision 1) and folds them once through the single agent's `fold_diagnosis` /
`merge`, unchanged. The verifier is submitted when the last side answer is in. `run_demo.py --split`.

| check | rule | measured | result |
|---|---|---|---|
| split off vs Phase 2 all-on (`a672af7`) | 0 differences, byte-identical prompts | 0 differences, summary 0; `cmp` identical | PASS |
| split on: deterministic layer | `state`, `triage`, `facts`, `belief_ranking`, `belief_supports` identical | 0 of 5,850 ticks differ | PASS |
| split on: side prompts hold only their side | checked on every call | 2,005 side jobs; facts off-side 0, cases off-side 0 | PASS |
| split on: tokens | prompt + 60 < 1,280, four tokenizers | max 915 / 927 / 951 / 944 (Llama / Qwen3 / Gemma / Qwen2.5); 0 over | PASS |
| split on: health | 0 | parse failures 0; out-of-range case / fact / x / note lines 0; guard and over-limit 0 | PASS |
| timing | S7 0 | 0.0 | PASS |
| tests | green, mutation-checked | `tests/test_split.py` 15 passed; full suite 301 (286 before); 21 / 21 mutations caught (6 added after the review) | PASS |

Reported, not gated (independent check: diagnosis jobs 2,005 = 1,982 Phase 2 calls + the 23 cross-side ticks of
the commit 1 table, relative difference 0):

| quantity | Phase 2 all-on | split |
|---|---|---|
| diagnosis calls | 1,982 | 2,005 (water 995, heat 1,010) |
| model calls per non-QUIET tick | — | mean 1.17, max 2 |
| verifier calls | 327 | 319 |
| side prompt tokens, mean (Llama / Qwen3 / Gemma / Qwen2.5) | 749.7 / 755.0 / 772.9 / 772.0 | 755.9 / 760.6 / 777.6 / 777.6 |
| **SIMULATED** diagnosis latency, mean / p95 (placeholder lane rates, an expectation, not a result) | 2.10 / 2.28 s | 3.90 / 6.40 s (water on NPU 1.99 s, heat on CPU 5.73 s) |
| mock library top-1 / group top-1 | 0.434 / 0.489 | 0.503 / 0.562 |
| mock group top-1 by family A / B / C / D | 0.250 / 0.668 / 0.397 / 0.819 | 0.268 / 0.885 / 0.397 / 0.819 |

**Causes, tested:**
- **Mock top-1 rises because the side filter changes which cases the mock ranks, not because anything diagnoses
  better. Mock: a retrieval-layer effect of the side case filter; not an accuracy result; not attributable to two
  calls versus one** (the same filter on one unsplit prompt would very likely give the same rise; that control was
  not run). The reviewer re-scored it against ground truth: of the 295 changed ticks, 109 went wrong to right, 0
  right to wrong, 186 wrong to wrong; the true case was never removed from every side's view. The mock ranks the top 3 cases it is SHOWN by retrieval score. Of the 295 ticks whose rank 1 changed,
  290 are ticks where Phase 2's rank-1 case was not shown to any active side (a case that moves only the other
  side is filtered out); the other 5 are cross-side ticks (B03 141 to 143, C04 77 and 81) where the two sides tie
  on severity and water goes first (decision 1). A real model's accuracy on the split is unmeasured; this number
  must not be quoted as a gain.
- **Verifier calls 327 -> 319**: the verifier trigger reads rank 1's confidence, and rank 1 changed on 295 ticks.
- **Simulated latency rises**: under fixed placement every heat-only tick (about half the ticks, commit 1) runs on
  the CPU lane, whose placeholder prefill rate is 126 tok/s against the NPU's 909; heat jobs average 5.73 s against
  water's 1.99 s. On these episodes the split mostly replaces one NPU call by one CPU call. This is the plan's own
  estimate shape (heat diagnostician on the CPU ~7.0 s, p.15) applied to a dataset with almost no cross-side ticks.
  Open item for Phase 5: under "earliest finish" a heat-only tick would take the idle NPU.
- **Side prompts are not smaller than the unsplit one**: the cases (up to 4) and the record-facts dominate the
  prompt and are not split; the side header and the other-side line add about 6 tokens on average. The plan's
  ~700 estimate assumed a smaller case list per side.

#### Decisions taken (conservative option)
1. **The per-side citation rule is the single agent's** (`min_faithfulness` 0.5 on that side's answer); a rejected
   side is recorded in `unexplained` and the other side still counts. The combined fold then re-checks the union
   with the same rule (it cannot fall below 0.5 when every accepted side is at or above it).
2. **A failed side marks the tick degraded** (`llm_<status>`), as a failed call did in Phase 2, even when the
   other side answered.
3. **Raw notes and note-facts are side-filtered by one rule each, never both**: with note-facts on, a note-fact
   goes by its own subjects (the commit 0 design); with the notes section off (the ablation), a raw note goes by its
   metadata tags. A first version filtered note-facts by both, which the review caught (below).
4. **Envelopes of both sides keep `agent: diagnostician`**; the side is in the run file's `multi` telemetry, so
   the bench tools (`bench/model_choice.py` filters on that name) read them unchanged.

#### Phase-reviewer findings (commit 2) and what was done
The reviewer ran the suite (297 passed), probed the gate with hand-built answers and ran monkeypatch mutations.
Checklist: single agent untouched PASS, shared code imported PASS (one copied rule, below), hard path PASS, single
writer PASS, answers reach belief only through the gate PASS, tick stamps PASS in code / untested, token caps PASS,
no tuning PASS, tests PARTIAL FAIL, metric causes PASS.

| finding | done |
|---|---|
| 1. The combined fold re-checked citations on the de-duplicated union, so two side answers that each passed (water [F1, line7] 0.5, heat [F1, line9] 0.5) failed together (union 0.33) and the tick fell back to belief while telemetry logged a merge. Cannot happen on the mock; would on the board, where shared `steam_flow` facts are common | **fixed**: after the per-side checks the combined payload goes to the single agent's `merge` directly (no second check); tested with the reviewer's example, mutation caught. Dev: 0 decision differences against the first version |
| 2. The mock top-1 rise is easy to misread as a gain from the split | **report wording fixed** (causes above) |
| 3. Note-facts were filtered by the raw note's tags AND by their own subjects (not in the design); `note_sides`, the cross-tick path, records to both sides and the tie rule were untested | **fixed and tested** (decision 3); 6 mutations added, all caught. On dev the fix changed 0 of 2,005 prompts: the mock text reader echoes a note's metadata tags as its subjects, so the two filters agree on the mock; with a real reader they can differ |
| the per-side citation rule is a second copy of the single agent's (`fold_diagnosis`) | **recorded**: it is three lines and the single agent's function cannot check one side without also merging it; a test fails if a side with invented citations is let through |
| `merge` keeps at most 6 supports per case, so a case named by both sides can lose the second side's citations | **recorded** (single-agent code) |
| "degraded" now also marks a tick where one side failed and the other answered | **recorded** (decision 2) |
| the merge telemetry said `evidence_tick: now` | **fixed**: it records `now_tick` and each answer's evidence tick |
| the guard covers only the fully compact prompt, while `split` needs only `schema` | **recorded**: a partial-compact split prompt over the limit is flagged (`over_limit_unguarded`), not cut, as in Phase 2 |
| "0 differences" with split off: the run file's `multi` telemetry gains `side`, `fact_ids`, `case_ids` | **recorded**: the comparator excludes the `multi` telemetry by name (Phase 1 rule); decisions, summaries and prompts are what is compared |

## Commit 3: call a side only when its evidence changed (`multi.split_on_change`). KEPT.
Mock, dev, lockstep, fixed placement, every compact section on, split on. **Not an accuracy result.**

What was built: a board section `side_answers` (owner: gate) holds each side's last ACCEPTED answer, its fact ids
stamped with its evidence tick, and the fingerprint of the evidence it answered (`sides.side_fingerprint`: the
side's signature triples, its open findings with their severity, its checked note-facts, and **the cases it is
shown**, added after the phase review). `make_jobs` makes no job for a side whose fingerprint equals its cached
one; `fold_sides(reuse=True)` re-uses that answer, drops the entry of a side no longer to run or whose new answer
failed, and caches each newly accepted one. `llm_invoked` is True when any model (diagnosis or verifier) was called
that tick. `run_demo.py --split --on-change`. `bench/evaluator.py` Q3 now checks a stamped citation against the
facts of the tick it names (review fix; single-agent and Phase 2 scores unchanged, below).

| check | rule | measured (after the review fixes) | result |
|---|---|---|---|
| switch off vs commit 2 | 0 differences, prompts byte-identical | 0 differences, summary 0; `cmp` identical (measured before the fixes, which touch only the switch-on path and the evaluator) | PASS |
| evaluator change | single-agent and Phase 2 scores unchanged | single dev and Phase 2 all-on dev re-run with the new evaluator: 0 differences in runs and summaries against `a672af7` | PASS |
| deterministic layer vs Phase 2 | identical | 0 ticks differ | PASS |
| no job on unchanged evidence with a cached answer (commit 0 prediction) | 0 such jobs | side jobs by cache state at creation: none 34, changed 1,015, **same 0** | PASS |
| health | 0 | parse failures 0; out-of-range lines 0 | PASS |
| timing | S7 0 | 0.0 | PASS |
| tests | green, mutation-checked | `tests/test_on_change.py` 8 passed; full suite 310; 16 / 16 mutations caught (one after extending the episode test to dev_B01) | PASS |

| quantity (reported) | Phase 2 | split (commit 2) | split + on change |
|---|---|---|---|
| diagnosis calls | 1,982 | 2,005 | **1,049** |
| model calls per non-QUIET tick, mean / max | — | 1.17 / 2 | 0.69 / 2 |
| non-QUIET ticks without a diagnosis call | 0 | 0 | 946 of 1,982 |
| re-used side answers | — | — | water 482, heat 474 |
| verifier calls | 327 | 319 | 318 |
| S4 (share of ticks with a model call) | 0.337 | 0.337 | 0.203 |
| Q3 faithfulness / Q3_rel | 1.0 / 0.573 | 1.0 / 0.592 | 1.0 / 0.590 |
| mock library top-1 / group top-1 | 0.434 / 0.489 | 0.503 / 0.562 | 0.503 / 0.572 |

Q3_rel is computed from fresh model answers only (a re-used answer has no envelope; it was scored when it was
fresh), so its sample shrinks with the call count.

**Causes, tested.** Against commit 2, hypotheses differ only on ticks that re-used an answer (950 ticks):
- 2,559 citation fields differ by the tick stamp alone (`t80.F1` for `F1`); 51 cite other facts (a fresh answer cites
  this tick's facts);
- 665 `confidence` (and 128 `confidence_shown`) values differ: a re-used answer carries the confidence belief had
  when its prompt was built (amendment 2 option (ii) at build time), not today's;
- 17 ticks differ in order: on all 17 the re-used side was shown the SAME cases in a different retrieval-score
  order (checked tick by tick). The fingerprint compares the case SET; the mock re-ranks by score. A real model's
  order is not tied to the score order, so the set is kept.

#### Deviation from commit 0, recorded
**The side-aware stale rule is not built.** Commit 0 listed it in commit 3 (the plan: an answer more than one tick
old is accepted "if that side's evidence hasn't changed since"). In lockstep every answer is folded in its own tick,
so the rule cannot be reached, and the board keeps the facts of the last 2 ticks only (`FactsBook(keep=2)`), so an
older answer's citations could not be checked anyway. It belongs to real-time mode: deferred to Phase 4, where the
fact history must grow for it.

#### Phase-reviewer findings (commit 3) and what was done
The reviewer ran the suite (308 passed) and its own measurements on 6 dev episodes. Checklist: single agent
untouched PASS, shared code PASS, hard path PASS, single writer PASS, belief PASS (re-used answers skipped today's
checks), tick stamps PARTIAL, token caps PASS, no tuning PASS, tests PARTIAL, metric causes **FAIL**.

| finding | done |
|---|---|
| 1. **Q3 faithfulness collapsed with the switch on (C01 1.0 -> 0.48, B01 -> 0.155) and was not reported**: the evaluator scored re-used stamped citations (`t80.F1`) against the current tick's facts | **fixed in the evaluator**: a stamped citation is valid if that fact existed at the tick it names (the run file holds every tick's facts); single-agent and Phase 2 scores re-measured unchanged; Q3 is 1.0 with the switch on. Tested, mutation caught. The first version of this section would have quoted numbers without this; it was rewritten |
| 2. **A re-used answer could name cases retrieval no longer returns** (rank-1/2 outside retrieval 3 -> 102 on 6 episodes; `approve` dropped their actions). The reviewer judged it blocking | **fixed**: the fingerprint includes the cases the side is shown, so a side whose case list changed is asked again (calls 764 -> 1,049). Tested on dev: every published model hypothesis on a re-use tick names a case shown that tick. The open decision first recorded here is settled by this |
| 3. **`llm_invoked` became False on ticks where only the verifier was called** (53 on B01), changing S4's meaning | **fixed**: True when any model was called that tick; S4 reported above |
| the episode test asserted an equality that held only by coincidence on C01; the re-used answer's evidence tick was untested (a mutation survived) | **fixed**: set inclusion (no-call ticks within re-use ticks), run on C01 and B01; evidence-tick test added; both mutation-checked |
| re-used answers are published up to ~19 ticks after their evidence tick, while the board keeps 2 ticks of facts | **recorded**: their citations name their tick (stamped) and are checked against the run file's facts of that tick by Q3; the board copy is not needed after the answer was checked |
| the fingerprint still omits the other-side line, belief top 3, record-facts and triage level | **recorded**: belief values move almost every tick, so including them would end re-use; record-facts change only with time windows; the case list was the input that changed published decisions |
| a side's cache survives QUIET ticks (`fold_sides` does not run on them) | **recorded**: the fingerprint then guards it (findings resolve on quiet ticks, which changes it) |
| the gate imported the diagnostician inside a function | **fixed**: module-level import |

## Commit 4: known-wrong-answer verifier tool (`bench/verifier_wrong_answer.py`). KEPT.
What it does (PROMPTS.md session 5: "feed the verifier one episode where rank 1 is known to be wrong and show whether
it disagrees"): a backend wrapper between the model and the agent rewrites each diagnostician answer so that rank 1
is a case SHOWN in the prompt but OUTSIDE the episode's true look-alike group (the model's rank-1 citations are
kept); the verifier is forced to `always`; a control run has no rewrite. It reports, per episode, how often the
verifier failed the rank-1 claim (detection; false alarm in the control), passed it, or left it not judged, and how
often the known-wrong case was still published at rank 1. `run_episode` gained an optional `wrap_backend`
(None leaves it unchanged; the full suite and the identity tests pass). The agent, gate and verifier are unchanged.

**Mock run (dev, split on). The mock verifier always agrees, so detection 0 is by construction; this proves the
plumbing, not the verifier.**

| episode | forced answers (calls) | not rewritten | verifier answered (ticks; all post-onset) | detected | passed | not judged | rank 1 outside the true group | control false alarms |
|---|---|---|---|---|---|---|---|---|
| dev_A03_bfp_suction | 73 | 20 (no shown case outside the true group) | 87 | 0 | 87 | 0 | 70 | 0 of 87 |
| dev_B01_tube_leak | 128 | 0 | 128 | 0 | 128 | 0 | 128 | 0 of 128 |
| dev_C05_low_cv_coal | 134 | 5 (same) | 119 | 0 | 119 | 0 | 118 | 0 of 119 |

Failed verifier calls: 0 on each. With a verifier scripted to fail ONLY the rank-1 claim, detection is 1.0; failing
only the rank-2 claim, it is 0 (both tested), so the tool reads the right claim. Tests:
`tests/test_verifier_wrong_answer.py` 11 passed; full suite 321; 13 / 13 mutations caught.

#### Findings, recorded (for the board run and for the plan's open question "is the verifier worth keeping?")
1. **The verifier never runs at WATCH**, even in `always` mode (the single agent's rule: QUIET and WATCH ticks are
   not verified). dev_A01 and dev_C01, for example, reach only WATCH, so the test needs episodes that reach
   INVESTIGATE or URGENT; its sample is those ticks only.
2. **A verifier that disagrees does not change the ranking.** `Verifier.apply` (single agent, unchanged) caps a
   failed claim's confidence at 0.35 and flags it; it never moves it down. So even a perfect verifier would leave a
   known-wrong case published at rank 1 (on the mock it stayed rank 1 on 70 to 128 forced ticks per episode, which
   is the rewrite plus an always-agreeing verifier). What a disagreement changes is the shown confidence, the
   `verifier` flag and `unexplained`. Whether that is enough is a question for the board result and the human.
3. The tool forces `split_on_change` off: with it on, a re-used side answer would be an earlier REWRITTEN answer
   taken from the cache with no new call.

#### Phase-reviewer findings (commit 4) and what was done
| finding | done |
|---|---|
| 1. A malformed answer (`{"r":[[1]]}`, `{"r":[5,...]}`) crashed the whole run; an out-of-range answer was "repaired" into a valid one, skewing the forced run's parse and citation counts | **fixed**: only an answer that passes the gate's own shape check with every case line in range is rewritten; anything else goes through untouched and is counted (`not_rewritten`); 5 malformed shapes tested |
| 2. The scripted verifier failed every claim, so the test could not tell rank 1 from rank 2 | **fixed**: fail only claim 1 -> detection 1.0; fail only claim 2 -> 0 |
| 3. Denominators: forced calls reported as ticks; failed verifier calls dropped silently; pre-onset ticks counted (T2 scores from onset); "model idea" claims and the control's own misses called "wrong still rank 1" | **fixed**: `forced_answers` (calls) and `verifier_ran` (ticks) reported separately; `verifier_call_failed` counted; a `post_onset` subset with the evaluator's key (`fault_onset_t`) and rule (`tick x 30 >= onset`); `rank1_outside_true_group` counts real case ids only. A first version read the onset from a wrong key and returned 0; an episode-level assertion now fails if it does |
| a control run that was rewritten by mistake would still report 0 rewrites | **fixed**: rewrites are counted from the wrapper's own log (a mutation caught it) |
| `split_on_change` would re-use rewritten answers | **fixed**: the tool forces it off |
| in real time a stale verdict would be counted against the current rank 1 (`apply_verdict` records the envelope before the stale check) | **recorded** for Phase 4 |
| with both `wrap_backend` and `record_prompts`, the recorder logs the rewritten text as the model's answer | **recorded**: the tool does not use the recorder |
| RCA-02 has no look-alike group, so every other shown case counts as wrong for it | **recorded**: correct for a case without look-alikes |

Board command (after Phase 2's real-model numbers): `bench/verifier_wrong_answer.py --backend llamaserver --split
--episodes dev_A03_bfp_suction,dev_B01_tube_leak,dev_C05_low_cv_coal --out results/board_multi/verifier_wrong.json`.

## Commit 5: the mock dev run and the Phase 3 summary
Code at `e379c42` (agent code unchanged since commit 3). Dev, 36 episodes, 5,850 ticks, mock, lockstep, fixed
placement, laptop. Runs made one after another (not in parallel). **Mock: none of this is an accuracy result.**

| check (commit 0 rules) | measured | result |
|---|---|---|
| single agent unchanged | `--arch single` vs the run at `fa6ee08`: 0 differences, summary 0; prompt log sha256 `6e9b748a` (the Phase 1 value); `git diff multi-phase1 -- fieldmind/agent` empty | PASS |
| Phase 3 switches off | multi, every compact section on, split off vs the Phase 2 all-on run at `a672af7`: 0 differences, prompt log byte-identical | PASS |
| deterministic layer with split (and on change) | 0 ticks differ from Phase 2 (commits 2 and 3) | PASS |
| side prompts hold only their side | 0 off-side facts or cases (commit 2) | PASS |
| tokens, every agent, four tokenizers | split + on change: diagnostician max 915 / 927 / 951 / 944 (+60), verifier max 524 / 512 / 518 / 529 (+30), text reader max 414 / 392 / 413 / 409 (+50): 0 over | PASS |
| health | parse failures 0, invalid verifier answers 0, out-of-range lines 0, guard firings 0 | PASS |
| S7 | 0.0 in every run | PASS |
| P0 over 200 ms (laptop) | **2 ticks (205, 335 ms, dev_D01 ticks 187 and 195) in the first split run**; cause tested: that run overlapped the commit-4 reviewer's test suite on this laptop; the split run re-made alone gives 0 over 200 ms (max 64.0 ms, p95 29.0) with identical decisions. Phase 2 run max 118.3 ms, split + on change max 102.0 ms | PASS (on the solo run) |
| tests | full suite 321 passed; every new test mutation-checked (commit 1: 23, 2: 21, 3: 16, 4: 13; all caught) | PASS |

### Phase 3 numbers beside Phase 2 (mock; reported, not gated)
| quantity | Phase 2 (one diagnostician) | split | split + on change |
|---|---|---|---|
| diagnosis calls | 1,982 | 2,005 | **1,049** |
| verifier calls | 327 | 319 | 318 |
| model calls per non-QUIET tick (all agents) | 1.16 | 1.17 | **0.69** |
| S4 (share of ticks with a model call) | 0.337 | 0.337 | 0.203 |
| diagnosis prompt tokens, mean, Llama 3.2 | 749.7 | 755.9 | 753.2 |
| **SIMULATED** diagnosis latency, mean / p95 (placeholder lane rates: an expectation, not a result) | 2.10 / 2.28 s | 3.90 / 6.40 s | 3.97 / 6.49 s |
| Q3 / Q3_rel | 1.0 / 0.573 | 1.0 / 0.592 | 1.0 / 0.590 |
| mock library top-1 / group top-1 | 0.434 / 0.489 | 0.503 / 0.562 | 0.503 / 0.572 |
| mock group top-1, post-onset, ticks with both sides active (n = 23) | 0.652 | 0.783 | 0.783 |
| mock group top-1, post-onset, ticks with one side active (n = 1,304) | 0.580 | 0.665 | 0.670 |

Every mock top-1 rise is the side case filter seen by a mock that re-ranks the cases it is shown (commit 2, cause
tested); it is not an accuracy result and not attributable to two calls versus one. The cross-side subset has 23
ticks.

### What Phase 3 shows on the mock, and what only the board can show
- **Built and checked:** two side diagnosticians with side-pure prompts; one merge through the unchanged single-agent
  `merge`; the deterministic layer untouched; calls only on changed evidence (diagnosis calls -48% against the split,
  -47% against Phase 2); a known-wrong-answer verifier tool.
- **Not shown, and not showable on these episodes:** the plan's parallel-lane speed-up. Cross-side ticks are 23 of
  1,982 (advisor question A1), and under fixed placement every heat-only tick runs on the slower CPU lane, so the
  SIMULATED diagnosis latency rises (2.10 -> 3.97 s mean). "Earliest finish" placement (Phase 5) would send a
  heat-only tick to the idle NPU.
- **Needs the board, after Phase 2's real-model numbers are accepted** (the condition of the human decision to start
  early): the plan's check (group top-1 at least Phase 2's, overall and on cross-side ticks); real diagnosis latency
  and lane use; the known-wrong-answer test (`bench/verifier_wrong_answer.py --backend llamaserver --split ...`).
- **Not run:** the 30 reporting episodes. Phase 2's own reporting run is still waiting on its G1a decision, and a
  Phase 3 reporting run belongs after the board check.

### Open items and decisions for the human (none acted on)
1. Advisor questions A1 (cross-side faults rare; the simulated tube leak does not visibly cool the bed) and A2
   (fuel-cap cases shown to both sides).
2. Placement for the split: fixed (today) puts heat-only ticks on the CPU lane; compare with earliest finish in
   Phase 5, or decide now.
3. A verifier that disagrees only caps confidence and never reorders (commit 4 finding 2): is that the intended
   role? (The plan's open question "is the verifier worth keeping?")
4. Deferred to Phase 4: the side-aware stale rule (needs a longer fact history); stale verdicts counted by the
   wrong-answer tool in real time.
5. Open for `main` (look only): `world_model._fact_ids` maps the SUSPENDED water fact to `energy_balance`;
   `merge` publishes half-invented citations (Phase 2 item).

---

## Questions for the advisor (Prof. Shukla), to verify later
Recorded on 2026-10-05; nothing below has been acted on. Mock numbers; they describe the dataset and the
deterministic checks, not a model.

### A1. Cross-side faults are almost absent from the data, and the simulated tube leak does not cool the bed visibly
**What was found.** On the 36 dev episodes, 23 of 1,982 non-QUIET ticks have both plant sides active (20 in
family C, 3 in family B); 972 are water only and 987 heat only (table under "Commit 1"). During a tube leak
(family B), L1 reports the heat side as steady on 602 of 605 ticks.

**Why it matters.**
- The plan's case for splitting the diagnostician (p.6, and the ~19 s -> ~10 s example on p.15) is a fault that
  moves both sides, with the NPU and the CPU working at once. With 23 such ticks the parallel-lane gain can barely
  be shown; on these episodes the split's benefit is mainly shorter prompts per call.
- The planned Phase 3 check "accuracy on faults that move both sides (family B)" would rest on 3 ticks.
- The scheduling study (Phase 5) compares ways of keeping both lanes busy; if most ticks need one side, the
  differences between policies may be small.
- The project's own physics note (CLAUDE.md, sign discipline) says a tube leak puts water into the furnace and the
  **bed cools**. If L1 almost never sees it, either the simulator's cooling is too weak for L1's thresholds, or the
  heat-side checks are too coarse. Either way the synthetic tube leak may not behave like the real one in
  `docs/Boiler_Failure_Case_Studies_RCA.pdf`.

**Options (none chosen):**
1. Accept it as a property of the dataset and report it: "cross-side faults are rare in these episodes, so the
   speed gain comes mainly from shorter prompts".
2. Check the simulator's tube-leak heat effect against the case-study PDF and the physics (direction and size of
   the bed cooling). If it is wrong, fix it under a pre-registered rule. This is a data change: every baseline
   (single v3, the board campaign, Phase 1 and 2) would have to be re-run.
3. Add a small number of episodes whose fault moves both sides, built from the case library, so the parallel-lane
   claim can be tested, without changing the existing 66 episodes.

**Questions:** Is option 1 acceptable for the writeup? If not, which of 2 or 3, and does the advisor know whether a
real AFBC tube leak shows on bed temperature within the first minutes?

### A2. Fuel-cap faults (RCA-14, RCA-18) are shown to both side diagnosticians
They move `steam_flow` (steam falls as a consequence of the fuel cap), and steam flow belongs to both sides (the
plan's "steam flow as load"), so the water-side diagnostician also sees them. This is the inclusive reading. The
alternative is to treat `steam_flow` movement in a case signature as heat-only. **Question:** which reading does
the advisor prefer for the writeup?
