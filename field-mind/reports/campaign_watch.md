# Campaign watch, night of 2026-10-03 / 04

## STOPPED BY A COMMITTED RULE at 02:41:54 (exit code 3). Not relaunched. Waiting for the human.
**Why:** stage C. `bench/model_choice.py` (run unchanged) passed **no model**, and the committed rule for that is
"stop and ask; no limit is relaxed". Stages D (reporting run) and E (ranking follow-up) did not run. Nothing was
relaunched, changed or acted on after the stop. Wi-Fi was on: not the offline proof. Mock numbers below are the
deterministic and retrieval layers only, not an agent result. Energy was not measured.

Hard constraints, 3 dev episodes (dev_A01, dev_B01, dev_C02), NPU lane, `-c 4096 -np 1 -fit off --cache-ram 0 -lv 4`:

| constraint (limit) | Llama 3.2 3B | Qwen2.5 0.5B |
|---|---|---|
| broken JSON, first reply (<= 0.10) | 0.061 pass | **0.389 FAIL** |
| broken JSON, after the one repair (<= 0.02) | **0.042 FAIL** | **0.290 FAIL** |
| projected verified-diagnosis time (<= 10 s; a projection) | 6.77 s pass | 2.24 s pass |
| all layers on HTP0 | pass | pass |
| every matrix Q4_0 / Q8_0 | pass | pass |
| **passes** | **no** | **no** |

Reported with it (`results/board/C/model_choice.json`, `.txt`):

| | mock (floor) | Llama 3.2 3B | Qwen2.5 0.5B |
|---|---|---|---|
| diagnostician calls | 262 | 262 | 262 |
| library group top-1 | 0.501 (committed 0.501) | 0.507 | 0.418 (flag: below the floor) |
| library top-1 | 0.419 | 0.394 | 0.302 |
| model citation faithfulness, raw | 1.0 | 0.828 | 0.179 |
| diagnostician envelopes ok / invalid | 262 / 0 | 251 / 11 | 186 / 76 |
| verifier envelopes ok / invalid | 32 / 0 | 69 / 40 | 2 / 21 |
| server prefill / decode tok/s | - | 890.1 / 16.10 | 2,939.3 / 47.91 |
| diagnostician call time, mean / p95 | - | 11.4 s / 15.9 s | 4.9 s / 10.7 s |
| answer tokens per call, mean; share at the 256 cap | - | 143.1; 0.047 | 210.5; 0.503 |
| ranking question (3 episodes) | INCONCLUSIVE | INCONCLUSIVE (n 169, belief 0.265, model 0.444) | BELIEF_DECIDES (n 32) |

- Screening (stage A): all 4 models passed the GGUF and offload checks. Measured verified-diagnosis time = projection
  for each: 3B 5.34 s, Qwen3 1.7B 3.51 s, Gemma 3 1B 2.63 s, Qwen2.5 0.5B 2.06 s. Option C therefore took Qwen2.5 0.5B
  as the second model; Qwen3 and Gemma got no dev run.
- Round-1 drop rule: 3B 0.035 / 0.035, Qwen2.5 0.14 / 0.088 (first reply / after repair): nobody dropped.
- **Relaunches and fixes tonight: none by me.** The wrapper's own first start (23:17:35) exited 2 because adb showed
  no device (USB dropped at 23:17:33 for 1 s, then 23:25:24-23:27:58; unexplained, before any call); the wrapper
  relaunched itself and the campaign ran from 23:37:59 to 02:41:54 in one process. Runner fixes used: 0 of 2.
- **Failed attempts: 0. Infrastructure errors: 0. Memory-guard restarts: 0.** Every job completed on attempt 1,
  including `B/round2/llama32-3b/dev_B01` (230 live calls), the job that stalled three times before `--cache-ram 0`.
- Memory before each episode: 3B server 489-490 MB, MemAvailable 6.2 GB; Qwen2.5 server 216-217 MB, 8.5 GB.
- Peak in-episode temperature (CPU / NPU C): 3B 68.2 / 65.2, 68.2 / 64.1, 69.0 / 65.2; Qwen2.5 57.0 / 51.7,
  63.2 / 56.0, **79.1 / 68.7**. The thermal gate waited 30-195 s before each episode and never timed out.
- Unexplained: the two USB drops before the campaign started; Qwen2.5's 79.1 C peak in round 3 against 57-63 C in
  its other two episodes; 29 cache hits in Qwen2.5's dev_B01 (identical prompts repeated within the episode), 1 in
  the 3B's dev_A01.
- Board at 03:16: connected, no llama-server running, no campaign or wrapper process.

Decisions for the human (not acted on): what the stage C result means, and whether any limit, the option C rule or
the candidate set changes. The runner will re-apply the same stop if relaunched as is.

Campaign launched by the human at 23:07:35 (tmux session `campaign`, `~/campaign_loop.sh`). Wi-Fi on: not the offline
proof. Watched by Claude under the human's written rules (relaunch only after exit 2 or a board drop; at most 2 runner
fixes; never after exit 3; nothing under results/ edited; no other board use). All times are laptop time.

## Checks
| time | running | stage | model | episodes done | lane RSS / MemAvailable (latest episode) | peak CPU / NPU C (latest episode) | notes |
|---|---|---|---|---|---|---|---|
| 23:12 | wrapper in its 10-min wait (campaign starts ~23:17:35) | - | - | 0 | - | - | board connected, no llama-server; results/board not created yet |
| 23:43 | yes (1 campaign, started 23:37:59) | A | gemma3-1b-qat | 2 of 4 screenings | - (stage A has no episode yet) | - | ODD: the wrapper's first start at 23:17:35 found 0 adb devices and exited 2; kernel log shows the board off USB and back at 23:27:58 (unexplained, before any campaign call); the wrapper relaunched by itself. idle 33.3 C, no failed attempt, no guard restart |
| 23:45 (addendum) | | | | | | | kernel log: USB disconnect 23:17:33 -> back 23:17:34 (same second as the wrapper's `adb shell svc power stayon usb`), then disconnect 23:25:24 -> back 23:27:58 (2.5 min, reboot-like). Both before the campaign's first call. Cause unexplained |
| 00:14 | yes | B (round 1) | qwen25-0.5b | A 4/4, B 1 of 6 | 489 MB / 6,240 MB (before B/round1/llama32-3b) | 68.2 / 65.2 | 3B finished dev_A01: 59 calls, 1 cache hit, no failed attempt (past the 31-call point where the aborted launch 1 died); thermal gate waited 30 s; no guard restart |
| 00:44 | yes | B (round 2) | llama32-3b | A 4/4, B 2 of 6 | 216 MB / 8,580 MB (before B/round1/qwen25-0.5b) | 57.0 / 51.7 (qwen25 ep); max over all episodes 68.2 CPU | the job that died 3 times (round2/llama32-3b/dev_B01) is at 95 live calls with no stall (old stall: call 37); decode 15-17 tok/s first to last; CPU 66 C; longest call 19.2 s; thermal gate waited 195 s before the qwen25 episode; no guard restart |
| 01:15 | yes | B (round 2) | llama32-3b | A 4/4, B 2 of 6 | 216 MB / 8,580 MB (latest finished episode, qwen25) | max over all episodes 68.2 CPU; current 3B episode 66-68 | dev_B01 on the 3B at 215 live calls (129 diagnostician, 86 verifier), tick 170 of 180, decode 15-17 tok/s, longest call 19.2 s, no failed attempt. Real 3B makes ~4x the mock's verifier calls, so episodes are longer than the planning estimate. Round-1 drop check (recorded, not acted on): 3B 0.035 / 0.035, qwen25 0.14 / 0.088 (first reply / after repair): nobody dropped. Checks go hourly from here |
| 02:15 | yes | B (round 3) | llama32-3b | A 4/4, B 4 of 6 | 216 MB / 8,548 MB (before B/round2/qwen25-0.5b) | 63.2 / 56.0 (qwen25 ep); max over all episodes 68.2 CPU | the 3B finished dev_B01 (the job that died 3 times) on attempt 1; round 2 done for both models; 3B now on dev_C02; no failed attempt, no guard restart, last error none |
| 03:16 | NO: exited 3 at 02:41:54 (rule stop, stage C) | C | - | A 4/4, B 6 of 6, C done (no pick) | 217 MB / 8,519 MB (before B/round3/qwen25-0.5b) | 79.1 / 68.7 (qwen25 round 3); 3B max 69.0 | not relaunched (rule: never after exit 3); board connected, no llama-server; summary at the top of this file. WATCH ENDED |
