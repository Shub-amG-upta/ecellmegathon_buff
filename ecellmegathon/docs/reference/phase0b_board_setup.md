# Phase 0b / board setup (Fedora laptop + QIDK) — report

## Status
DONE for the setup and the campaign runner; the campaign itself has NOT been run (the human launches it). llama.cpp
pinned and built, 4 candidate GGUFs built to the recipe, pushed and checksum-verified, smoke test passed (B, C, D),
CLAUDE.md and `/board-up` updated, unattended campaign runner built and tested against a fake board. No timing,
accuracy or energy result exists for any candidate beyond the smoke-test calls below. Step-by-step state:
`reports/session2_setup_progress.md`.

## Human decision: quantization recipe (recorded before any board run)

**Decision (human, 2026-10-03), identical for all 4 candidates** (Llama 3.2 3B Instruct, Qwen3 1.7B, Gemma 3 1B QAT,
Qwen2.5 0.5B Instruct):

- every weight matrix Q4_0; token embedding and output at Q8_0. Both types are allowed by the committed rule
  ("every weight matrix Q4_0 or Q8_0", `reports/phase0b_lanes_model_choice.md`, hard constraints).
- built with `llama-quantize --pure --token-embedding-type q8_0 --output-tensor-type q8_0 <src> <dst> Q4_0`;
- from the original 16-bit weights: official safetensors through `convert_hf_to_gguf.py`. If a repo is gated and not
  yet approved, a well-known F16/BF16 GGUF mirror, with repo, file and sha256 recorded;
- Gemma: if Google's QAT Q4_0 GGUF already matches this recipe under `bench.gguf_types`, use it as is; otherwise
  build it from `google/gemma-3-1b-it-qat-q4_0-unquantized`.

**Reason (human):** published "Q4_0" files carry K-quant / Q4_1 tensors, which fall back to the CPU on HTP. Confirmed
on the board's `geniex/models/Llama-3.2-3B-Instruct-Q4_0.gguf` (bartowski, 1,921,909,280 B), whose header has
`token_embd.weight` (tied output) at Q6_K and 3 `ffn_down` tensors at Q4_1 (`bench.gguf_types`, 2026-10-03).

**Earlier 3B rates are on the impure file.** The earlier Llama 3.2 3B rates, 839 tok/s prefill and 11.7 tok/s
decode (figures reported by the human; their measurement is not in this repo), were measured on that impure file.
They are not comparable with any rate from the recipe above and must not be mixed with them.

## Human decision: context size and slots (2026-10-03)
`llama-server` is always launched with `-c 4096 -np 1`, the same for every model and both lanes, because the
single-agent prompt is ~2484 tokens and context size changes NPU speed. Code: `bench/board.py` (`CTX_SIZE`,
`N_PARALLEL`, `lane_command`); test: `tests/test_board_env.py::test_lanes_always_use_ctx_4096_np_1`.

## Human decisions on the mirror, EOS and launch flags (2026-10-03, after the laptop-side build)
1. **Safetensors mirrors accepted for the two gated models.** Reason: the weights are the official files. How the
   identity was established: the Hub's API (`model_info(..., files_metadata=True)`) returns the LFS sha256 of every
   file even for a gated repo that cannot be downloaded. Those published hashes were read for the official repos and
   compared with the mirrors' published hashes, then `~/fieldmind-build/fetch_weights.py` recomputed sha256 over the
   downloaded bytes and required equality (`logs/fetch_weights.log`, `OK` lines):
   - `meta-llama/Llama-3.2-3B-Instruct` == `unsloth/Llama-3.2-3B-Instruct`:
     `model-00001-of-00002.safetensors` `13cbd6d16e927a0c5bad54102514e6e18b4a47b3a6eb911e39d678d328d19f55`,
     `model-00002-of-00002.safetensors` `7b770216613ac5c34d7c54bdff1fa616bc4e338a9d0b20af6303e48c295ee23c`;
   - `google/gemma-3-1b-it-qat-q4_0-unquantized` == `unsloth/gemma-3-1b-it-qat`:
     `model.safetensors` `6d571889049c5550a2cdcc3e1846646e595b683ce0d3c7bea904ed8874cf8ef2`,
     `tokenizer.model` `1299c11d7cf632ef3b4e11937501358ada021bbdf7c47638d13c0ee982f2e79c`.
   Limit: the identity covers those LFS files only. The mirrors' small JSON files differ from the official ones.
2. **Gemma EOS id = 1 accepted**, to match Google's own GGUF.
3. **`-fit off` accepted on both lanes**, so the server cannot change the context size or the offload on its own.
   **`-lv 4`**: decided by the fixed 5% rule after the measurement (smoke test, step C): kept everywhere.

## Human decisions for the campaign (2026-10-03, committed before any further board run)
- **`-lv 4` rule, fixed in advance.** If `-lv 4` lowers prefill or decode tok/s by more than 5% (calls 2-3 of each
  launch), it is used only for a one-off offload-confirmation launch per model and all timed calls run at the default
  log level. Otherwise `-lv 4` stays everywhere. Which case applied is recorded with the numbers.
- **Option C.** All 4 candidates are screened; full dev runs only for Llama 3.2 3B and the one other model that passes
  the GGUF and offload checks with the lowest measured verified-diagnosis time. Reason: limited board time.
- **The campaign runs unattended.** Every decision point is applied by code from rules committed in advance; the model
  is chosen by `bench/model_choice.py` unchanged, not by the human. Flags are recorded and do not stop the run.
  Reason: the rules are mechanical and already committed.
- **Go-ahead condition after the smoke test** (no waiting for the human only if ALL hold): every layer and the final
  logits step run on HTP0 with a nonzero HTP0 buffer; the Gemma tokenizer check gave identical IDs on every dev
  prompt; the Gemma call stopped by itself. Otherwise stop and report.

## Step A: Gemma tokenizer check (laptop only) — passes against the accepted reference (Hugging Face tokenizer)
Prompts: no dev prompts were recorded on this machine, so they were recorded now: mock dev run at HEAD with
`--log-prompts` (36 dev episodes; 2,174 prompts = 1,982 diagnostician + 192 verifier; 2,084 distinct). Verifier
prompts embed the mock's diagnostician answer, so they are real prompt shapes, not the prompts a real model would
produce. Each prompt was wrapped as one user message in the chat template (jinja2). Our template and Google's
render **identical text on all 2,084**. Tokenized with the pinned `llama-tokenize` (`--no-bos --no-escape`, special
tokens parsed). Evidence: `~/fieldmind-build/tokcheck/`.

| comparison (token IDs, 2,084 distinct dev prompts) | identical |
|---|---|
| our Gemma GGUF vs Google's own QAT GGUF vocabulary, as published (the reference compared against earlier) | **0 / 2,084** |
| our Gemma GGUF vs the Hugging Face tokenizer (`transformers`, a different implementation; `tokenizer.model` sha256-identical to Google's) | 2,084 / 2,084 |
| our Gemma GGUF vs Google's GGUF vocabulary with ONE key added, `tokenizer.ggml.add_space_prefix = false` | 2,084 / 2,084 |

- The mismatch is exactly one extra token per prompt on Google's side, and the first differing token is `▁user`
  (2430) where ours and Hugging Face give `user` (2364).
- **Cause, tested:** Google's GGUF has no `tokenizer.ggml.add_space_prefix` key; the pinned llama.cpp then inserts a
  space after a special token. Adding that single key (value false, as the pinned converter writes) makes all 2,084
  identical. So the difference is in how this llama.cpp reads Google's older file, not in our vocabulary.
- **The 30 differing token spellings** are ids 138-167: runs of 2 to 31 `U+2581` in Google's GGUF, runs of 2 to 31
  plain spaces in ours (id 138 = 2, id 139 = 3, ..., id 167 = 31); token type 1 (normal) in Google's, 4
  (user-defined) in ours. Four of them occur in the dev prompts (ids 138, 139, 151, 155; 18,380 occurrences in total)
  and are produced identically by all three tokenizers.
- **HUMAN DECISION (2026-10-03): the Hugging Face tokenizer is the accepted reference for Gemma.** Reason: its
  `tokenizer.model` is sha256-identical to Google's, so it is the tokenizer the model was trained with; Google's GGUF
  is a converted copy, and its mismatch is caused by a missing `add_space_prefix` key under this llama.cpp version
  (tested above). **The check passes 2,084 of 2,084.**
- **Limits of this check.** (1) It covers input prompts recorded with the mock, so real-model verifier prompts are
  not covered. (2) The ~6,400 differing scores and token types between our file and Google's GGUF are unexplained,
  with no effect on any of the 2,084 prompts.

## Smoke test on the board (Llama 3.2 3B, new pure file, NPU lane, `-c 4096 -np 1 -fit off`)
GGUFs pushed to `/data/local/tmp/llm/`; `sha256sum` on the board equals the laptop value for all 4. Lanes load only
files in `bench.board.CANDIDATES` whose board sha256 matches (tested), so the old impure file cannot be loaded.

### B. Offload and where the final logits step runs
One-off diagnostic launch (`GGML_SCHED_DEBUG=2`, `-lv 5`, one 38-token request; not a timed run).
Log: `logs/smoke_B_diag_sched_npu.log`.

| item | value from the startup log |
|---|---|
| tensor types (server's own loader) | f32 58, q4_0 196, q8_0 1: identical to `bench.gguf_types` |
| layers offloaded to HTP0 | **29 of 29** (27 repeating + the output layer; `offloading output layer to GPU`) |
| HTP0 model buffer | **1,911.90 MiB** (nonzero) |
| CPU model buffer | 399.23 MiB |
| HTP0 KV buffer / compute buffer | 448.00 MiB / 64.01 MiB |
| slots, context | `n_slots = 1`, `n_ctx_slot = 4096` |

- **Where the token embedding lives: in both places.** The loader keeps one copy on the CPU
  (`'token_embd.weight' (q8_0) ... using CPU`) for the input lookup, and a second copy inside the HTP0 buffer for the
  tied output layer. Independent check: the Q8_0 table is 128,256 x 3,072 values at 34 bytes per 32 values =
  399.23 MiB, equal to the CPU buffer; and the HTP0 buffer (1,911.90 MiB) equals llama-quantize's total quant size
  for ALL tensors (1,911.90 MiB), which is only possible if the embedding is counted there too.
- **The final logits step runs on HTP0.** Scheduler assignment, every graph: `node #873 (MUL_MAT): result_output
  [HTP0]`, with inputs `token_embd.weight (399M) [HTP0]` and `result_norm [HTP0]`.
- **The only CPU operation is the input embedding lookup** (`GET_ROWS embd`, one per graph, 10 of 10 graphs). Every
  other node is on HTP0: per graph 197 MUL_MAT (196 weight matrices + the logits), plus RMS_NORM, MUL, ROPE, ADD,
  SWIGLU, FLASH_ATTN.
- Limit: this is the scheduler's assignment, printed by the server. It is not a hardware trace of the DSP.

### C. Cost of `-lv 4` (numbers for the human's fixed rule)
Same diagnostician prompt (dev_A01-shaped, ep_A01 tick 64), `max_tokens` 256, through `LlamaServerBackend`; 3 calls
per launch, 60 s cooldown before each launch and between calls. Raw: `logs/smoke_C.json`.

| launch | call | prompt tok | answer tok | prefill tok/s | decode tok/s | chip before (CPU/NPU C) |
|---|---|---|---|---|---|---|
| `-lv 4` | 1 (first of launch) | 2,081 | 182 | 932.32 | 16.378 | 42.2 / 37.4 |
| `-lv 4` | 2 | 2,081 | 182 | 900.28 | 16.257 | 35.9 / 35.1 |
| `-lv 4` | 3 | 2,081 | 182 | 896.83 | 16.208 | 36.3 / 35.9 |
| default | 1 (first of launch) | 2,081 | 182 | 921.12 | 16.199 | 44.9 / 39.7 |
| default | 2 | 2,081 | 182 | 901.25 | 15.992 | 37.4 / 36.6 |
| default | 3 | 2,081 | 182 | 897.45 | 16.033 | 36.4 / 37.0 |

| calls 2-3, mean | `-lv 4` | default | `-lv 4` relative to default |
|---|---|---|---|
| prefill tok/s | 898.56 | 899.35 | -0.09% |
| decode tok/s | 16.233 | 16.013 | +1.37% |

- **Rule applied (fixed by the human before these numbers):** `-lv 4` lowers neither rate by more than 5%, so
  **`-lv 4` stays everywhere** (`bench.board.TIMED_LOG_LEVEL = 4`).
- Limits: two calls per condition, one prompt, one model. This shows no large cost; it is not a precise estimate.
- The reply text was identical in all 6 calls (temperature 0, seed 0).
- These are the first rates on the recipe file: about 900 tok/s prefill and 16.1 tok/s decode at a 2,081-token
  prompt. They are NOT comparable with the earlier 839 / 11.7 tok/s (impure file, other build, conditions unknown).
- The first call of a launch prefills 2.5-3.7% faster than calls 2-3 and starts on a warmer chip (42-45 C vs 36-37 C,
  right after the model load). Cause not investigated.

### D. Gemma stops by itself; Qwen3 thinking-off mechanism
One short prompt each on the NPU lane (`logs/smoke_D.json`, startup logs `logs/smoke_D_lane_npu_*.log`).

| model | request | stop reason | answer tok (cap) | `<end_of_turn>` in text | reasoning text | layers on HTP0 | HTP0 buffer |
|---|---|---|---|---|---|---|---|
| Gemma 3 1B QAT | plain | `stop` | 33 (256) | no | none | 27 / 27 incl. output | 680.82 MiB |
| Qwen3 1.7B | `chat_template_kwargs: {enable_thinking: false}` | `stop` | 10 (400) | no | none | 29 / 29 incl. output | 1,071.77 MiB |
| Qwen3 1.7B | default (thinking on) | `length` | 400 (400) | no | 1,877 chars, content empty | same launch | same |

- **Gemma: the reply stopped by itself** (stop reason `stop`, not the token limit) and contains no `<end_of_turn>` text.
- **Qwen3 thinking-off mechanism, recorded as the committed rule requires:** per-request
  `chat_template_kwargs: {"enable_thinking": false}`. Evidence: with it, no reasoning and a 10-token answer; without
  it, the whole 400-token budget went to reasoning and the content was empty. At this llama.cpp the server moves
  thinking into `message.reasoning_content`, so a `<think>` test on the content alone would miss it: the screening
  check fails Qwen3 on a `<think>` tag in the content OR any non-empty `reasoning_content`.
- For both models the server's loader type counts equal `bench.gguf_types`.

### Go-ahead conditions (human, fixed in advance): all hold
| condition | result |
|---|---|
| every layer and the final logits step on HTP0, nonzero HTP0 buffer | yes: 29/29, `result_output` on HTP0, 1,911.90 MiB |
| Gemma tokenizer check identical on every dev prompt | yes: 2,084 / 2,084 against the accepted reference |
| the Gemma call stopped by itself | yes: stop reason `stop` at 33 of 256 tokens |

## Campaign crashes (2026-10-03 evening): diagnosis and runner fixes
**Correction:** launches 2 and 3 were NOT stopped by power cuts. Each died on an uncaught `InfraFailure` (below).
**The board reboot at about 20:08 is unexplained:** the laptop kernel log shows the board leaving USB at 20:02:45 and
returning at 20:08:35, 13 minutes after launch 2 died; the board records only `sys.boot.reason=reboot` (no panic,
thermal or watchdog reason); `pstore` and `dmesg` are not readable as the shell user; nothing in the repo issues a
reboot.

| launch | job | failing call | prompt sha | prompt tok | calls finished on that lane |
|---|---|---|---|---|---|
| 1 (18:37) | `B/round1/llama32-3b/dev_A01` | tick 68 diagnostician | `1c163cd2dff3` | 1,910 | 31 |
| 2 (19:14) | `B/round2/llama32-3b/dev_B01` | tick 72 verifier | `6ec2c628324f` | 1,145 | 36 |
| 3 (21:21) | same | tick 97 verifier | `44e660ee6493` | 1,108 | 35 |

- Each failing call succeeded when the next launch reached it; earlier verifier calls had succeeded.
- Server log (launch 3): the request reached `init sampler`, then nothing for 120.7 s (no prefill timing, no error)
  until the client's cancel. The server's host prompt cache (`--cache-ram`, default 8,192 MiB, active although every
  request sends `cache_prompt: false`) held 33 prompts, 6,797.6 MiB, on an 11.1 GiB board; each 3B prompt costs
  0.1094 MiB per token = the 3B's F16 KV size. The call before each failure was slower (decode 12.35 / 14.30 / 12.05
  tok/s against about 16); launch 3's last good call ended at CPU 93.8 C. Memory and heat are not separated by this
  data: the soak test below does that.
- **Why no retry:** `python -m bench.campaign` ran the file as `__main__`, and `main()` then imported
  `bench.campaign` again (default `--board`; also `tests.fake_board`), creating a second module whose
  `InfraFailure` class the `except` clauses did not match. Exit 1, no retry, STATUS.md not updated. The in-process
  tests had one module copy; the command-line tests never injected a fault.
- **Fixed (no board):** (a) one module copy however launched, with command-line tests for exit 2 (3 retries, stop
  reason in STATUS.md) and exit 3; (b) per-attempt call logs, exclusive-create, archived to
  `results/board/attempts/` after a failure or a kill (the relaunch had overwritten launches 1 and 2's records;
  their failing calls were reconstructed from the reply cache); (c) STATUS.md's attempt number from the manifest.
  Each with a test and a caught mutation (`PYTHONDONTWRITEBYTECODE=1`). 140 tests pass.

## HUMAN OVERRIDE 2026-10-05: the model-choice stop is overridden; Llama 3.2 3B is the chosen model
**Decided AFTER both stage C results were seen. The rule itself is unchanged and passed no model, twice.**
- 2026-10-04 22:43-23:24 (second launch, dev runs for every passing model): Qwen3 1.7B and Gemma 3 1B were dropped
  after round 1 by the committed drop rule (first reply / after repair: 0.368 / 0.053 and 0.456 / 0.333; limits
  0.30 / 0.10). Stage C on the survivors (3B, Qwen2.5 0.5B) again passed no model: 3B fails only "broken JSON after
  repair" (0.042 > 0.02); Qwen2.5 fails both JSON limits. Exit 3.
- Cause of the broken JSON, read from the call logs (not a model run): first replies cut off at the 256-token answer
  cap. 3B: 13 at the cap, all unparseable, 3 repaired, 10 of its 11 final failures. Qwen3: 21 of 21 unparseable first
  replies were at the cap. Gemma: 25 of 26. The cap, the limits and the agent are unchanged.
- **Override (human, short of board time): the campaign continues past stage C with Llama 3.2 3B as the chosen
  model**, the only candidate that completed all 3 dev rounds and fails a single constraint. Code:
  `bench.campaign.PICK_OVERRIDE`. The stage C record keeps the rule's own result (`rule_pick: null`) next to the
  override, and the reporting baseline file carries the override note.
- **What this means for every number from the reporting run:** it is the 3B at a 256-token answer cap, where about
  4% of diagnosis ticks have a cut-off answer and fall back to the deterministic ranking. It is NOT a model that
  passed the pre-registered rule, and must not be described as one. The other three candidates were not tested at
  any other cap.
- The ranking follow-up (stage E) is left as committed: it runs after the reporting run if the 3-episode verdict is
  INCONCLUSIVE (it is, for the 3B). The human may stop the campaign after stage D; that would be recorded.

## HUMAN DECISION 2026-10-04: dev runs for every model that passes screening (replaces option C)
**Decided AFTER the campaign's stage C result was seen** (no model passed; `reports/campaign_watch.md`). Option C
(dev runs only for Llama 3.2 3B plus the fastest other model) is replaced: **every model that passes screening gets
the 3 dev rounds, interleaved by episode, with the same early-drop rule (0.30 / 0.10 after round 1), then
`bench/model_choice.py` is applied unchanged to all survivors.** No limit is changed. Reason (human, as proposed on
2026-10-03 before any result and adopted now): the speed-only pick left two candidates, Qwen3 1.7B and Gemma 3 1B,
untested, and the measured 3B call time makes the extra dev runs affordable.

- The stage A screening and the dev results already in `results/board` for the 3B and Qwen2.5 0.5B are kept and reused
  (same flags, same sampling, same code paths for the agent); only the missing episodes are run.
- The first stage C record (no pick, two models) is kept in the manifest under `superseded`, and its
  `C/model_choice.*` files are kept under a `.superseded-1` name; stage C is then recomputed on all survivors.
- Known before this run, for the record: 10 of the 3B's 11 after-repair failures were first replies cut off at the
  256-token answer cap (13 first replies hit the cap; the repair prompt shows only the first 800 characters of the
  failed answer and rescued 3). 1 failure was not cap-related. The cap and the limits are unchanged by this decision,
  so the 3B's stage C result is expected to stay as it is.

## Campaign launch of 2026-10-03 23:07 (after the fixes): NOT the offline proof
Launched by the human in tmux (session `campaign`, wrapper `~/campaign_loop.sh`: waits for adb, sleeps 10 minutes,
runs `scripts/board_campaign.sh all`, relaunches only on exit code 2, at most 12 times). **The laptop's Wi-Fi is on
for this run, so it is not evidence of offline operation.** Model calls go over USB (adb forward) and the campaign
itself uses no network, but that is by construction, not shown by this run; each episode summary records the laptop's
network state. Overnight watch log: `reports/campaign_watch.md`.

## Human decisions after the soak test (2026-10-03, recorded before any further board run)
**Override of the pre-set reading, decided AFTER seeing the result.** The rule said run B's server RSS must be "not
growing", with no tolerance. The human calls that wording a drafting error: run B grew about 0.5 MB per call against
about 190 MB per call in run A, MemAvailable stayed at 6.2 GB, and every other condition passed. **Memory exhaustion
from llama-server's host prompt cache is accepted as the cause of the stalls.** Recorded as an override: the reading
was NOT CONFIRMED as written, and my own 2% margin (written into `bench/soak_cache.py` before the runs) also failed
(4.5%).

1. **Every lane, every model, both lanes, adds `--cache-ram 0`.** Reason: no prompt is ever reused, the cache only
   grows, and it penalises models with a larger state per token.
2. **The campaign restarts from stage A with the new flags.** The results so far are kept, not used for any decision
   or timing: `results/board` moved to `results/board_aborted_20261003` and committed. Reason: the 3B calls were timed
   on a degrading server and the server flags have changed, so every number must come from one configuration. No
   model-choice result has been looked at.
3. **Peak in-episode temperature** (93.8 C CPU was seen) is reported per episode from now on. It is not a gate.
4. **Memory guard.** Before each episode, log server RSS and board MemAvailable. If MemAvailable is under 2 GB, restart
   the lane before that episode and log the restart. Never restart inside an episode.
5. **Unexplained:** launch 3's last good call ended at 93.8 C CPU, against a 72.1 C peak in soak run A at the same
   stall, with the same prompts. Not investigated.
6. **Open item, to be tested after the campaign:** replies at temperature 0 and seed 0 differed between soak runs A
   and B on 10 of 37 calls. **Until tested, no claim is made that replies are reproducible across launches.**

### Part 3 as implemented
- `--cache-ram 0` in `bench.board.lane_command` (the only place a lane command is built); test that every lane, model
  and log level has it; mutation (flag removed) caught by 3 tests.
- `results/board` moved to `results/board_aborted_20261003` with a README; committed in full (1.1 MB).
- Peak in-episode temperature: `meta.chip_temp_peak` in every episode summary (max CPU and NPU over the start, every
  live call and the end reading; cache hits make no reading). Reported, not a gate.
- Memory guard: `meta.memory_guard` in every episode summary (server RSS and MemAvailable before the episode). Under
  2 GB the lane is stopped and restarted before the episode; the restart goes into the manifest (`lane_restarts`) and
  STATUS.md. "2 GB" is implemented as 2 GiB = 2,097,152 kB, the stricter reading (restarts slightly earlier). The guard
  runs only before an episode, so it can never restart a lane inside one. Six mutations caught (threshold, `<=`, no
  restart, guard not called, peak not updated, peak keeping the minimum).

## Soak test: memory vs heat (2026-10-03, 22:15-22:53) — reading NOT CONFIRMED, stopped
`bench/soak_cache.py` (committed before the runs, `1dda41c`). Fresh 3B NPU lane per run, campaign request body,
the same 50 prompts in the same order: the first 50 distinct prompts of the crashed job
`B/round2/llama32-3b/dev_B01_tube_leak` (ticks 51-81; 34 diagnostician, 16 verifier; real calls, so real sizes:
348-2,036 prompt tokens). Client timeout 300 s, to see a stall longer than 120 s. Raw: `reports/data/soak_{A,B}.jsonl.gz`.
Run A = current flags; run B = current flags + `--cache-ram 0` (`-cram, --cache-ram N ... 0 - disable`, confirmed in the
pinned server's `--help`).

| | run A (current flags) | run B (`--cache-ram 0`) |
|---|---|---|
| calls completed | 36 of 50; call 37 (tick 72 verifier, `6ec2c628324f`, the call launch 2 stalled on) got no reply in 300 s | 50 of 50 |
| longest call | 300.05 s (stall) | 18.54 s |
| prefill, first 5 calls (mean) | 994.2 tok/s | 920.8 tok/s |
| prefill, lowest | 795.9 tok/s (-19.9%, call 36) | 845.6 tok/s (-8.2%) |
| decode before the stall | 16-17 tok/s, then 14.8 and 11.2 on calls 34 and 36 | 15.8-16.3 tok/s throughout (diagnostician) |
| server prompt cache | grew about 240 MiB per call: 1,708 MiB at call 10, 6,832 MiB at call 37 | disabled (no cache lines) |
| server RSS | 512 -> 6,598 MB (call 33), then fell to 908 MB as the kernel paged it out | 512 -> 535 MB, a steady ~0.5 MB per call |
| board MemAvailable | 3.5 GB -> 0 at the stall | 6.29 -> 6.24 GB |
| CPU / NPU peak | 72.1 / 66.8 C | 67.4 / 64.8 C |
| idle reference, thermal wait | 32.5 C, 0 s | **40.5 C**, 0 s (measured while the board was still warm from run A) |

**Reading (rule fixed by the human before the runs):** run A's half holds (a call over 120 s). Run B meets three of
four conditions (50 calls; none over 60 s; prefill within 10% of its first 5 calls, worst -8.2%) but **its server RSS
grew** (512 -> 535 MB, monotonically). By the rule's words that is "anything else": **stop and report**. The
committed script's own check also says NOT CONFIRMED (it allows 2% RSS growth; B grew 4.5%; the 2% was my choice,
written before the runs). Part 3 (cache-off flag, restart from stage A, moving `results/board`) was then done after the human's override (next section).

What the data shows, for the human's decision (not acted on):
- Run A stalled at the same call as launch 2, at the moment board MemAvailable reached 0, with the chip at 70 C;
  run B at the same calls and similar temperatures (66-67 C) never slowed. Heat alone does not reproduce the stall.
- Run B's RSS growth is about 23 MB over 50 calls, against about 6 GB in run A. Cause not investigated.
- Run B started warmer: its idle reference was re-measured right after run A (40.5 C against 32.5 C), so "within
  5 C of idle" was a looser gate for B. B's first-5 prefill is 7% below A's; not separated from this.
- **The replies are not identical across the two runs** at temperature 0, seed 0: decoded-token counts differ on 10
  of the 37 calls both runs completed (e.g. tick 68: 256 vs 169 tokens). Cause not investigated (server flags differ
  between the runs; the smoke test's 6 repeats on one lane were identical). This bears on the campaign's reply cache,
  which assumes a reply depends only on (model, flags, sampling, prompt): the flags are in the key, so a cache built
  under one flag set is not reused under the other.

## Human decisions on two runner choices (2026-10-03, before any screening or dev result)
1. **Stage C floor = the mock run of the same 3 dev episodes.** Reason: like-for-like with the models' scores. The
   committed 0.501 is to be printed beside it. Current runner: the mock floor is in `C/model_choice.txt`/`.json`;
   0.501 is NOT printed by the runner (adding it is a code change, not made); compare by hand with `FLOOR_DEFAULT`.
2. **"Measured verified-diagnosis time" = mean server time of the screening calls, short answers counted as they
   are**, with the rate-based projection printed beside it for every model. Current runner: `A/screening.json` holds
   `measured_verified_s` and `projected_verified_s` for all 4 models.

**Proposed amendment NOT made: option C stays.** The human proposed dev rounds for every model that passes
screening, to be made only if it were a job-list or config change. It is not: it changes the stage A selection,
the stage B model list and its stop rule (`both dropped` -> `all dropped`), and the tests that assert option C. Per
the human's condition, nothing was changed; the committed runner (`a75bdc1`) is unchanged.

## Campaign runner (built, tested on a fake board, not launched)
Code: `bench/campaign.py`, `scripts/board_campaign.sh`; tests `tests/test_campaign.py` with `tests/fake_board.py`
(a local HTTP server that speaks `/health`, `/tokenize` and `/v1/chat/completions`, with fault injection).
`fieldmind/` is untouched; `bench/harness.run_episode` gained an optional `on_tick` callback (telemetry only; the
mock reporting run still equals `single_v3_summary.json` on every key outside latency: 1,659 paths, 0 differences).

Launch, once, from the repo root: `tmux new -s campaign 'scripts/board_campaign.sh all'`.
Status: `results/board/STATUS.md`. Exit codes: 0 finished, 2 infrastructure stop, 3 stopped by a committed rule.

| requirement | how it is met | test |
|---|---|---|
| ordered job list, manifest, resume | `results/board/manifest.json`; a job is complete only if its summary file exists (written last, atomically) | `test_complete_episode_is_skipped_incomplete_is_redone` |
| per-call JSONL, flushed at once | `CallLog`: flush + fsync per record; fields: laptop time, stage/phase, model, sha256, server flags, episode, tick, lane, prompt sha + full prompt, raw reply, parse status, stop reason, server timings (None if missing), chip temperature | `test_call_is_logged_at_once_with_every_field`, `test_missing_server_timings_are_none` |
| per-episode record | git commit, code hash, llama.cpp commit (read from the board), full config, start/end temperature, thermal wait, network state, energy `null` | full dry run |
| reply cache | key = sha256(model sha, server flags, sampling params incl. `max_tokens` and extra body, prompt sha); hits are marked and carry no timing | `test_cache_*` (3 tests) |
| infrastructure failures | `CampaignBackend` raises `InfraFailure` (connection, HTTP error, timeout, malformed body, failed temperature read); the episode is INCOMPLETE, the lane is health-checked and restarted, 3 retries, then a clean stop with the position in `STATUS.md` and the manifest | `test_infra_failure_is_retried_and_never_scored` (4 fault types), `test_adb_drop_is_infrastructure`, `test_stops_cleanly_after_three_retries_and_resumes`, `test_run_all_returns_infra_exit_code` |
| thermal gate | within 5 C of the idle temperature measured at campaign start, 15 s polls, 15 min cap, wait logged | 2 tests |
| `results/board/` tracked, commit per stage, never push | `.gitignore` un-ignores it (scratch `tmp/` and `cache/` stay ignored); `git commit -- results/board` after each stage | not tested (tests run with `--no-commit`) |
| one script, stages A-E | `scripts/board_campaign.sh all` under `systemd-inhibit` | full dry run |
| status file | `STATUS.md` rewritten after every episode | full dry run, stop tests |
| stage rules | A: option C; B: interleaved rounds and the 0.30 / 0.10 drop; C: `bench.model_choice` run unchanged as a subprocess; D: round-robin N, A, B, C, D, E; E: only if the 3-episode verdict is INCONCLUSIVE, reusing the 3 episodes | 9 tests |
| kill and resume | `SIGKILL` delivered mid-episode to the real CLI process, then relaunch | `test_kill_minus_9_mid_episode_then_resume_is_identical` |

### Verification (runner)
| step | result |
|---|---|
| ran the module | full fake campaign A-E through the real CLI: exit 0, 50 jobs, 4,955 model calls, 26 s. Real-board interface exercised once outside the campaign: `bench.gguf_types` over adb OK, lane start, offload parsed (29/29, 1,911.9 MiB), `/tokenize`, one 700/60 and one 350/30 call (server prompt tokens exactly 700 and 350; 60 and 30 answer tokens) |
| self-tests | 135 passed / 0 failed (29 new in `tests/test_campaign.py`; the suite now takes about 80 s) |
| independent re-derivation | kill-and-resume: the resumed campaign's summaries, run records, model-choice output and stage decisions equal an uninterrupted run's on every non-timing field (99 files compared). The measured screening time is recomputed in the test from the raw call records and equals the runner's value; the fake projection equals the hand value 1050/900 + 90/16 = 6.79 s |
| mutation check | 23 distinct mutations, `PYTHONDONTWRITEBYTECODE=1`, all caught: cache-hit timings kept; log not flushed; cache key without server flags; without sampling; hit not marked; no retry; retries 3 -> 1; HTTP 500 returned as a model reply; complete without a summary; thermal margin 5 -> 10; max wait 900 -> 300; second model slowest; screening 700 -> 800; reasoning text unchecked; offload ignoring layer count / output layer / zero buffer; drop limit 0.30 -> 0.60; family order; adb drop ignored; cache not reloaded after a kill; stage E condition inverted; no-pick not stopping. Two were first NOT caught for the right reason (offload layer count: no test; drop limit: the test was failing anyway); tests were added/fixed and both re-run |

### Decisions taken in the runner (conservative choice each time; all reversible before launch)
1. **Cache hit = replay, timings nulled.** A hit returns the stored reply; afterwards its call timings and the
   envelope latency are set to `None` in the run record, so `bench.model_choice` (unchanged) leaves it out of rates,
   the projection and call-time means. Token counts stay. Per-tick wall time (S1, S7) of a tick answered from the
   cache is the real, short wall time; such ticks are marked `cache_hit`.
2. **Screening calls never use the cache** and start with one warm-up call that is logged and excluded (the first
   call of a launch was 2.5-3.7% faster in prefill in step C).
3. **Screening prompts** are the first real diagnostician prompt (dev_A01 tick 37) and verifier prompt (dev_B01 tick
   51) from the mock runs, cut with the server's own tokenizer to exactly 700 / 350 prompt tokens with the output
   schema kept at the end. Answers are capped at 60 / 30; a shorter answer is counted (`short_answers`), not padded.
4. **"Measured verified-diagnosis time"** = mean server time (prefill + decode) of the five 700/60 calls + mean of
   the five 350/30 calls. The projection beside it is `model_choice.projected_s` at the same calls' pooled rates.
5. **Screening pass** = GGUF check + offload check (+ no thinking for Qwen3). The 10 s projected limit is NOT a
   screening gate; it is applied by `model_choice` in stage C, as committed.
6. **Qwen3 thinking off** = per-request `chat_template_kwargs: {"enable_thinking": false}`; it fails screening on a
   `<think>` tag in the content OR any non-empty `reasoning_content`.
7. **Idle temperature** is measured once, at campaign start, with no lane running (stable to 0.5 C), and kept in the
   manifest across restarts. If the board is warm at launch, the gate is correspondingly looser.
8. **Stage E reuse** of the 3 model-choice episodes requires an unchanged hash of `fieldmind/`, `bench/`, `configs/`
   and `data/kb/` (result commits do not move it); otherwise all 13 are run.
9. **The mock column** for stage C's floor is the mock run of the same 3 dev episodes, computed at stage C.
10. **Baseline name:** `results/baselines/single_v3_real_<model>_npu_summary.json`.
11. **Timeout:** the config's 120 s per call. A timeout is infrastructure (human rule), so a model that needs more
    than 120 s for one call would stop the campaign, not be scored.

## What was built
- **llama.cpp pin:** `ggml-org/llama.cpp` tag `b11371`, commit `99b95488cac0f00ce3f05af113a8c1e287753f87`.
- **Android package:** container `ghcr.io/snapdragon-toolchain/arm64-android:v0.7` (digest `sha256:c012b817...`,
  NDK r29, Hexagon SDK 6.6.0.0), preset `arm64-android-snapdragon-release` plus `-DLLAMA_BUILD_SERVER=ON
  -DLLAMA_OPENSSL=OFF`. On the board at `/data/local/tmp/llm/llama.cpp`; 134 files, board sha256 == laptop sha256.
  `llama-server --version` on the board: `build 11371, commit 99b95488c`; `--list-devices` shows `HTP0: Hexagon`.
- **Host build:** `llama-quantize` from the same commit. Convert venv from `requirements-convert_hf_to_gguf.txt`.
- **Candidates** (`bench/build_candidate_gguf.sh`; `convert_hf_to_gguf.py --outtype auto`, then
  `llama-quantize --pure --token-embedding-type q8_0 --output-tensor-type q8_0 ... Q4_0`):

| model | source repo @ revision | official? | source precision | bytes | sha256 | matrices |
|---|---|---|---|---|---|---|
| Llama 3.2 3B Instruct | `unsloth/Llama-3.2-3B-Instruct` @ `006f5dcd13` | mirror (Meta gated) | BF16 | 2,012,612,832 | `5aa3ece50ab33d09a7181888a75f8755f924c662dc99626e7f45440adfeadcdb` | 196 Q4_0, token_embd Q8_0 (output tied) |
| Qwen3 1.7B | `Qwen/Qwen3-1.7B` @ `70d244cc86cc` | official | BF16 | 1,460,395,904 | `4a4ebf10354822c45dfa38b9248a42a59ebae53c0ad992147b881dd7ed09c56c` | 196 Q4_0, token_embd Q8_0, output.weight Q8_0 |
| Gemma 3 1B QAT | `unsloth/gemma-3-1b-it-qat` @ `82120d4d65` | mirror (Google gated) | BF16 | 720,425,280 | `3a229fece56839877093042f0699939d9c4a65691dda81999f80ff27dae2cc5f` | 182 Q4_0, token_embd Q8_0 (output tied) |
| Qwen2.5 0.5B Instruct | `Qwen/Qwen2.5-0.5B-Instruct` @ `7ae557604adf` | official | BF16 | 352,154,624 | `00d3bb3f9210f132ef246cc5db2a7d8c9f8b63785679a95aef3558875b50e341` | 168 Q4_0, token_embd Q8_0 (output tied) |

Files: `~/fieldmind-build/gguf/<name>-Q4_0-pure-embq8.gguf`.

## Verification
| step | result |
|---|---|
| ran the module | `bench/build_candidate_gguf.sh` ran for all 4, exit 0 each; `bench.board env/temp/start/log/stop` ran against the board (before it was unplugged) |
| self-tests | 103 passed / 0 failed (`.venv/bin/python -m pytest`), 5 new in `tests/test_board_env.py` |
| independent re-derivation | tensor-type counts of each candidate by llama.cpp's own `gguf-py` `GGUFReader` (a different parser): identical to `bench.gguf_types` for all 4, relative difference 0. Also: for the impure on-board file, llama-server's loader printed `f32 58, q4_0 193, q4_1 3, q6_K 1`, identical to `bench.gguf_types` on its header (the "first real file" check left open in `phase0b_lanes_model_choice.md`) |
| mutation check | `CTX_SIZE` 4096 -> 2048: caught by `test_lanes_always_use_ctx_4096_np_1`. `ADSP_LIBRARY_PATH` removed from the launch: caught by `test_lane_command_env_and_offload`. `drop_pythonpath()` removed: caught by `test_board_script_drops_pythonpath`. `env=clean_env()` removed from the adb call: caught by `test_adb_subprocess_gets_clean_env`. Build-script gate: fed the impure bartowski header, the `verdict OK` grep fails (exit 3 path) |

## Direction checks
None: no physical response was modelled in this stage.

## Constants introduced
| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `CTX_SIZE` | 4096 | human decision 2026-10-03 (equals `llm.llamaserver.ctx_size`, itself derived in the config) | fixed | NPU speed, KV buffer size; every lane launch |
| `N_PARALLEL` | 1 | human decision 2026-10-03; CLAUDE.md "one request at a time" | fixed | one slot per lane |
| `TIMED_LOG_LEVEL` | `-lv 4` | found on the board: the default level prints no loader lines; kept for timed calls by the human's 5% rule (measured -0.09% prefill, +1.37% decode) | 4 or higher | whether the NPU-confirmation rule can be checked on every launch |
| `-fit` | off | conservative: no argument may be adjusted silently (HTP0 reports 0 MiB free) | on/off | layer placement |

## Numbers that changed
| quantity | old | new | note |
|---|---|---|---|
| Llama 3.2 3B file | 1,921,909,280 B (bartowski: 193 Q4_0, 3 Q4_1, token_embd Q6_K) | 2,012,612,832 B (196 Q4_0, token_embd Q8_0) | ratio 1.047. The earlier 839 / 11.7 tok/s belong to the old file |
| tests | 98 | 135 | 29 for the campaign runner, 8 for the board helpers |

## Decisions taken
1. **Toolchain image pulled.** The message said the snapdragon-toolchain container was already in `podman images`; it
   was not (only `ubuntu:22.04`). Pulled the image the pinned llama.cpp docs name. NDK is r29 in the image.
2. **Safetensors mirrors, not GGUF mirrors, for the two gated models.** The instruction for a gated repo was an
   F16/BF16 GGUF mirror. I used safetensors mirrors whose weight files have the same sha256 as the official gated files
   (the Hub publishes LFS hashes for gated repos): both Llama shards equal Meta's; Gemma `model.safetensors` and
   `tokenizer.model` equal `google/gemma-3-1b-it-qat-q4_0-unquantized`. That keeps the recipe's primary path
   (official weights through `convert_hf_to_gguf.py`, lossless BF16). Reversible: nothing downstream has run.
3. **Gemma: Google's QAT GGUF not used.** Checked on the byte-identical copy in `tetf/gemma-3-1b-it-qat-q4_0-GGUF`
   (sha256 `95e5b8d8...` equals Google's): `token_embd.weight` is F16, so it does not match the recipe.
4. **Gemma EOS restored to 1.** unsloth's tokenizer files set EOS to `<end_of_turn>` (106). Google's own GGUF and the
   `mlx-community/gemma-3-1b-it-qat-bf16` config say `<eos>` (1). Built with
   `--override-kv tokenizer.ggml.eos_token_id=int:1`; verified 1 in the output.
5. **Llama metadata left as converted.** Chat template, tokens, token types, merges, BOS/EOS are identical to the
   Meta-derived `bartowski/...-f16.gguf` header. unsloth adds `padding_token_id` 128004; left in (unused here).
6. **`-lv 4` and `-fit off` added to the launch command** (see Constants).
7. **Build tree outside the repo:** `~/fieldmind-build/` (new folder; nothing existing was changed).
8. **`logs/` added to `.gitignore`.**

## Disagreements recorded, not resolved
- **The premise of the "only Q4_0 or Q8_0" rule does not hold at the pinned llama.cpp, by source.** CLAUDE.md says
  K-quants silently fall back to the CPU on HTP. At commit `99b9548`, `ggml_hexagon_supported_mul_mat`
  (`ggml/src/ggml-hexagon/ggml-hexagon.cpp`) accepts Q4_0, Q4_1, Q8_0, IQ4_NL, MXFP4 and Q2_K-Q6_K weights. So the
  old impure file (Q4_1, Q6_K) may well run fully on HTP0 with this build. **Not tested on the board** (no request
  was timed or traced on that file). The rule and the recipe are human decisions and are unchanged; the recipe still
  gives one uniform, reproducible file per model. Whether the rule's stated reason should be reworded is the
  human's call.
- Gemma tokenizer arrays: against Google's own GGUF, my conversion differs in 30 token spellings (ids 138-167, `▁`
  runs vs spaces), 6,414 scores and 6,407 token types. The sentencepiece `tokenizer.model` is byte-identical, so I
  attribute this to the converter version. **Not tested** (would need Google's file converted by the pinned converter,
  which needs the gated repo). Recorded as unexplained-but-probable, not as verified.
- Gemma chat template: Google's GGUF stores a flattened one-line template whose system prefix ends in one newline; the
  HF template (unsloth and mlx copies, identical to each other) uses two. Irrelevant to this project's requests
  (single user message, `LlamaServerBackend._body`); it would matter if a system message were ever sent.

## Blocked / needs a decision
- Launching the campaign is the human's action: `tmux new -s campaign 'scripts/board_campaign.sh all'`.
- Whether the Q4_0/Q8_0 rule's stated reason should be reworded, given the Hexagon source finding (see Disagreements).

## What I could not verify
- That every operation runs on the NPU. The dry run (impure on-board file, launch mechanics only, no request sent, no
  timings taken) showed `offloading output layer to GPU`, `offloaded 29/29 layers`, `HTP0 model buffer size = 1845.95
  MiB`, `CPU model buffer size = 308.23 MiB`. That is weight placement, not execution. The 308 MiB CPU buffer is
  probably the input embedding table; not checked.
- Gemma: my Q4_0 tensors were not compared bit-for-bit with Google's QAT GGUF (needs the full 1 GB file).
- The unsloth small files (config, tokenizer_config) against the official gated ones, beyond the comparisons above.
- No accuracy or energy number exists for any candidate. Timing exists only for the smoke-test calls above.
- The campaign runner has never run a real episode on the board; only its board interface was exercised (one lane
  start and three calls). The per-stage `git commit` path is untested (tests run with `--no-commit`).
- A resumed real campaign can differ from an uninterrupted one in timing-based fields (rates, call-time means, the
  third tie-break of the model-choice rule), because redone calls answered from the cache have no timing.
- NPU replies were identical across 6 repeats of one prompt; determinism across lane restarts over a whole episode
  is assumed by the cache, not measured.
- Campaign duration is a planning estimate, not a measurement: mock call counts x 13.6 s per 3B call give about
  1.1 h (stage B, 3B only) + 8.8 h (stage D) + 4.5 h (stage E) = 14.4 h if the 3B is chosen, plus the second model's
  dev runs, thermal waits and repair calls.
- The server's own `predicted_per_second` differs from tokens / `predicted_ms` on the same call (19.15 vs 19.81 tok/s
  on one 30-token call). Unexplained; every rate in this project uses tokens / server ms, as `model_choice` does.
