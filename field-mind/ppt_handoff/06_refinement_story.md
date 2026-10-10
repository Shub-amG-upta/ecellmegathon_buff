# 06. The refinement story: what broke, how we found out, what we changed

A chronological arc for the deck. Every step is a measured problem, the evidence, the change, and the measured
effect. Dates are 2026.

---

## The arc in one table

Published group top-1 = share of scored ticks whose published top cause is the true case or its look-alike.
Mean over the four fault episodes A01, B01, C01, D01, which every configuration ran; from `data/config_summary.csv`.

| step | configuration | group top-1 | belief alone (same episodes) | median non-quiet tick | unusable answers |
|---|---|---|---|---|---|
| 1 | single agent (Llama 3B, NPU) | 0.672 | 0.726 | 18.3 s | 71 |
| 2 | multi-agent v2, merge `model` | **0.378** | 0.726 | 2.3 s | 0 |
| 3 | multi-agent v2, merge `tiebreak` | 0.688 | 0.726 | 1.5 s | 0 |
| 4 | multi-agent v2, merge `nudge` | 0.750 | 0.726 | 1.5 s | 0 |
| 5 | **multi-agent v3** (no letters, raw notes, `hybrid`) | **0.836** | 0.726 | 1.3 s | 0 |

On all ten fault episodes (only nudge and v3 ran all ten): nudge 0.644, **v3 0.703**, belief 0.575.

---

## Step 0. Getting models onto the NPU (3 October)

- **Problem:** a published "Q4_0" Llama file wasn't pure Q4_0 (Q6_K and Q4_1 tensors), and nothing proved the work
  ran on the NPU.
- **Change:** every model rebuilt from its original weights to pure Q4_0 with a Q8_0 embedding. Every launch's loader
  log is checked by code; the scheduler log showed all matrix multiplications and the final logits on HTP0.
- **Effect:** 29/29 layers on the NPU, a 1.91 GB HTP0 buffer, and proof saved with every benchmark episode.
  Details in `02`.

## Step 1. A memory leak that looked like a heat problem (3 October)

- **Problem:** overnight runs stalled after about 31–37 calls; the chip was hot, so heat was the first suspect.
- **Evidence:** a controlled soak test. With default flags, `llama-server`'s host prompt cache grew about 240 MiB per
  call until board memory hit zero and a call stalled for 300 s. With `--cache-ram 0`, all 50 calls ran at a steady
  speed and memory barely moved. Heat alone didn't cause it.
- **Change:** `--cache-ram 0` on every lane.

## Step 2. Choosing the model: fast isn't enough (4–5 October)

- **Problem:** the smaller models were 1.5–2.6× faster on the NPU, but were they usable?
- **Evidence:** an automatic campaign with rules fixed in advance. Qwen3 1.7B and Gemma 3 1B broke their JSON 37–46%
  of the time on the first reply; Qwen2.5 0.5B ranked causes worse than no model at all; Llama 3.2 3B failed only
  one limit (4.2% broken after repair, against 2%).
- **Cause of broken JSON:** answers cut off at the 256-token cap.
- **Change:** Llama 3.2 3B chosen, by an explicit, recorded override. Lesson carried forward: **make answers short
  and structured.**

## Step 3. The single-agent baseline (5–7 October)

- **Measured:** about 13–17 s per model call, ticks over budget (81 of 500 on four episodes), 71 unusable answers,
  82–89% of model time spent decoding long answers, and on average no better than code alone (0.672 against 0.726).
- **Lesson:** the model must not block the tick, answers must be short, and the format must be enforced.

## Step 4. Multi-agent: small jobs on two lanes (3–5 October, built on the mock first)

- **Changes:**
  - compact prompts (~750 tokens);
  - answers as line numbers capped at 60 tokens;
  - water and heat diagnosticians;
  - ask a side only when its evidence changes;
  - a scheduler with one queue per lane;
  - real-time mode in which the tick never waits;
  - Gemma 1B on the CPU for the verifier and note reader.
- **Effect** (board): median model call 3.1 s against 13.8 s; no tick over budget; about 10× less wall time per
  episode.

## Step 5. Answer grammar: zero broken answers (5 October)

- **Problem:** the first multi-agent board runs had 26–42% of Llama's diagnoses and 68–100% of Gemma's answers
  unusable: extra brackets, ```` ```json ```` fences, answers cut off at the cap.
- **Change:** each request carries a **GBNF grammar** built from exactly the lines that prompt showed, so llama-server
  can only produce a valid answer. The grammar's longest possible answer fits the token cap.
- **Effect:** unusable answers on A01 26/84 → 0/57, B01 47/112 → 0/52, notes 10/10 → 0/10, and **0 in every later
  run**.
- **Cost:** none measurable on the NPU (Llama decode 20.2 tok/s with and without). On the CPU it slowed Gemma's
  decode (62 → 46 tok/s, one call).

## Step 6. The accuracy collapse (7 October)

- **Problem:** with every answer well-formed, multi-agent accuracy **fell**: B01 0.09, D01 0.03, against belief's
  0.55 and 0.82.
- **Evidence:** the model's ranking replaced belief's.
  - Example, D01 tick 121: belief had the true cause at 0.98. The model put a "light-up flame" fault, flat on every
    sensor, first; it was published at confidence 0.30 above the true cause at 0.98.
  - Over four episodes the model made 294 right ticks wrong and fixed 57.
- **Change** (teammate): merge rules that keep belief's order as the base. `tiebreak` (model reorders near-ties
  only) gave 0.688; `nudge` (capped boosts) gave 0.750.

## Step 7. Finding a shortcut inside the model's answers (7 October)

- **Question:** why was the model fine on some fault types (A, C) and near zero on others (B, D)?
- **Evidence** (515 answers):
  - each case line carried a look-alike group letter, and the answer had to start with one;
  - the model wrote the **most common letter in the list** in 89% of answers and picked a case with it: right 92% of
    the time when that letter was the truth's, 26% otherwise;
  - faults whose true cause has a look-alike partner (A, C) got the shortcut's help; single-case faults (B, D) never
    could;
  - on B and D the model was **worse than a random pick** (0.02–0.08 against 0.21–0.32).
- **Change:** removed the group letters from the prompt and the answer.
- **Effect** (v3 board run), the model's own first pick:

  | family | before | after |
  |---|---|---|
  | all | 0.33 | 0.65 |
  | B (tube leaks) | 0.04 | 0.70 |
  | D (high-CV coal) | 0.02 | 0.70 |
  | A (feed faults) | 0.69 | 0.66 |
  | C (fuel faults) | 0.60 | 0.49 |

  Filler cases picked first: 39% → 16%. The pattern matched the prediction made before the run.

## Step 8. Letting the model lead where belief is unsure: `hybrid` (7–8 October)

- **Evidence** (ten episodes, by belief's own state; the model's own pick against belief):

  | belief's state | belief right | model right |
  |---|---|---|
  | weak (best cause has log-odds < 0) | 0.18 | 0.88 |
  | near-tie | 0.32 | 0.41 |
  | clear leader | 0.79 | 0.16 |

- **Change:** `hybrid`. The model leads when belief is weak or tied, otherwise capped nudges; a guard stops the model
  from leading with filler cases. Without the guard the idea failed (fillers again).

## Step 9. Raw notes instead of a model note reader (8 October)

- **Problem:** under the grammar, the Gemma note reader had to code every note with a listed word, so irrelevant
  notes became false plant facts ("fire extinguisher refill due" → `FEED_VALVE DOWN`).
- **Change:** no reader; the diagnostician reads the selected notes' own text inside a data fence.
- **Effect:** prompts got *smaller* overall (median 731 against 748 tokens, max 873 against 914), since the letters
  went too; call time unchanged (3.05 s against 3.01 s).

## Step 10. v3 on the board (8 October)

- **Results** (ten fault episodes):

  | | v3 | belief alone |
  |---|---|---|
  | group top-1 | **0.703** | 0.575 |
  | right group in the top 3 | 0.908 | 0.722 |
  | ticks helped / harmed vs belief | 224 / 38 | – |
  | right before the trip | 0.76 | 0.41 |
  | first right cause after fault onset | 9.6 min | 14.9 min |

- **Remaining problem:** all three fuel-fault episodes (C) lose to belief. Their true answer is a look-alike pair
  that belief always scores equally, so `hybrid` treats it as a tie and the model leads with another cause.
  - A re-run with a cool chip reproduced it (the scores moved by at most 0.04), so it isn't heat or chance.
  - A group-aware tie test fixes C in replay but gives back one water-side gain (A03). This is the open design
    question.

## What's next (planned, not done)

- **Decide the tie rule and validate v3 on the dev set,** since the design choices were made while looking at the
  reporting episodes.
- **Use the idle NPU time:** model work is under 2 s of each 30 s tick. Spend it on short, targeted questions (yes/no
  checks of each candidate cause, head-to-head comparisons, several samples) when belief is unsure.
- **Make shown confidence group-aware; add a stability rule** so the top cause changes less often.
- **Measure energy** (never measured so far), and **move the agent code onto the board** (it runs on the laptop
  today).
- **A purpose-built decision model on a third lane** is being prototyped by the team; it hasn't run on the board yet.
