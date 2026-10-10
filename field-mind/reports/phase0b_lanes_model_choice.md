# Phase 0b, Session 2: persistent model servers and model choice — report

Baseline: **single-agent-v3** (`results/baselines/single_v3_summary.json`, mock). Mock numbers in this report measure only
the deterministic and retrieval layers. They are **not agent results**. Energy is `null` everywhere because no power
was measured. Agent code runs on the **laptop**, and only model calls run on the board. Hard-path timings are laptop
timings, not board timings.

## Status
PARTIAL. Steps 1, 2 and 4 are built and tested: `LlamaServerBackend`, board tooling, the ranking rule and the
model-choice tool. The mock reference row is run. **Blocked on the board:** `adb devices` lists no device, so
`/board-up`, the candidate runs, the lane rates and the reporting run have not happened.

## Decisions taken
1. **C episode = `dev_C02_feeder_trip`** (truth RCA-14, library). The C twins C01/C03/C05/C06 are held-out (RCA-06):
   they cannot score top-1 or library ECE. The model-choice set is `dev_A01_fcv_seize` (RCA-01),
   `dev_B01_tube_leak` (RCA-11) and `dev_C02_feeder_trip` (RCA-14).
2. **No JSON grammar** (`json_mode: false`), so the broken-JSON rate measures the model. The plan's open decision ranks
   models on that rate, and a grammar would push it to about 0.
3. **`cache_prompt: false`**, so `prefill_tokens` and prefill tok/s count every prompt token.
4. **temperature 0, seed 0**, so runs can be reproduced. Design choice.
5. **Ranking verdict: measured and recorded; `_merge` is not changed in this session.** A "belief decides" verdict
   becomes a separate keep/revert step on dev.
6. `llm.backend` default stays `mock`. The URL lives in `llm.llamaserver.url` (the per-backend sub-block that
   `make_backend` reads), not in a top-level `llm.url`.
7. Model-choice runs use dev episodes only. The 30 reporting episodes run once, at the end, with the chosen model
   (user rule).

## Pre-registered: the ranking question (written and committed before any real-model run)
The question: should belief decide rank-1, with the model only breaking ties, or does the model's order stay?

- **Population.** Post-onset ticks of library-truth episodes that meet all of these:
  - the belief ranking is non-empty;
  - the diagnostician envelope is `ok`, and the gate kept the model's order (raw citation faithfulness ≥
    `min_faithfulness` = 0.5);
  - the **model's own rank-1** is in a group that is **none** of the groups of belief's tied top set. The model's
    rank-1 is `payload.hypotheses[0]`, resolved by cause against the deterministic hypotheses and then by
    `case_ref`, the same way `_merge` resolves it.
- **Scoring.**
  - Model correct means its rank-1 case lies in the true group (`case_groups.json`). A model-only idea with no case
    counts as incorrect and is counted separately.
  - Belief correct is tie-fair: the share of the tied top set that lies in the true group.
- **Rule.**
  - n < 30 → INCONCLUSIVE.
  - p_belief − p_model ≥ 0.10, and belief ≥ model in every episode with ≥ 10 such ticks → **BELIEF_DECIDES**.
  - The mirror image → **MODEL_DECIDES** (status quo).
  - Anything else → INCONCLUSIVE (status quo kept).
  - The 0.10 margin and the n and episode floors are pre-registered decision constants (design choice, not derived).
    Ticks are autocorrelated, so the per-episode clause stands in for a significance test.
- **Decided on the chosen model's dev runs.** Reported for every candidate. Also reported:
  - the same statistic using the **shown** rank-1 (the population definition the mock prior used);
  - the same statistic on the **mock** run of the same three dev episodes.
- **The mock prior is a different population.** Session 1b's figures (belief 41%, model order 28%, 276 ticks) cover
  only the reporting-set ticks that are in the lowest confidence bin and have a negative margin.
- Code: `bench/model_choice.py` (`ranking_question`, `verdict`). Tests: `tests/test_model_choice.py`.

### Provenance of the rule: order of events, as it happened
Sources: git commit times; file mtimes for `results/` (gitignored); and the plan file, which is outside git.
| time (2026-10-02) | event | evidence |
|---|---|---|
| 00:06:29 | Plan file last written. It contains the population, scoring, the 0.10 margin, n ≥ 30 and the per-episode clause | file mtime; **outside git, so git cannot prove it** |
| (between) | `bench/model_choice.py` written, with the rule in code | not separately recorded |
| 00:09:21 | **Mock reference first computed** (`results/phase0b/runs_mock_dev3.json`); `model_choice` run on it, giving the 157-tick table | file mtime |
| (between) | **After seeing the mock result**, `verdict()` changed to round the difference to 3 d.p. The boundary test had failed on 0.50 − 0.40 = 0.0999…. The constants and the population did not change | this session |
| 00:11:53 | **d6ff743 committed (the rule), AFTER the mock reference was computed.** The commit also contains the mock table | `git log` |

Stated as it is: **the rule was committed after the mock reference, not before.** The rule's text predates the mock
run only in the plan file, and git cannot vouch for that. One code change, the rounding, was made after the mock
result was seen. It cannot change the mock verdict: |0.239 − 0.389| = 0.150 is above 0.10 with or without rounding,
and the verdict is INCONCLUSIVE because of the per-episode clause (C02 goes the other way). The real-model claim still
holds: d6ff743 predates every real-model run, because none has happened yet.

### Pre-registered follow-up (committed before any real-model run)
**If the 3-episode verdict is INCONCLUSIVE** for the chosen model, run the same statistic with the **chosen model
only**, using the same rule, unchanged (same code, `bench/model_choice.py` at d6ff743 plus the rounding fix), on
**all dev fault episodes whose truth is a library case**:
- the 13 episodes A01, A02, A03, A05, A06, B01, B02, B03, B05, C02, C04, D01, D03;
- excluded, because the truth is held-out RCA-06: C01, C03, C05, C06;
- excluded, because the truth has no case: A04, B04, D02, D04, E01–E03.

The verdict from the 13 episodes replaces the 3-episode verdict, and it is final for this session. The three
model-choice episodes are **reused**, not rerun, if the commit of every decision path, the config and the model file
are unchanged (temperature 0, fixed seed). Otherwise all 13 are rerun.

**Board-time estimate (planning, not measured):**
- Diagnostician calls come from the deterministic triage, so the mock dev run gives the call count for any model: 1,329
  diagnostician + 151 verifier calls over the 13 episodes, or 1,067 + 119 = 1,186 for the 10 not yet run. Verifier
  calls depend on the merged order, so their count is approximate.
- Prompt: about 2,007 tokens per diagnostician call. This is an `est_tokens` (chars / 4) estimate, not a server count.
- Rates: the plan's NPU planning figures for Llama 3.2 3B Q4_0 (about 909 prefill / about 13.9 decode tok/s,
  `multi_agent_plan.pdf`, "Rates used for planning").
- Answer length: unknown for a real model. Bounded by 60 tokens (the plan's diagnostician target) and 256 (the cap).
- That gives about 2.2 s of prefill plus 4.3–18.4 s of decode, so about 6.5–20.6 s per call.
- **Total: about 2.1–6.8 h of board time** for the 10 remaining episodes, plus cooling gaps between episodes, plus
  repair calls (not estimated). Rerunning all 13 is about 2.7–8.5 h.
- A smaller chosen model would be faster, but its rates are not measured yet. The estimate is recomputed from the
  measured board-up rates and answer lengths before the run.

### Mock reference on the same population (not an agent result)
| mock, dev A01+B01+C02 | n | p_belief | p_model | verdict |
|---|---|---|---|---|
| pooled | 157 | 0.239 | 0.389 | INCONCLUSIVE |
| dev_A01_fcv_seize | 53 | 0.000 | 0.528 | |
| dev_B01_tube_leak | 69 | 0.145 | 0.478 | |
| dev_C02_feeder_trip | 35 | 0.786 | 0.000 | |

On the mock, the model's rank-1 and the shown rank-1 give identical figures, because the mock answers on every
invoked tick and the gate never drops it. Pooled, these three dev episodes go the **opposite** way to the
reporting-set prior. C02 disagrees with A01 and B01, so the rule returns INCONCLUSIVE.

## Follow-up (Session 2, part 2): missing timings are None
- `LlamaServerBackend` now returns `None`, not 0, for any timing field the server does not send:
  `prefill_tokens` ← `prompt_n`, `decode_tokens` ← `predicted_n`, `ttft_ms` ← `prompt_ms`. A failed call also
  carries `None`. The other backends are unchanged. **LiteRT still writes 0 for anything it cannot parse.** That is
  not changed here; it is noted only.
- **One agent change, telemetry only:** the diagnostician's repair path (`l4_diagnose.py`) used to add the two calls'
  token counts, and adding `None` would crash. A count that either call did not report now stays `None`. No decision
  reads these counts. The mock run of the 3 dev episodes is identical before and after: 400 assessments, 0
  decision-field differences, 0 token differences.
- `env.prompt_tokens` still falls back to `est_tokens(prompt)` when the server sends no count. That is existing,
  documented behaviour. `bench/model_choice.py` therefore uses the server count `tokens.prefill` for prompt tokens,
  not `prompt_tokens`.
- Aggregations skip `None` and report the count of missing values per field:
  - `bench/board.py`: `lane_speed()["missing"]`, `mean_rates()` with `<field>_missing`, and `decode_rate_wallclock()`
    gives `None` if a count is missing;
  - `bench/model_choice.py`: `prompt_tokens_missing` and `answer_tokens_missing` rows.
  - TTFT is not stored in the envelope, so it is not aggregated in the table.
- Tests: `tests/test_llamaserver_backend.py` (no timings, partial timings, failed call) and
  `tests/test_missing_timings.py` (repair path, both aggregations). `pytest`: 89 passed / 0 failed.
- Mutation checks, run on scratch copies; all caught:
  - backend fills 0 for missing → 2 tests;
  - repair sum treats `None` as 0 → `test_repair_path_keeps_unknown_counts_unknown`;
  - `model_choice` counts `None` as 0 → `test_model_choice_excludes_none_and_counts_it`;
  - `board.mean_rates` counts `None` as 0 → `test_board_mean_rates_excludes_none_and_counts_it`;
  - a failed call drops the explicit `None`s → `test_failed_call_reports_no_counts`.

## Pre-registered: model-choice rule (human-approved; committed before any real-model run)
**Candidates, run in this order:**
1. Llama 3.2 3B Q4_0.
2. Qwen3 1.7B Q4_0, **with thinking off**. How it is turned off is recorded when it is run: the mechanism (chat-template
   flag, server option or prompt switch) and evidence from the replies that no `<think>` block was emitted.
3. Gemma 3 1B QAT Q4_0 (`google/gemma-3-1b-it-qat-q4_0-gguf` on Hugging Face). Gated: the Gemma licence has to be
   accepted before download.
4. Qwen2.5 0.5B Q4_0, last.

The same GGUF file runs on both lanes, and runs are separated by cooling gaps.

**Hard constraints.** A model must pass every one. Code: `bench/model_choice.py`, `selection()`.
| constraint | limit | source of the value |
|---|---|---|
| broken JSON, first reply | ≤ 0.10 | human-approved. Each failure costs a whole repair call |
| broken JSON, after the one repair | ≤ 0.02 | human-approved. Each failure drops that tick to the deterministic answer |
| **projected** verified-diagnosis time, NPU lane | ≤ 10 s | **human decision, changed from my proposal** (see below) |
| all layers offloaded to HTP0 | confirmed in the NPU lane's startup log | human-approved |
| every weight matrix Q4_0 or Q8_0 | `bench.gguf_types` verdict OK | human-approved |

A check with no evidence counts as **UNVERIFIED and fails**. The board-up and GGUF results enter through
`--checks <json>`.

**Projection (labelled a projection everywhere):**
projected = (700 / prefill_rate + 60 / decode_rate) + (350 / prefill_rate + 30 / decode_rate) seconds.
- The rates are this model's **measured server rates from its own dev runs**, pooled over the ok calls on the lane:
  Σ tokens / Σ server ms.
- They come from the new per-call record `envelope.calls`, which holds token counts, `prompt_ms` and `predicted_ms`.
- Caveat: the rates are measured at the single-agent prompt size (about 2,000 tokens), and the prefill rate at 700
  tokens may differ.

**Ranking among the models that pass:**
1. Library group top-1 on dev, with a tie band of 0.05.
2. Then model citation faithfulness (raw, before the gate), with a band of 0.02.
3. Then mean diagnostician call time, labelled **"stand-in for energy, not measured energy"**.

**If no model passes: stop and ask.** No limit is relaxed.

**Reported, not gating:** single-agent diagnostician call time (mean, p95), tick deadline misses (count and rate), and
answer tokens per call (mean, p95, share at the 256 cap).

**Flags in the table:**
- A model whose library group top-1 is **below the deterministic floor**. The floor is the mock run of the same 3 dev
  episodes, 0.501. Such a model is also noted as evidence for the ranking question: its order loses to the
  no-reasoning order.
- **A lead that comes from one episode.** Any pair ranked apart by more than the tie band where the higher model is
  ahead on at most one of the three episodes. The table also shows group top-1 per episode for every model.

### Decision record: the call-time limit was changed by human decision
My proposal was "single-agent diagnostician call p95 ≤ 20 s at the ~2,000-token prompt". **The human replaced it**
with the projected verified-diagnosis limit above (≤ 10 s). Their reason: this model runs on both lanes for every
multi-agent session. That design targets prompts under 1,280 tokens, 60-token answers and a verified diagnosis in
about 10 s. The single-agent call shape (about 2,000 tokens in, up to 256 out) is dominated by decode time, so it would
select for the baseline, not for the target design. The single-agent p95 is kept as a reported row.

### Telemetry added for this rule (no decision path changed)
- `LLMReply.decode_ms` (llama-server `predicted_ms`; `None` when not sent).
- `AgentEnvelope.calls`: one record per backend call (`runtime.llm_backend.call_record`). The diagnostician writes 1
  or 2 records (with repair) and the verifier 1.
- The mock dev run is identical with these fields added: 400 assessments, 0 decision-field and 0 token differences, 294
  call records (262 diagnostician + 32 verifier).
- On mock, the rate, projection and answer-length rows are nulled, because the mock's timings and token counts are
  synthetic.
- Tests: `tests/test_model_choice.py`, which covers:
  - the projection formula (worked value 8.67 s at 900 / 12 tok/s);
  - each hard constraint, including the exact limits and UNVERIFIED;
  - both tie bands and the call-time tiebreak;
  - stop-and-ask;
  - both flags, and the floor taken from the mock column.

  Also `tests/test_missing_timings.py` (each repair call recorded separately) and the backend test (`decode_ms`).
  `pytest`: 98 passed.
- Mutation checks, all caught:
  - verifier shape (350, 30) → (350, 60);
  - limit 10 → 20 s;
  - tie band 0.05 → 0.02;
  - offload check passes when unverified;
  - one-episode flag `<= 1` → `< 1`;
  - call-time tiebreak reversed;
  - repair call record dropped;
  - `decode_ms` read from `prompt_ms`.

## GGUF tensor-type check (Session 2, part 2): BLOCKED, tooling ready
**Not done: there is no candidate file to read.**
- No GGUF exists on the laptop: `device/models/` is empty, and a search of the home directory finds none.
- No dump tool is installed: no `gguf-dump`, `llama-gguf` or `llama-quantize`, and no `gguf` Python package.
- The candidate files are, as far as anyone knows, only on the board. **Not even their presence is verified**, because
  no device is attached.

Ready for the next board session: `bench/gguf_types.py` (stdlib). It reads only the GGUF header, so a prefix of
each file is enough and multi-GB files need not be pulled:

    adb exec-out "head -c 33554432 <board path>/<model>.gguf" > <scratch>/head.gguf
    .venv/bin/python -m bench.gguf_types <scratch>/head.gguf

- Output: type counts, the types of `output.weight` and `token_embd.weight`, and every matrix tensor outside
  Q4_0/Q8_0. 1-D tensors (norms, biases) are listed separately, not flagged; llama-quantize never quantizes them.
- Tests: `tests/test_gguf_types.py`. Mutations caught:
  - Q6_K allowed → caught;
  - the Q6_K id mis-mapped → caught;
  - every tensor treated as 1-D → caught.
- **Limit:** the tests check the parser against a writer built from the same reading of the format. The independent
  check is the first real file: the type counts must match llama-server's own startup log
  (`llama_model_loader: - type q4_0: N tensors`).

**Expectation, to verify:** llama-quantize's default for Q4_0 keeps `output.weight` at a higher-precision type
(commonly Q6_K). Published "Q4_0" files are often not pure. Under the plan's K-quant rule, that would put the output
projection on the CPU. Untested here.

**If any matrix tensor is flagged** (not run; needs an F16/BF16 source GGUF of the same model, because requantizing a
Q4_0/Q6_K file compounds the error):

    llama-quantize --pure <model>-F16.gguf <model>-Q4_0-pure.gguf Q4_0

Alternative within the rule: keep the two big tensors at Q8_0 instead of forcing them to Q4_0:

    llama-quantize --output-tensor-type q8_0 --token-embedding-type q8_0 <model>-F16.gguf <model>-Q4_0-q8out.gguf Q4_0

Either produces a **different model file**, so it must be re-checked with `bench.gguf_types` and re-run through the
model-choice table; it does not inherit the original file's numbers.

## Verification
| step | result |
|---|---|
| ran the module | mock run on the 3 dev episodes via the edited `run_demo.py`: 400 assessments, **0** decision-field differences against Session 1b's `runs_mock_dev_c.json` (hypotheses, belief_ranking, state, triage, actions, escalate, llm_invoked). `bench.model_choice` runs on it (table above). Live board call: **not run (no device)** |
| self-tests | `pytest tests`: 83 passed / 0 failed (72 before, plus 7 backend and 4 model-choice tests). `bench/test_checks.py` 20/0, `test_envelope_logging.py` 16/0, `test_orchestrator.py` 22/0. A bare `pytest` (no path) errors at collection, because `bench/test_checks.py` calls `sys.exit` at import. This predates this session (checked on the stashed tree) |
| independent re-derivation | planned: decode tok/s from client wall clock, by differencing two calls that differ only in `max_tokens` (`bench.board.decode_rate_wallclock`), against the server's `predicted_per_second`. **Not run (no device)** |
| mutation check | Run on scratch copies; the repo was untouched. Backend: (M1) swap `prompt_n` and `predicted_n`: caught by `test_every_field_comes_from_server_timings`. (M2) `prompt_ms` × 1000: caught by the same test. (M3) lane label hard-coded: caught by it and by `test_registered_and_built_from_config`. Ranking statistic: (M1) group comparison replaced by case comparison: 3 tests fail. (M2) tie-fair dropped: `test_population_and_scoring_worked_by_hand`. (M3) gate filter removed: 3 tests fail. (M4) `n < 30` changed to `<=`: `test_verdict_rule_boundaries` |

Found while testing: in `verdict`, 0.50 − 0.40 evaluates to 0.0999… in floating point, so an exact 0.10 margin would
have failed the rule. The difference is now rounded to 3 d.p., the same rounding the rates already get. The boundary
test caught this.

## Direction checks
None: no physical response was added or changed.

## Constants introduced
| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `llamaserver.ctx_size` | 4096 | DERIVED: max `context_cap_tokens` 3072 + `max_tokens` 256 = 3328, rounded up to 2^12 | n/a | `-c` on both lanes; a prompt over it would be truncated or rejected |
| `llamaserver.timeout_s` | 120 | ASSUMED: 256 tokens at the plan's ~12 tok/s decode is ~21 s, plus about 3 s prefill, with a wide margin | [30, 300] s | when a slow call counts as a timeout |
| `llamaserver.cpu_threads` | 6 | /board-up skill starting value | to be measured | CPU lane decode rate |
| `temperature` / `seed` | 0.0 / 0 | design choice (reproducibility) | n/a | answers; greedy decoding |
| ranking rule margin / n floor / episode floor | 0.10 / 30 / 10 | pre-registered decision constants | n/a | the ranking verdict only |

## Numbers that changed
None so far. No decision path changed: the mock dev run is identical to Session 1b.

## Disagreements recorded, not resolved
- Mock ranking statistic on these three dev episodes (model 0.389 > belief 0.239) against the reporting-set prior
  (belief 41% > model 28%). They are different populations, and dev is not reporting. Recorded, not explained.

## Blocked / needs a decision
- **No device attached** (`adb devices` is empty). Steps 3, 5 and 6 need the QIDK on USB.

## What I could not verify
- Everything on the board: the server's timing fields on this llama.cpp build, the HTP0 offload, both lanes' rates and
  the thermal zone names.
- The backend is tested only against a fake server that returns the documented llama-server `timings` keys. If this
  board's build names them differently, every field stays 0 instead of being guessed. The first live call checks this.
