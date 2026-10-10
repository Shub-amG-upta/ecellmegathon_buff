# Multi-agent Phase 1: plumbing: report

## Status
DONE. Phase 1 plumbing is built and passes the gate on the dev set and on the one-shot reporting run. Reviewer findings were fixed
and re-gated. Tagged `multi-phase1`.

**Every number below comes from the mock backend.** The mock backend re-ranks retrieved cases and does no reasoning, so these
figures measure the deterministic and retrieval layers only. They are not an agent result. All timings are **laptop timings**
(Apple M5, agent code on the laptop, no board), not board timings.

## Close-out (after acceptance; report only, no code changes; on top of tag `multi-phase1`)

### 1. Timings: audit state, what each one spans, and the single agent beside it
All laptop timings (Apple M5), mock backend, in ms. "Audit" is `multi.audit_writes`, the fingerprinting of every blackboard
section around each agent step.

**What each number spans.**
- **Deterministic-assessment time (P0)**: from the start of `MultiOrchestrator.tick` (before `gate.begin_tick`) to the moment
  the gate's deterministic assessment exists.
  - On a non-QUIET tick that covers: status reset, sensor (L1 checks, trust, findings, baselines), triage, opening the
    assessment, signature, retrieval, belief update, and the gate's `approve` on belief's claims.
  - On a QUIET tick it covers the whole tick, up to the gate publishing.
  - With the audit on, it also includes the audit's fingerprinting around each of those steps.
  - It does not include the model jobs, folding their answers, or the final approve and assembly.
- **Hard-stage time**: the single agent's own `hard_ms`, i.e. the L1 check run plus the L6 `approve` call. This is what its
  S7 deadline test uses. The multi path computes the same two timers (`multi.hard_ms`); the audit's fingerprinting falls
  outside both timers, so it is not counted in either arch's S7.
- **Plumbing overhead**: per-tick `tick_latency_ms` of multi minus single, paired by tick, whole tick.

**Dev, 5,850 ticks (1,982 non-QUIET).**

| quantity | audit | mean | p50 | p95 | max | over 200 ms |
|---|---|---|---|---|---|---|
| single: hard-stage time (L1+L6), all ticks | n/a | 0.276 | 0.252 | 0.436 | 0.778 | 0 |
| single: hard-stage time, non-QUIET ticks | n/a | 0.317 | 0.344 | 0.442 | 0.778 | 0 |
| single: whole tick | n/a | 0.416 | 0.457 | 0.771 | 1.138 | 0 |
| multi: hard-stage time (same two timers), non-QUIET, rep 1 / rep 2 | off | 0.315 / 0.314 | 0.319 / 0.299 | 0.437 / 0.441 | 1.96 / 1.76 | 0 |
| multi: hard-stage time, non-QUIET, rep 1 / rep 2 | on | 0.347 / 0.324 | 0.360 / 0.301 | 0.466 / 0.461 | 23.4 / 0.587 | 0 |
| multi: P0 deterministic-assessment time, all ticks, rep 1 / rep 2 | off | 0.376 / 0.377 | 0.377 / 0.376 | 0.644 / 0.646 | 2.20 / 2.01 | **0 / 0** |
| multi: P0 deterministic-assessment time, all ticks, rep 1 / rep 2 | on | 5.04 / 5.04 | 3.25 / 3.29 | 15.4 / 15.4 | 100.5 / 27.6 | **0 / 0** |
| multi: P0, gate run (also `--log-prompts`) | on | 6.13 | 4.14 | 17.1 | 29.3 | **0** |
| plumbing overhead (multi − single, whole tick), rep 1 / rep 2 | off | 0.020 / 0.005 | 0.012 / 0.008 | 0.060 / 0.059 | 1.90 / 1.64 | n/a |
| plumbing overhead, rep 1 / rep 2 | on | 6.36 / 6.33 | 2.65 / 2.68 | 22.0 / 22.1 | 121.2 / 50.6 | n/a |

- **The 200 ms check was measured with the audit on as well as off.** P0 over 200 ms: 0 of 5,850 ticks in two audit-off runs
  and in three audit-on dev runs (two timing repetitions plus the gate run). S7 deadline misses: 0 in every run, both arches.
  No new run was needed for this.
- The reporting run (5,160 ticks) ran with the **audit on**: P0 mean 7.12, p50 4.90, p95 21.2, max 34.3; over 200 ms: 0.
- Earlier tables in this report that say "P0 never exceeded 200 ms" without an audit state refer to both states.
- The single-arch hard-stage row was measured for this close-out by timing the single agent's `CheckLayer.run` and
  `Gate.approve` from a scratch script (one dev run, nothing in the repo changed). The single agent does not write its
  `hard_ms` to the run file.
- **Reproducibility (added in Phase 2, commit 1).** That script is now in the repo, logic unchanged:
  `.venv/bin/python bench/time_single_hard.py` (dev set, mock, laptop timing). Two re-runs on 2026-10-03:

  | single, dev, 5,850 ticks | mean | p50 | p95 | max | over 200 ms |
  |---|---|---|---|---|---|
  | hard-stage, all ticks: original | 0.276 | 0.252 | 0.436 | 0.778 | 0 |
  | hard-stage, all ticks: re-run 1 / 2 | 0.299 / 0.290 | 0.256 / 0.257 | 0.519 / 0.463 | 1.808 / 1.609 | 0 / 0 |
  | hard-stage, non-QUIET: original | 0.317 | 0.344 | 0.442 | 0.778 | 0 |
  | hard-stage, non-QUIET: re-run 1 / 2 | 0.355 / 0.320 | 0.346 / 0.342 | 0.651 / 0.451 | 1.808 / 1.264 | 0 / 0 |
  | whole tick: original | 0.416 | 0.457 | 0.771 | 1.138 | 0 |
  | whole tick: re-run 1 / 2 | 0.460 / 0.434 | 0.460 / 0.462 | 0.903 / 0.778 | 5.918 / 2.229 | 0 / 0 |

  Mean and p50 reproduce within 8% and 2%. **The maximum does not reproduce**: 0.778 originally against 1.808 and
  1.609 (2.3x and 2.1x). It is one tick in 5,850 on a laptop running other work; the cause was not investigated, so it
  is recorded as unexplained. Every value is under 1% of the 200 ms budget, and the count over 200 ms is 0 in all
  three runs. Wall-clock timings do not reproduce to the digit; the original row stands as measured.
- Unexplained: the one 23.4 ms multi hard-stage maximum (audit on, rep 1; rep 2's maximum is 0.587). The two timers do not
  include fingerprinting. Not investigated; a single outlier against a 200 ms budget.

### 2. HUMAN DECISIONS taken in this phase
- **(a) Commit `10ce4df`: the single agent's tick glue was moved into importable functions.** Decided by the human at
  planning time. Reason: the multi agents then reuse that glue by import instead of re-typing about 40 lines, so the two
  paths cannot drift apart in later phases. It is the one change to `fieldmind/agent/` on this branch, made as its own
  commit and shown to change nothing (0 decision differences against `main` on dev, prompt log byte-identical).
- **(b) Lane choice in Phase 1 is earliest predicted finish, not the plan's fixed placement.** Decided by the human in the
  session prompt. Reason: Phase 1 is where the scheduler and the lane-choice rule get built and tested without hardware, on
  the simulated clock. On the mock backend both lanes call the same backend, so lane choice cannot change a decision.

### 3. HUMAN DECISION for later phases
With a real model, **the accuracy gates in Phases 2 and 3 use the plan's fixed placement.** Reason: NPU and CPU numerics can
give different answers to the same prompt, and an accuracy change must have one cause. **Lane disagreement is measured
separately**, by sending the same recorded prompts to both lanes. Free lane choice (earliest predicted finish and the other
policies) returns in the Phase 5 scheduling study.

Not built yet: the scheduler has no fixed-placement mode today (`multi.placement` is a label only). It has to be added
before the first real-model accuracy gate.

### 4. Simulated lane timings are expectations, not results
The simulated lane timings, the lane split, and "every P1 diagnosis misses its 10 s deadline" are **expectations computed
from placeholder rates**. The rates were measured on two different models, one of them an impure GGUF (the human's close-out
note; the Phase 0b GGUF purity check was blocked, so this report does not confirm it independently). They are relabelled
where they appear below.

### 5. Open items carried out of Phase 1
- **The write audit must be out of the timed hard path before board timings (Session 6).** Today it runs inside the P0 span
  and costs about 5 ms mean and up to 100 ms of it on the laptop.
- **The placeholder lane rates are replaced after Session 2**, with rates measured for the chosen model on both lanes.

### 6. Mutation checks: confirmed run with `PYTHONDONTWRITEBYTECODE=1`
All 15 mutations (9 at the gate, 6 after review) were run by one script that, for each mutation: edits the file, deletes
every `__pycache__` outside `.venv`, runs pytest in a subprocess with `PYTHONDONTWRITEBYTECODE=1` in its environment and
`-p no:cacheprovider`, restores the file, and deletes `__pycache__` again. The phase-reviewer's own mutations also ran with
`PYTHONDONTWRITEBYTECODE=1`, in a separate copy of the tree.

## Phase 1 gate (pre-registered, committed before any Phase 1 code: `0404b04`)

Backend: mock only. The phase is model-agnostic: no model is chosen and there is no real-model baseline, because the Session 2
board runs have not been done. Mode: lockstep only, so every job finishes inside its tick.

Pass requires all of:

1. **Decision identity, dev.** On all 36 dev episodes (`data/episodes_dev`) with `--backend mock`, every decision field is
   identical between `--arch single` and `--arch multi`.
2. **Prompt identity.** Every prompt sent to the backend is byte-identical and sent in the same order. Each call is recorded
   with its role, max_tokens and a hash of the mock hint; repair calls are included; the two logs are diffed.
3. **Baseline unchanged.** `--arch single` on this branch reproduces current `main` (`ca93ed5`) exactly: 0 differences on the
   same field list, prompts included.
4. **Timing.** S7 deadline misses are 0 on both arches. Report the per-tick overhead of the multi plumbing in ms and the
   deterministic-assessment (P0 hard path) time against the 200 ms target, as **laptop timing**, not board timing.
5. **Tests.** One-writer enforcement, scheduler priority order, lane choice, and evidence-tick stamping each have a test,
   and each test is mutation-checked with `PYTHONDONTWRITEBYTECODE=1` and `__pycache__` cleared.

If any decision field differs, stop and report the first (episode, tick, field) where it differs. The single agent is not
changed to make them match.

After the build: run the phase-reviewer subagent, then one reporting-set run (30 episodes) with both `--arch` values,
expecting 0 differences, then commit and tag `multi-phase1`.

### Compared fields (fixed before the build)
Per assessment:
- `tick`, `timestamp`, `state`, `triage`, `headline`, `facts`
- `hypotheses`: their order, and every key: cause, rank, case_ref, confidence, confidence_shown, supports (= cited fact IDs),
  discriminator, and the verifier / carried / model_only flags
- `belief_ranking`, `actions`, `escalate`, `unexplained`, `confidence` (shown), `degraded_mode`, `llm_invoked`
- per envelope: `agent`, `tick`, `status`, `payload`, `cited_facts`, `cited_cases`, `tokens`, `prompt_tokens`,
  `retrieved_cases`, `retries`, `error`, `prompt`, `raw_reply`
- per call: `status`, `prefill`, `decode`, `decode_ms`

Per run: `n_ticks`, `diag_calls`, `ver_calls` (the LLM call count), `llm_invocation_rate`, `parse_failure_rate`,
`verifier_disagreement_rate`, `llm_retries`, `mean_prompt_tokens`, `envelope_status_counts`.

Summary: every key of `summary_*.json` except the latency keys.

### Excluded fields (fixed before the build)
- `latency_ms` (envelope and per call)
- `prefill_ms` per call (the mock's ttft is derived from latency)
- `tick_latency_ms`, `wall_clock_s`, and the `S1_*` latency keys
- the multi-only `multi` telemetry key (jobs, lanes, queue wait)
- `deadline_miss` / `deadline_miss_rate`: these are timing-derived, so they are reported separately as S7 rather than compared

## Gate result (dev, 36 episodes, mock, HEAD `abdf2e9`, clean tree)

| clause | result | verdict |
|---|---|---|
| 1. decision identity, multi vs single | `bench/compare_runs.py`: **0 differences** over 36 episodes, 5,850 assessments and 2,174 envelopes; the summaries also have 0 differences | PASS |
| 2. prompt identity | 2,174 recorded calls on each side; **`cmp` reports the logs byte-identical**, same sha256 (`6e9b748a…`) | PASS |
| 3. single on branch vs main `ca93ed5` | runs: 0 differences; summary: 0 differences. The single prompt log is also byte-identical to the one recorded before the refactor | PASS |
| 4. S7 / timing | S7 misses = 0 for single and multi, in both repetitions. P0 never exceeded 200 ms (0 of 5,850 ticks). Timing table below | PASS |
| 5. tests | 47 new tests at the gate, 56 after review fixes; all 9 + 6 mutations were caught (tables below); full suite **154 passed** | PASS |

No decision field differed, so there was no first differing tick to report.

### Timing (laptop, mock; dev, 5,850 ticks; two repetitions, interleaved)
| | rep 1 | rep 2 |
|---|---|---|
| single tick ms: mean / p95 / max | 0.408 / 0.764 / 1.66 | 0.425 / 0.786 / 2.55 |
| **plumbing overhead, audit off** (paired multi−single per tick): mean / p50 / p95 / max | **0.020 / 0.012 / 0.060 / 1.90** | **0.005 / 0.008 / 0.059 / 1.64** |
| plumbing overhead, audit on: mean / p50 / p95 / max | 6.36 / 2.65 / 22.0 / 121.2 | 6.33 / 2.68 / 22.1 / 50.6 |
| **P0 deterministic-assessment ms, audit off** (all ticks): p50 / p95 / max | **0.377 / 0.644 / 2.20** | 0.376 / 0.646 / 2.01 |
| P0, audit off, non-QUIET ticks (1,982): p50 / p95 / max | 0.540 / 0.657 / 2.20 | 0.528 / 0.664 / 2.01 |
| P0, audit on (all ticks): p50 / p95 / max | 3.25 / 15.4 / 100.5 | 3.29 / 15.4 / 27.6 |
| P0 > 200 ms | 0 | 0 |
| S7 deadline misses, single / multi (audit on and off) | 0 / 0 | 0 / 0 |

The P0 time runs from tick start to the deterministic assessment: sensor, triage, signature, retrieval and belief, then the
gate's approve on belief's claims. On QUIET ticks it covers the whole tick. In lockstep the published assessment is still the
final one; the P0 assessment is telemetry.

**The write audit is the expensive part.** It costs about 300× the plumbing itself, and up to 122 ms on a laptop tick, because
it fingerprints every section (including the growing event log and the assessment) around each agent step. On a slower board
CPU this could approach the 200 ms budget. See Decisions taken, item 3.

### Lane and job telemetry: EXPECTATIONS from placeholder rates, not results
Dev, multi, simulated clock. Every timing in this table (queue wait, deadline misses, finish times, lane busy seconds) and
the lane split itself are computed from the placeholder lane rates. Those rates were measured on two different models, one
of them an impure GGUF, and neither is the model both lanes will run. They are what the plumbing predicts under those rates;
nothing here was measured on a lane. Job counts, stale drops and prompt-token estimates do not depend on the rates.
| | value |
|---|---|
| jobs | 2,174 = 1,982 diagnostician + 192 verifier. This equals `diag_calls + ver_calls`; stale drops 0; replaced 0 |
| diagnostician P1 (URGENT): lane | NPU 118, CPU 199 |
| diagnostician P2: lane | NPU 1,615, CPU 50 |
| verifier P3: lane | NPU 48, CPU 144 |
| queue wait (simulated) | 0 for 99.7% of jobs; max 1.3 s |
| simulated deadline misses | P1 317 of 317, P2 0 of 1,665, P3 0 of 192 |
| finish more than 30 s after the evidence tick | 142 of 2,174 (mean 33.3 s for those 142). Over all 192 diagnostician-then-verifier ticks the chain finishes at mean 31.0 s, max 35.6 s |
| lane busy (simulated) | NPU 36,093 s, CPU 6,508 s |
| prompt tokens (estimate) | diagnostician mean 2,036, max 2,285, **1,980 of 1,982 over 1,280**; verifier max 2,095, 170 of 192 over 1,280 |

**Expectation, not a result:** all P1 jobs miss the 10 s P1 deadline on the simulated clock, because a ~2,000-token prompt plus a 256-token answer cap does not fit 10 s at either
lane's planning rate (NPU ≈ 2.2 + 18.4 s). That is the expected Phase 1 outcome: the prompt shrink (Phase 2) and the
60-token cap are what make P1 feasible. These are simulated-clock numbers on placeholder rates, so they are not a measurement.

### Dev summary (mock; identical for both arches)
| metric | value |
|---|---|
| Q1 macro F1 | 0.782 |
| Q2 top-1 / top-3 | 0.332 / 0.453 |
| library: belief top-1 tie-fair / belief group | 0.375 / 0.502 |
| Q3 faithfulness | 1.0 |
| Q4 precision / recall | 0.357 / 0.882 |
| Q5 FP/h | 0.98 |
| Q6 lead time (min) | 32.4 |
| S4 LLM invocation rate | 0.337 |
| S7 | 0.0 |

## Design note

**The tick (lockstep):**
1. `gate.begin_tick`
2. P0 path: `sensor` → `triage` → (if QUIET, gate publishes and the tick ends) → `sensor.signature` → `retriever+belief` →
   gate publishes the deterministic assessment
3. scheduler: diagnostician job (P1 if URGENT, else P2) → gate checks the result against the evidence tick's facts and merges
   it into the diagnosis slot
4. scheduler: verifier job (P3), only when `should_run` says so → gate writes the verdict
5. gate+memory: approve, shown confidence, assessment, event log, status

This is the single agent's order exactly. Every step runs inside `bb.step(agent)`.

**Blackboard sections** (`fieldmind/multi/blackboard.py`, `OWNERS`):

| section | single writer | readers | WorldModel field |
|---|---|---|---|
| facts (per tick; index of stamped ids `t84.F1`) | sensor | all | none (new) |
| trust | sensor | sensor (next tick), triage, diagnostician (summary) | `trusted_tags` |
| residuals | sensor | triage (trip extrapolation) | `residuals` |
| findings | sensor | triage, diagnostician (summary) | `open_findings` |
| baselines | sensor | none in Phase 1 | `baselines` |
| signature (+ slopes) | sensor | retriever | none |
| outbox.sensor (this tick's events) | sensor | gate | none |
| triage (level, reason, plant state) | triage | orchestrator / scheduler, gate | none |
| retrieval (packet) | retriever | diagnostician, gate | none |
| belief (hypotheses, log-odds) | retriever | diagnostician (summary), gate (shown confidence) | `hypotheses` |
| outbox.retriever | retriever | gate | none |
| jobs (job table) | scheduler | telemetry | none |
| diagnosis (checked, merged claims) | gate | verifier, gate | none |
| verdict | gate | gate | none |
| status (`degraded_mode`, tick, state) | gate | triage (`derive_state`) | `degraded_mode`, `tick`, `state` |
| events (timeline) | gate | evaluator / checkpoint | `timeline` |
| assessment | gate | harness, evaluator | none |

The diagnostician and verifier own no section. Their answers return as Results, and only the gate puts them on the board.

**Enforcement** has three layers:
- `write`/`mutable` raise `WriterError` for a non-owner, and for an owner writing during another agent's step.
- `read` returns read-only containers.
- The audit (`multi.audit_writes`) fingerprints all sections around every step and raises if a non-owner's step changed one.
  This catches in-place mutation by the reused world-model functions.

Those functions are given a namespace that holds only the calling agent's live sections plus a fresh event outbox. Events
reach the log only through the gate, in pipeline order.

**Messages** (`fieldmind/multi/jobs.py`):
- `Job{job_id, agent, side, evidence_tick, priority 1..4, deadline_s, max_answer_tokens, submit_s, lane, prompt_tokens,
  predicted_finish_s, start_s, finish_s}`.
- `Result{job, envelope, evidence_tick, lane, queue_wait_ms, start_s, finish_s, stale}`.
  - The envelope is the existing `AgentEnvelope`, unchanged, so it goes into `Assessment.envelopes` exactly as in the single
    agent.
  - The scheduling fields go to `assessment["multi"]["results"]`, in the same order as `envelopes`.

**Lanes and scheduler** (`fieldmind/multi/lanes.py`, `fieldmind/multi/scheduler.py`):
- Each `SimLane` runs one job at a time (a second `begin` raises `LaneBusy`) on a simulated clock. A job starts at
  max(submit, lane free) and takes prompt/prefill + answer/decode seconds; the answer length is capped at `max_tokens`.
- `LaneBackend` is the backend the Diagnostician and Verifier are built on, which is why they are unmodified. The job's first
  call picks the lane by earliest predicted finish (ties go to the NPU), and repair calls stay on that lane.
- The queue is ordered by (priority, submit order), with at most one waiting job per (agent, side).
- On mock, both lanes share one backend. On llamaserver, each lane gets its own URL (8080 / 8081). That path is wired but was
  not run this session.

**Fact IDs.** Fact objects keep their local id (`F1`) because the prompt text must be byte-identical in Phase 1. The stamp
lives in the board's index (`FactsBook.stamped_ids`, `resolve("t84.F1")`). The gate resolves citations against
`facts_of(result.evidence_tick)`, which is tested to differ from using the current tick.

## Verification
| step | result |
|---|---|
| ran the module | dev, single and multi, mock: 36 episodes, 5,850 ticks each, exit 0 |
| self-tests | `pytest`: 145 passed at the gate (98 before + 47 new), **154 after the review fixes**; `bench` scripts 20/22/16 passed after the refactor |
| independent re-derivation | LLM call count by three routes: prompt-log lines by role (1,982 diag + 192 ver = 2,174) = run counters `diag_calls`+`ver_calls` (2,174) = lane `n_jobs` sum (2,174); relative difference 0. Decision identity by a second method: a whitelist sha256 over the decision fields (not `compare_runs`' blacklist walk) gives `e43b0ceb4d073cee` for main, single and multi |
| mutation check | 9 mutations, 9 caught (next table) |
| comparator can fail | 3 injected differences (a confidence_shown +0.001, a prompt +1 space, diag_calls +1) were all reported; a +50 ms `tick_latency_ms` was correctly ignored |

### Mutation checks (`PYTHONDONTWRITEBYTECODE=1`, `__pycache__` cleared before and after each)
| mutation | caught by |
|---|---|
| blackboard `_check` owner test disabled | `test_every_section_rejects_every_non_owner[*]` (17), `test_wrong_writer_leaves_section_unchanged`, `test_events_reach_the_log_only_through_the_gate` |
| audit owner comparison disabled | `test_audit_catches_in_place_mutation_by_non_owner`, `test_audit_catches_deep_mutation_inside_a_section` |
| scheduler priority sort inverted | `test_dispatch_is_by_priority_then_submit_order` |
| no replacement of a waiting job | `test_newer_job_replaces_waiting_one_for_same_agent_and_side` |
| predictor prefill/decode rates swapped | `test_prompt_heavy_job_goes_to_fast_prefill_lane`, `test_queue_wait_on_one_lane_is_exact`, `test_answer_longer_than_cap_is_clocked_at_the_cap`, `test_repair_call_stays_on_the_lane_of_the_first_call` |
| lane choice by latest finish | `test_prompt_heavy_…`, `test_decode_heavy_…`, `test_busy_lane_loses_to_idle_lane`, `test_repair_call_…` |
| gate checks citations against the current tick | `test_citation_checked_against_evidence_tick_not_current_tick` |
| stale threshold 1 → 2 ticks | `test_answer_older_than_one_tick_is_dropped_and_logged` |
| Result evidence tick not taken from the job | `test_result_carries_the_jobs_evidence_tick`, `test_episode_every_result_stamped_with_its_tick_and_decisions_match` |

## Direction checks
None: no physical response is modelled in this phase.

## Constants introduced
| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `multi.deadlines_s` P1/P2/P3/P4 | 10 / 30 / 60 / none s | CITED: plan, "Priorities and deadlines" (p.10) | none | simulated deadline-miss telemetry only |
| `multi.lanes.npu` prefill / decode | 909 / 13.9 tok/s | CITED: plan, "Rates used for planning" (p.14): Llama 3.2 3B Q4_0, llama.cpp NPU. ASSUMED as this project's NPU lane rate | unknown until the chosen GGUF is measured (Session 2); the plan's own NPU figures span 839–909 prefill and 11.7–13.9 decode | lane choice and the simulated clock only; never a mock decision (both lanes call one backend) |
| `multi.lanes.cpu` prefill / decode | 126 / 42 tok/s | CITED: plan p.15: Gemma 3 1B, LiteRT-LM CPU. ASSUMED as the CPU lane rate; a different model and runtime from the NPU row | unknown until measured | same |
| `FactsBook.keep` | 2 ticks | DERIVED: the stale rule accepts answers at most one tick old, so the current and previous tick suffice | none | which evidence ticks the gate can resolve |
| simulated decode length | min(reply decode tokens, max_tokens) | DERIVED: a server stops at `max_tokens`; the mock ignores the cap and reports len(text)//4 ≈ 960 | none | simulated clock only |

## Numbers that changed
None. Decisions, prompts and summaries are identical to `main` `ca93ed5`.

## Decisions taken
1. **Glue extraction in `fieldmind/agent/` (user's choice).** Commit `10ce4df` moved about 40 lines of tick glue out of
   `Orchestrator.tick` into module functions that `fieldmind/multi` imports. It was proven with 0 decision differences against
   main, a byte-identical prompt log, and passing pytest and bench scripts.
2. **Triage is its own agent.** The plan folds L2 into the scheduler; the session prompt lists triage separately. Keeping it
   separate keeps the scheduler a pure job/lane component. This changes no behaviour.
3. **`audit_writes: true` by default.** This is the conservative choice: it is the strongest single-writer check. It costs
   6.3 ms mean, 22 ms p95 and up to 122 ms per laptop tick. It must be off, or made incremental, before board P0 timings
   (Session 6) are trusted. Recorded under Open items.
4. **P1 rules not applied.** The plan's P1 rules ("verifier skipped", "shortest prompt") and rate adaptation from measured
   timings would change decisions or need real time, so they are not built. P1/P2/P3 only set priority and deadline.
5. **Simulated decode is capped at `max_tokens`** (see Constants). This is telemetry only.
6. **`build_multi_agent` lives in `bench/harness.py`, not in `fieldmind/multi/`.** That keeps `fieldmind/` free of `bench`
   imports for the device copy. It builds the components through `build_agent`, so nothing is duplicated.
7. **Lane choice uses earliest predicted finish, not the plan's Phase 1 "fixed placement".** The session prompt asked for
   earliest finish, and it overrides the plan here. It cannot change a mock decision, since both lanes share one backend.
   With a real model it could: NPU and CPU arithmetic may give different answers. Phase 2 and later comparisons on a real
   backend must either pin placement or report lane-dependent differences. The plan's rule 3 (same GGUF on both lanes)
   assumes they don't differ.
8. **Cross-tick answers are published with stamped ids (review fix).** An answer whose evidence tick is not the current tick
   is checked, merged and published with `t84.F3`-style ids, so L6 cannot take it for the current tick's `F3`. This never
   happens in lockstep, where ids stay local and prompts stay identical.

## Phase-reviewer findings and what was done

The reviewer independently reproduced the dev gate (0 differences, prompt sha256 matches, telemetry numbers match). It then
ran 6 mutations in a scratch copy and read the diff against the 10 checks. Its three most serious findings, and what was done
about each:

| finding | action |
|---|---|
| **1. Failure-path wiring untested.** The mock never fails, so "gate skips `Verifier.apply`" (A) and "gate drops the degraded marker" (B) survived all 145 tests, and A would also pass the dev gate | Added `tests/test_multi_failure_paths.py`. A scripted backend, keyed by call index, forces a repaired answer, invalid_schema (degraded marker), timeout, error, low faithfulness, a model-only cause, a verifier fail and a verifier revise. They run through both arches on 3 dev episodes (A02, C02, D03), with prompts compared. The test asserts each path occurred, then 0 differences. **Mutations A, B and F (URGENT `max_facts` forced to 12) are now caught.** F was first missed because the test ran without `log_prompts`, so prompt text was not compared; fixed. |
| **2. Tick stamp only in the index.** A one-tick-old accepted answer brought local ids into the current tick, where `Gate.approve` (`l6_gate.py:114-116`) could match them to the wrong facts and hide an ALARM from `unexplained`; the old test asserted this as correct | Cross-tick answers now carry stamped ids (Decisions taken, item 8). New test: a tick-84 citation of `F1` leaves tick 85's ALARM `F1` in `unexplained`. Mutation "stamping off" is caught by 2 tests. |
| **3. Enforcement depends on the audit.** `read()` is shallow; `equipment`, `notes_seen` and `episode_id` were in no section | `equipment` and `notes_seen` are now sections (owner gate), and `episode_id` is audited under `status`. A test pins that every WorldModel field is in a section. The docstring and this report now say read handles are shallow; a test documents it (no raise with audit off, raise with audit on). The audit stays on; the board question is under Open items. |
| latent lane latch: a job that raises after `begin` left `LaneBusy` set | `lane.end` moved into `finally`; test `test_lane_is_released_when_a_job_raises` |
| report: "mean 31.0 s" described the wrong set | corrected (lane telemetry table) |
| plan says "fixed placement" for Phase 1 | recorded as a deviation (Decisions taken, item 7) |
| some tick glue is re-typed in `gate.py` (assembly, QUIET publish, verifier-apply branch) | Not extracted further, as the conservative choice: no second `fieldmind/agent` refactor. These copies are now pinned by the failure-path parity test (mutations A and B caught). |

Not acted on, because it is already declared and out of Phase 1 scope: the hard path blocks on lockstep jobs, and is a
synchronous HTTP call on llamaserver (Session 6); the capped model→belief update; the prompt and answer caps.

### Review-fix mutation checks (`PYTHONDONTWRITEBYTECODE=1`, `__pycache__` cleared)
| mutation | caught by |
|---|---|
| A: gate skips `Verifier.apply` | `test_failure_paths_identical_single_vs_multi` |
| B: gate drops the degraded marker | `test_failure_paths_identical_single_vs_multi` |
| F: multi URGENT `max_facts` forced to 12 | `test_failure_paths_identical_single_vs_multi` |
| cross-tick stamping off | `test_citation_checked_against_evidence_tick_not_current_tick`, `test_late_answer_cannot_explain_a_current_tick_fact_with_the_same_local_id` |
| lane not released on raise | `test_lane_is_released_when_a_job_raises` (+3) |
| `equipment` removed from sections | `test_audit_catches_a_change_to_equipment_or_episode_id` |

After the fixes the dev gate was re-run: multi vs single 0 differences, prompt logs byte-identical.

## Reporting run (30 episodes, run once, mock, HEAD `c1e1470`, clean tree)

| check | result |
|---|---|
| decision identity, multi vs single | **0 differences** over 30 episodes, 5,160 assessments and 2,335 envelopes; summary 0 differences |
| prompt identity | 2,335 calls each; `cmp`: byte-identical |
| single vs committed `results/baselines/single_v3_summary.json` | `summary` 0 differences, `per_episode` 0 differences |
| S7 | 0 for single, 0 for multi; P0 > 200 ms: 0 of 5,160 |
| stale drops / jobs | 0 / 2,335 (= `diag_calls` + `ver_calls`) |

| metric (mock, both arches identical) | value |
|---|---|
| Q1 macro F1 | 0.743 |
| Q2 top-1 / top-3 | 0.334 / 0.523 |
| library: belief top-1 tie-fair / belief group / group top-1 | 0.438 / 0.574 / 0.547 |
| Q3 faithfulness | 1.0 |
| Q4 precision / recall | 0.38 / 0.926 |
| Q5 FP/h | 0.91 |
| Q6 lead time (min) | 31.5 |
| S4 | 0.45 |
| S7 | 0.0 |

Saved as `results/baselines/multi_p1_summary.json`. Mock numbers measure the deterministic and retrieval layers, not an agent.

## Disagreements recorded, not resolved
- **Prompt cap.** CLAUDE.md requires every prompt to stay under 1,280 tokens. In Phase 1, 1,980 of 1,982 diagnostician prompts
  and 170 of 192 verifier prompts exceed it (estimated: mean 2,036, max 2,285). This is forced by gate clause 2: prompts must be
  byte-identical to the single agent's. Shrinking them is Phase 2's job. Prompt and answer tokens are logged per call.
- **Answer caps.** The plan's caps (diagnostician 60, verifier 30) are not applied; the single agent's `agent.max_tokens` of 256
  is kept, for the same reason.
- **P1 is expected to be infeasible at these sizes.** On the simulated clock every P1 job misses its 10 s deadline. This is an
  expectation from placeholder rates (two models, one an impure GGUF), not a measured result. It is consistent with the plan's
  own arithmetic for a ~2k-token prompt and a 256-token cap.

## Blocked / needs a decision
- None blocking Phase 1.
- Carried forward: no model is chosen and the lane rates are placeholders until the Session 2 board runs.

## Open items
- An audit mode cheap enough for the board: incremental fingerprints, or audit only in tests and lockstep runs.
- Real-time mode, the "hard path never waits on a lane" test (with a fake lane that sleeps 60 s), the degradation ladder, and
  rate adaptation: Session 6. The stale rule's "unless that side's evidence is unchanged" clause needs sides: Phase 3.
- The capped model→belief update (the plan's "the belief agent moves log-odds by a capped amount"). In Phase 1 a model answer
  only reorders the shown claims through the single agent's `merge`, exactly as before. The capped update is new behaviour,
  so it belongs to a later phase.

## What I could not verify
- **Board timings.** Every millisecond here is laptop time with the agent code on the laptop. The P0 margin on the SM8650
  is unknown.
- **The llamaserver lane wiring.** Per-lane URLs (`make_lanes`) were not exercised. This session is mock-only, and no lane was
  started.
- **Lane-choice realism.** The lane rates come from two different models in the plan, and the simulated clock omits the
  plan's overhead term. The lane split and simulated deadlines above therefore illustrate the mechanism; they are not
  predictions.
