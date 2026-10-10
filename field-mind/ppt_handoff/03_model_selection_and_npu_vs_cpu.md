# 03. Model selection on the NPU, and NPU vs CPU

Sources: `results/board/A/*/screen.summary.json` (screening), `results/board/C/model_choice.txt` (campaign verdict),
`reports/phase0b_board_setup.md`, and `data/lane_speeds_in_runs.csv` / `data/npu_model_screening.csv` (this pack).

---

## 1. The four candidates

All four were built to the same recipe (pure Q4_0, Q8_0 embedding; `02`) and all four ran **every layer on the NPU**.

| model | size on disk | layers on HTP0 |
|---|---|---|
| Llama 3.2 3B Instruct | 2.01 GB | 29 / 29 |
| Qwen3 1.7B (thinking turned off) | 1.46 GB | 29 / 29 |
| Gemma 3 1B (QAT) | 0.72 GB | 27 / 27 |
| Qwen2.5 0.5B Instruct | 0.35 GB | 25 / 25 |

## 2. NPU screening: speed of each model (measured on the board)

Two fixed request shapes, matching what the multi-agent design needs:
- a **diagnosis:** 700-token prompt, 60-token answer;
- a **verification:** 350-token prompt, 30-token answer.

Five calls of each after a warm-up, chip cooled before each model. Rates are the server's own timing counters.

| model | prefill (tok/s) | decode (tok/s) | one diagnosis + one verification, measured |
|---|---|---|---|
| Llama 3.2 3B | 986 | 21.0 | **5.34 s** |
| Qwen3 1.7B | 1,432 | 32.4 | 3.51 s |
| Gemma 3 1B | 1,757 | 44.2 | 2.63 s |
| Qwen2.5 0.5B | 2,960 | 52.8 | 2.06 s |

Reading it:
- **Prefill scales with model size** roughly as expected: the 0.5B reads prompts 3× faster than the 3B.
- **Decode is the slow phase on every model** (21–53 tok/s against about 1,000–3,000 tok/s for prefill), so
  **answer length dominates call time**. A 60-token answer takes about 2.9 s on the 3B, a 700-token prompt about
  0.7 s.
- **All four meet the design target** of a verified diagnosis in under 10 s.

## 3. The model-choice campaign: speed is not enough

An automatic, unattended campaign (rules committed before any run) ran the single agent on three development
episodes with each model, then applied the selection rule.

| hard limit | value |
|---|---|
| broken JSON on the first reply | ≤ 10% |
| broken JSON after one repair attempt | ≤ 2% |
| projected verified diagnosis | ≤ 10 s |
| all layers on HTP0 | required |
| only Q4_0 / Q8_0 matrices | required |

**Round 1 drop rule** (broken JSON first reply / after repair, limits 0.30 / 0.10):
- **Qwen3 1.7B dropped:** 0.368 / 0.053.
- **Gemma 3 1B dropped:** 0.456 / 0.333.

**Final comparison** (single agent, 3 dev episodes, answers capped at 256 tokens, no answer grammar):

| | Llama 3.2 3B | Qwen2.5 0.5B | code only (no model) |
|---|---|---|---|
| group top-1 (right cause or its look-alike at rank 1) | **0.507** | 0.418 (below the floor) | 0.501 |
| broken JSON, first reply / after repair | 0.061 / **0.042** | 0.389 / 0.290 | – |
| cited facts that exist (raw, before the gate) | 0.828 | 0.179 | – |
| mean diagnosis call | 11.4 s | 4.9 s | – |
| answers that hit the 256-token cap | 4.7% | 50.3% | – |
| ticks over the 30 s budget | 16.8% | 0.25% | – |

**Outcome:**
- **No model passed every limit;** the 3B missed only "broken JSON after repair" (4.2% against 2%).
- **The cause was answers cut off at the 256-token cap:** 10 of the 3B's 11 final failures.
- **The team chose Llama 3.2 3B by explicit override:** it was the only model that completed all rounds and failed
  only one limit.
- **The smaller models were fast but unreliable:** the 0.5B's ranking was worse than using no model at all.

This finding (small models on the NPU are fast, but the answer format and length decide reliability) is what led to
the multi-agent design's short, grammar-constrained answers. With the answer grammar added later, every model answer
in every run was well-formed (`06`).

## 4. NPU vs CPU

### 4.1 Llama 3.2 3B on the NPU, in real runs

| setting | calls | median prompt | mean answer | prefill tok/s | decode tok/s | median call | share of model time spent decoding |
|---|---|---|---|---|---|---|---|
| single agent, diagnostician (no grammar) | 657 | 1,931 | 159 | 666 | 13.6 | 13.8 s | 82% |
| single agent, verifier (no grammar) | 177 | 1,107 | 193 | 678 | 14.4 | 14.4 s | 89% |
| **multi-agent, diagnosticians (grammar on)** | **1,492** | **749** | **34** | **809** | **15.9** | **3.1 s** | **70%** |

The multi-agent design cut the median model call from **13.8 s to 3.1 s**, mainly by cutting the answer from about
160 to about 34 tokens (and the prompt from about 1,900 to about 750 tokens).

### 4.2 Gemma 3 1B on the CPU lane, in real runs

| agent | calls | median prompt | mean answer | prefill tok/s | decode tok/s | median call |
|---|---|---|---|---|---|---|
| verifier (grammar on) | 280 | 406 | 25 | 341 | 37.0 | 1.9 s |
| note reader (grammar on) | 174 | 404 | 17 | 357 | 27.1 | 1.8 s |

### 4.3 The same model on both: Gemma 3 1B

| where | prefill | decode | conditions |
|---|---|---|---|
| NPU (HTP0) | **1,757 tok/s** | 44.2 tok/s | screening, 700-token prompts, no grammar |
| CPU (6 threads) | 392 tok/s | **58.4 tok/s** | one call, 2,111-token prompt, no grammar (board check, 5 Oct) |
| CPU (6 threads) | 341–357 tok/s | 27–37 tok/s | real runs, ~400-token prompts, grammar on |

**Reading it:**
- **On prefill the NPU is about 4–5× faster** than the CPU for the same 1B model.
- **On decode the CPU was as fast or faster** for this small model in the one comparable call: 58 tok/s against
  44 tok/s, with different prompt sizes. Decode of a small model is memory-bandwidth-bound, so the NPU's advantage
  shrinks.
- **The answer grammar costs decode speed on the CPU:** one Gemma call measured 62 tok/s without the grammar and
  46 tok/s with it. On the NPU (Llama 3B) it made no difference in the same test (20.2 tok/s both).

**Caveat for slides:** these rows were not measured under identical conditions (prompt size, grammar, chip
temperature differ). Present them as "measured in our runs", not as a controlled NPU-vs-CPU benchmark. A controlled
same-prompt comparison of Llama 3B on both lanes was not done.

### 4.4 Why this matters for the design
- **Prefill-heavy work belongs on the NPU** (long prompts, big model): the diagnosticians.
- **Short-answer, small-model work can live on the CPU** without losing much (the verifier), freeing the NPU.
- **Both lanes run at the same time** in real-time mode (`05`), so the CPU adds capacity rather than competing.
