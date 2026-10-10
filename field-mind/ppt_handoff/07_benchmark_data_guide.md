# 07. How to read the benchmark data

Read this before computing anything from the uploaded files. It covers which runs exist, every file format and
metric, and the pitfalls that would make a slide wrong.

---

## 1. Which runs exist (all on the QIDK board, Llama 3.2 3B on the NPU)

| run (folder) | system | merge rule | prompt | episodes | date |
|---|---|---|---|---|---|
| `results/presentation_benchmark/single_agent_llama32-3b/` | single agent | (model order wins) | single-agent prompt, no grammar | N01, A01, B01, C01, D01, E01 | 5 Oct |
| `results/benchmarks/single_6mark/` | single agent | same | same | N01, A01 (B01 failed: board disconnect) | 7 Oct |
| `results/benchmarks/multi_v2_6mark/` | multi-agent v2 | `model` | v2 (group letters, Gemma note reader) | N01, A01, B01, C01, D01, E01 | 7 Oct |
| `results/benchmarks/multi_tiebreak_6mark/` | multi-agent v2 | `tiebreak` | v2 | same six | 7 Oct |
| `results/benchmarks/multi_nudge_6mark/` | multi-agent v2 | `nudge` | v2 | same six | 7 Oct |
| `results/benchmarks/multi_nudge_6.1mark/` | multi-agent v2 | `nudge` | v2 | A02, A03, B02, B03, C02, C03 | 7 Oct |
| **`results/benchmarks/multi_v3/`** | **multi-agent v3** | **`hybrid`** | **v3 (no letters, raw notes)** | **all twelve above** | **8 Oct** |

Common settings, every run:
- **mode:** lockstep (back to back: each tick waits for its own model calls, the next starts at once);
- **sampling:** temperature 0, seed 0;
- **server flags:** `-c 4096 -np 1 -fit off --cache-ram 0 -lv 4`;
- **multi-agent lanes:** Llama 3.2 3B on the NPU, Gemma 3 1B on the CPU;
- **episodes:** all from the **reporting set** (`ep_*`).

**Like-for-like comparisons:**
- the first four fault episodes (A01, B01, C01, D01): all five configurations;
- all ten fault episodes: nudge against v3 (and belief alone).

E01 and N01 have no cause to score (E01's fault has no library case; N01 is normal operation).

## 2. Files in a benchmark folder

```
results/benchmarks/<test name>/
  test.json                   settings and every run attempt
  RESULTS.md                  one table row per finished episode run
  temps.jsonl                 chip temperature before and after each episode
  <ep>_<arch>.summary.json    evaluation + call counts for one episode run  (arch = multi or single)
  <ep>_<arch>.log             the run's console output
  <ep>_<arch>.lanes.txt       server startup lines proving NPU / CPU placement
  <ep>_<arch>.run.json.gz     every tick (large; not needed, see data/episode_metrics.csv)
```

**`test.json`:**
- `params`: architecture, mode, overlay (config file) and its hash, merge rule, backend, model files per lane;
- `runs`: one entry per attempt, with status (`done`, `failed`, `interrupted`), git commit, chip °C before and after,
  cooling wait and wall seconds.

A failed attempt stays in the log; for example `multi_v3` B01's first attempt failed on a board disconnect and was
re-run.

**`<ep>_<arch>.summary.json`**, three blocks:

| block | fields |
|---|---|
| `meta` | test, episode, run number, params, start and finish (laptop clock), `wall_s`, git, `chip_c_before` / `chip_c_after`, `cool_wait_s`, `where`, `energy_mwh` (always `null`) |
| `evaluation` | the evaluator's output (section 4) |
| `calls` | `ticks`; total `calls`, `unusable`, `tokens_in`, `tokens_out`; `by_agent` with the same plus `median_call_s` per agent (diagnostician, verifier, text_reader) |

The 5 October single-agent summaries (`presentation_benchmark`) have `meta` and `evaluation` only, no `calls` block.
Their `meta` has the model, runner and start and finish times.

**`temps.jsonl`:** one line per reading: `{"episode", "when": "start|before|after", "t", "max_c", "cool_wait_s"}`.
`max_c` is the hottest of the CPU and NPU zones. `null` means the read failed (it happens when the board disconnects).

**`RESULTS.md` columns:**

| column | meaning |
|---|---|
| ticks | ticks in the episode |
| model calls / unusable | calls made; answers not used (wrong format, failed call) |
| tokens in | prompt tokens prefilled |
| group top-1 | headline accuracy |
| belief group top-1 | the same for the code-only ranking |
| exact top-1 | the exact true case at rank 1 |
| faithfulness | cited facts that exist |
| actions P / R | action precision and recall |
| lead time | minutes before the trip of the first exact correct call |
| false alarms /h | no-fault episodes only |
| tick p95 ms | slowest 5% of ticks, all ticks included |
| wall s | episode wall time |
| chip C | chip °C before → after |

## 3. NPU screening and model choice files

- `results/board/A/<model>/screen.summary.json`:
  - tensor type counts (`gguf`) and layers on HTP0 (`offload`);
  - `server_prefill_tok_s` and `server_decode_tok_s`, pooled over 10 calls;
  - `measured_verified_s`: the time of one 700-token/60-token diagnosis plus one 350-token/30-token verification;
  - `calls`: per-call server timings.
- `results/board/C/model_choice.txt`: the campaign's comparison table (3 dev episodes, single agent) and the rule's
  verdict ("no model passes"), before the human override that chose Llama 3.2 3B. Its `mock` column is a no-model
  reference, not an AI result.

## 4. The metrics

### Accuracy (cause identification). Scored only from fault onset onward, on ticks with a published ranking

| metric | where | definition |
|---|---|---|
| **group top-1** (headline) | `evaluation.T2_root_cause.group_top1`, CSV `group_top1` | share of scored ticks whose **published** top cause is the true case **or its look-alike** (cases the six sensors can't tell apart, `data/kb/case_groups.json`) |
| belief group top-1 | `T2.belief_group`, CSV `belief_group_top1` | the same for **belief**, the code-only ranking: the bar the model has to beat |
| exact top-1 | `T2.top1_strict`, CSV `exact_top1` | the exact true case at rank 1 (`null` for held-out causes) |
| `top3` / `top3_strict` | evaluator | the **exact** true case anywhere in the top 3 |
| true group in top 3 | CSV only | the **true group** anywhere in the published top 3 |
| MRR | CSV only | 1 / rank of the first true-group case in the top 3 (0 if absent) |
| helped / harmed vs belief | CSV only | ticks the published cause is right and belief's wrong, and the reverse |
| first right, min after onset | CSV only | minutes from fault onset to the first tick with the right group on top |
| right before the trip | CSV only | share of scored ticks before the trip with the right group on top (episodes with a trip: A01, A02, A03, D01) |
| lead time (exact) | `T6_lead_time.lead_time_min` | minutes between the first tick with the **exact** true case on top and the trip; **negative = after the trip** |
| rank-1 changes per 100 ticks | CSV only | how often the top cause flips (stability) |
| `sep_named` | evaluator | right group on top AND the exact true case in the top 3 with its distinguishing check shown |

### Confidence

| metric | definition |
|---|---|
| shown confidence (`confidence_shown`) | min(the cause's own confidence, sigmoid(its belief log-odds − its strongest rival's)). It measures how clearly belief separates the top cause from the next one; a tie shows 0.50 |
| conf when right / wrong, conf AUROC | CSV only. AUROC: the probability that a right tick shows higher confidence than a wrong one; 0.5 = useless, 1.0 = perfect |

### Other evaluator fields

| field | meaning |
|---|---|
| `T1_state.macro_f1` | plant state (NORMAL, DEVIATION, ALARM, TRIP_IMMINENT) against the truth; computed **by code**, identical across all multi-agent configurations |
| `T3_faithfulness.faithfulness` | share of cited fact IDs that exist |
| `T4_actions` | precision and recall of suggested actions against the expected ones; recall is 1.0 almost everywhere, precision 0.3–0.8 |
| `T5_false_positives` | no-fault episodes: ticks not NORMAL, per hour (target ≤ 2/h) |
| `T9_injection` | ticks reporting NORMAL while the plant wasn't, in episodes with an injected note. **Measures code-computed state, not the model**; identical across runs (see pitfalls) |
| `S1_tick_latency_ms_p50/p95` | percentiles over **all** ticks including quiet ones; the p50 is tiny because most ticks are quiet. Use the CSV's `median_nonquiet_tick_s` instead |
| `S4_llm_invocation_rate` | share of ticks with a model call |
| `S7_deadline_miss_rate` | share of ticks over budget (code path > 200 ms or whole tick > 30 s) |
| `parse_failure_rate` | first-reply format failures (0 with the grammar) |
| `S3_energy_mwh` | always `null`: **energy was not measured** |

## 5. The CSVs in `data/` (generated by `tools/make_tables.py` from the run files)

| file | content |
|---|---|
| `episode_metrics.csv` | one row per (configuration, episode): every metric above plus calls, tokens, median call, median non-quiet tick, slowest tick, ticks over 30 s, wall time, chip °C |
| `config_summary.csv` | means (sums for counts) per configuration on two episode sets: the first four fault episodes and all ten. `*_n` columns give how many episodes contributed to each value |
| `npu_model_screening.csv` | the four models on the NPU: size, tensor types, layers on HTP0, prefill and decode tok/s, verified diagnosis time |
| `lane_speeds_in_runs.csv` | pooled from every call in the benchmark runs, per lane and agent: calls, median prompt tokens, mean answer tokens, prefill and decode tok/s, share of time decoding, median call ms |

Configuration labels in the CSVs:
- `single`: A01 and N01 from 7 October, the rest from 5 October;
- `multi_model`: v2, merge `model`;
- `multi_tiebreak`;
- `multi_nudge`: both nudge folders;
- `multi_v3`.

## 6. Pitfalls (each of these would make a slide wrong)

1. **Reporting episodes only, and v3 was designed while looking at them.** The merge rule, the guard and the
   letter removal were chosen after studying these same episodes, so v3's lead is optimistic. The dev set (same
   faults, different random seeds) is meant for tuning and hasn't been run with v3. Say "on the episodes measured".
2. **One run per episode.** At temperature 0, 13–22% of model replies still differ between runs of identical
   prompts. Treat differences under about 0.05 as noise.
3. **Held-out cause.** C01 and C03's true cause (wet coal, RCA-06) is deliberately not in the library; only group
   credit (its look-alikes RCA-14 + RCA-18) is possible, so exact top-1 is `null`.
4. **Look-alike credit.** A01 and A02's exact case is almost never separated from its look-alike (exact top-1 near 0
   while group top-1 is 0.58–0.89); their "exact" lead times are negative for that reason.
5. **Tick times are laptop times.** Model call times and tok/s are board times.
6. **Chip temperature differs between runs.** In `multi_v3`, episodes after the B01 restart started at 55–61 °C (the
   restarted batch took its cooling target from a hot chip). A cool re-run of C01–C03 gave the same accuracy
   (within 0.04), and call times were unaffected.
7. **Faithfulness under `tiebreak`, `nudge` and `hybrid`** is somewhat lower on some episodes (0.76–0.99). Those
   rules republish belief's own causes with fact IDs belief accumulated on **earlier** ticks, which the evaluator
   counts as non-existent. It is a bookkeeping effect of the merge rules, not the model inventing facts.
8. **T9 "injection"** is the same number in every run because it measures code-computed state. In v3 the injected
   note was never selected into a diagnosis prompt, so injection resistance is **untested**.
9. **The single agent's B01–E01 are from 5 October,** with an older runner. The same model, flags and agent code, so
   comparable, but a different day.
10. **Energy:** `null` everywhere. Do not derive it from time or temperature.
11. **Mock folders** (`results/benchmarks_mock/`, any `mock` column) are plumbing checks, never AI results.
