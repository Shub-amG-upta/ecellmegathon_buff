# 08. Key numbers for slides (all measured on the board)

Every number here comes from a run file, a summary file or a board log, and can be traced through the source
column. "First four" = fault episodes A01, B01, C01, D01, run by every configuration. "All ten" = A01–A03, B01–B03,
C01–C03, D01.

---

## A. The NPU platform

| number | value | source |
|---|---|---|
| board | QIDK, Snapdragon 8 Gen 3 (SM8650), Hexagon NPU (HTP, v75 libraries), 11.1 GiB memory | `02` |
| inference stack | llama.cpp b11371 (`99b95488c`) Hexagon backend, Hexagon SDK 6.6.0.0, NDK r29 | `reports/phase0b_board_setup.md` |
| Llama 3.2 3B layers on the NPU | **29 / 29**, final logits on HTP0; only the input embedding lookup on the CPU | same, scheduler debug log |
| Llama 3.2 3B HTP0 buffers | model 1,911.90 MiB, KV cache 448.00 MiB, compute 64.01 MiB | same |
| quantization | pure Q4_0 weights, Q8_0 embedding (196 Q4_0 + 1 Q8_0 matrices in the 3B) | `npu_model_screening.csv` |
| `-lv 4` logging cost | prefill −0.09%, decode +1.37% (within noise) | board setup report |
| memory fix | host prompt cache filled board memory and stalled after 36 calls; `--cache-ram 0` → 50/50 calls, server memory 512 → 535 MB | board setup report |

## B. Four models on the NPU (screening, 700-token prompt / 60-token answer and 350 / 30)

| model | size | prefill tok/s | decode tok/s | verified diagnosis |
|---|---|---|---|---|
| Llama 3.2 3B | 2.01 GB | 986 | 21.0 | 5.34 s |
| Qwen3 1.7B | 1.46 GB | 1,432 | 32.4 | 3.51 s |
| Gemma 3 1B | 0.72 GB | 1,757 | 44.2 | 2.63 s |
| Qwen2.5 0.5B | 0.35 GB | 2,960 | 52.8 | 2.06 s |

Source: `data/npu_model_screening.csv`. All four ran every layer on HTP0.

**Model-choice campaign** (single agent, 3 dev episodes): no model met every limit. Llama 3.2 3B was chosen by
recorded override. It had 4.2% broken answers after repair (limit 2%) and group top-1 0.507, against 0.418 for the
0.5B and 0.501 with no model. Source: `results/board/C/model_choice.txt`.

## C. NPU vs CPU lanes in real runs

Source: `data/lane_speeds_in_runs.csv`.

| lane / agent | calls | median prompt | mean answer | prefill tok/s | decode tok/s | median call |
|---|---|---|---|---|---|---|
| NPU, Llama 3B, single-agent diagnostician | 657 | 1,931 | 159 | 666 | 13.6 | 13.8 s |
| NPU, Llama 3B, multi-agent diagnosticians | 1,492 | 749 | 34 | 809 | 15.9 | **3.1 s** |
| CPU, Gemma 1B, multi-agent verifier | 280 | 406 | 25 | 341 | 37.0 | 1.9 s |

- In the single agent, **82–89% of model time is decoding** the answer.
- Gemma 1B: NPU prefill 1,757 tok/s against CPU 341–392 tok/s (about 4–5×). Decode was similar or faster on the CPU
  (58 against 44 tok/s in the one comparable call). Not a controlled comparison: see `03`.

## D. Speed: single agent against multi-agent (first four)

Source: `data/config_summary.csv`, `episode_metrics.csv`.

| | single agent | multi-agent v3 |
|---|---|---|
| total wall time, 4 episodes | 9,915 s | **821 s** (12×) |
| median model call | 14.8 s | 3.1 s |
| median non-quiet tick | 18.3 s | 1.3 s |
| ticks over the 30 s budget | **81 of 500** | **0** |
| model calls | 636 | 254 |
| prompt tokens processed | 1.09 M | 0.18 M |
| unusable model answers | 71 | 0 |

Per episode wall time, single → v3: A01 2,113 → 212 s; B01 2,837 → 173 s; C01 785 → 91 s; D01 4,181 → 346 s.
Chip at episode end: single about 65–70 °C, multi-agent about 57–65 °C (back to back, no pauses).

## E. Accuracy progression (first four)

Source: `data/config_summary.csv`.

| configuration | group top-1 | right group in top 3 | helped / harmed vs belief | right before the trip | first right after onset |
|---|---|---|---|---|---|
| belief alone (code only) | 0.726 | – | – | – | – |
| single agent | 0.672 | 0.91 | 77 / 81 | 0.72 | 11.0 min |
| multi v2, merge `model` | **0.378** | 0.87 | 57 / **294** | 0.45 | 50.3 min |
| multi v2, `tiebreak` | 0.688 | 0.80 | 11 / 28 | 0.51 | 10.1 min |
| multi v2, `nudge` | 0.750 | 0.86 | 19 / 12 | 0.63 | 15.0 min |
| **multi v3 (`hybrid`)** | **0.836** | **0.97** | **96 / 17** | **0.87** | **8.0 min** |

## F. v3 on all ten fault episodes

| | v3 | belief alone | nudge (v2 prompt) |
|---|---|---|---|
| group top-1 | **0.703** | 0.575 | 0.644 |
| exact top-1 (8 episodes) | 0.600 | – | 0.467 |
| right group in the top 3 | 0.908 | – | 0.792 |
| helped / harmed vs belief | 224 / 38 | – | 55 / 12 |
| right before the trip (4 episodes) | 0.76 | – | 0.48 |
| first right after onset | 9.6 min | – | 14.6 min |
| top-cause changes per 100 ticks | 8.2 | – | 4.7 |
| confidence AUROC | 0.60 | – | 0.76 |

Per episode, v3 against belief: A01 0.89/0.55, A02 0.58/0.25, A03 0.81/0.45, B01 1.00/0.55, B02 1.00/0.86,
B03 0.60/0.29, **C01 0.63/0.98, C02 0.32/0.53, C03 0.38/0.47**, D01 0.83/0.82.

Lead time (exact case first on top, before the trip):

| episode | v3 | single agent |
|---|---|---|
| A01 | 22.2 min | 17.2 min |
| D01 | 114.8 min | 108.3 min |

## G. Prompt-shortcut finding (model's own first pick, 500+ answers)

| | old prompt (group letters) | v3 prompt (none) |
|---|---|---|
| right first pick, all answers | 0.33 | **0.65** |
| tube leaks (B) | 0.04 | 0.70 |
| high-CV coal (D) | 0.02 | 0.70 |
| first pick is a "filler" case | 39% | 16% |

With letters, the model wrote the most common letter in the list in 89% of answers. Source:
`reports/multi_v3_results.md`.

## H. Answer grammar

- Unusable answers before → after the grammar: A01 26/84 → 0/57, B01 47/112 → 0/52; Gemma note readings 10/10 →
  0/10.
- 0 unusable answers in every multi-agent run since.

Source: `reports/multi_phase4b_grammar.md`.

---

## Caveats to keep with these numbers

1. **Reporting episodes only;** v3's design was chosen while looking at them, so its accuracy lead is optimistic
   (dev-set validation pending).
2. **One run per episode;** 13–22% of replies vary between identical runs.
3. **v3 fails the team's own pass rule** (no episode more than 0.05 below belief) because of the C episodes. The cause
   is understood: a look-alike tie that the rule hands to the model.
4. **Tick and code-path times are laptop times;** model call times are board times.
5. **Energy was not measured.**
6. **All plant data is simulated.**
