# v4 and v5: the keep/drop rules, written before any board run

Written 2026-10-11, after the code and its tests were committed and **before** any BOARD run of v4 or v5.
Results must not change these rules; if a human changes one after seeing results, record it here as an
override with the date and the reason. Same procedure as `benchmarks/2026-10-11_board_SM8750P/SEVFILL_RULE.md`.

## What is being decided

| version | change | switch |
|---|---|---|
| **v4** | v3 + post-model fixes (`app/postfix.py`: drop repeats, wrong-field veto, witness rule, full location, time fallback) | `POSTFIX=on` (default on) |
| **v5** | v4 + prompt v2 (field definitions with "never" rules, type rules, a dense example) | `PROMPT=2` (default on the `v5` branch) |
| cooler CPU | llama-server `-t 2 -tb 2 --poll 0` on the NPU lane | `COOL_CPU=1` |
| heat pause | pause the next LLM call at NPU ≥ 90 °C until ≤ 80 °C | `EVAL_HEAT_PAUSE=90:80` |
| paced run | 4 s before each LLM call, like a real conversation | `EVAL_PACE=4` |

## Conditions for every run

Board QIDK SM8750P, Llama 3.2 3B pure Q4_0, context 2048, fast path on, **dev split only**,
`benchmarks/run_bench.py` (cool-down to ≤ 50 °C and a fresh server before each run), **2 runs per
configuration** (if they disagree, run a third). **Every run in a comparison uses the same cooling** (fan on
or off, same position): write it in the run notes. `EVAL_HEAT_PAUSE=90:80` on every run from now on (a pause
changes wall time, not answers; the eval records each pause). A run the board doesn't survive (reboot, crash)
is an infrastructure failure: record it and replace it, as with sevfill #2.

**Baseline v3** (sevfill #1 = #3, BOARD): teacher-forced fields right **165/242**, per field t 33/43 · s 42/42
· l 35/44 · d 29/42 · a 12/27 · e 6/20 · p 7/18 · w 1/6; tool 0.975; turns with an extra value 51; free-running
ended right 34/44.

## Rule v4 (POSTFIX=on vs v3). Keep only if all hold on both runs

1. fields right **≥ 180/242** (the offline projection was 190; this leaves room for it to be optimistic);
2. **no field** loses more than 1 vs v3;
3. tool accuracy unchanged (± 1 turn);
4. ended right **≥ 34/44**;
5. turns with an extra value **≤ 51**;
6. mean LLM time per turn not more than 5% above v3 (the fixes are code; this checks nothing else moved).

## Rule v5 (PROMPT=2 vs v4). Keep only if all hold on both runs

1. fields right **≥ v4 + 5**;
2. incident type right **≥ v4**;
3. the four list fields together (p + e + a + w) right **≥ v4**;
4. **no field** loses more than 2 vs v4;
5. turns with an extra value **≤ v4**;
6. ended right **≥ v4**;
7. median LLM time of **first turns** ≤ **1.25 ×** v4's (a longer prompt and fuller answers cost time).

If v4 fails its rule, v5 is compared with v3 instead, on the same points.

## Rule cooler CPU (COOL_CPU=1 vs off, on the kept configuration)

Make it the default only if: CPU peak **≥ 5 °C lower** in both runs, decode speed **≥ 95%** of the run without
it, and accuracy identical (every field count). Otherwise leave it off and record the numbers.

## Not decisions, but reported

- One **paced** run (`EVAL_PACE=4`) of the final configuration: NPU/CPU temperature, Android thermal status,
  and latency under a realistic load, next to the back-to-back runs.
- Board reboots and NPU crashes per configuration.

## At the end, once

The final configuration and v3 on the **locked test split** (21 dialogs), one run each, reported as they come.
No change after seeing them. This checks that the v4 rules, written after reading dev-split failures, were
not fitted to the dev split.

## Result (BOARD, recorded after the runs; PROVISIONAL: 1 run each, cooling not recorded)

Runs by Sarthak, branch `v5` at `267e52e`, QIDK SM8750P, ctx 2048, fast path on, dev split,
`EVAL_HEAT_PAUSE=90:80` (0 pauses; NPU peak 81.8 °C, CPU ~100–101 °C). Files:
`benchmarks/2026-10-11_board_SM8750P/eval_BOARD_dev_v4_run1_*` and `…_v5_run1_*`; reports `ANALYSIS_v4_run1.md`,
`ANALYSIS_v5_run1.md`. v3 = sevfill #1.

| | v3 | **v4** | v5 |
|---|---|---|---|
| fields right /242 | 165 | **190** | 180 |
| t · l · d · p · e · a · w | 33 · 35 · 29 · 7 · 6 · 12 · 1 | 33 · 41 · 39 · 9 · 8 · 12 · 6 | 31 · 38 · 40 · 9 · 4 · 10 · 6 |
| tool right /157 | 153 | 153 | 153 |
| ended right /44 | 34 | **41** | 39 |
| turns with an extra value | 51 | 37 | 37 |
| LLM ms per turn (avg) | 717 | 743 | 786 |

**v4: KEEP. Passes all 6 rules.** 190/242 equals the offline projection exactly; the model's replies were
identical to v3's, so the whole gain is the post-model fixes.

**v5 (prompt v2): DROP. Fails rules 1, 2, 3, 4 and 6.** It changed 58 of 68 LLM answers: 17 fields fixed (type 6:
theft → security ×2, slip vs fall, struck-by ×2, electrical), 27 broken. Causes found in the answers:
(1) **copying from an example**: `ps-02` answered `"l":"pump room"`, which exists only in Example 5 of the prompt;
(2) the type line that put `equipment_failure` and `lift` together: **lift → equipment_failure 3 times**;
(3) the stricter equipment definition: equipment 8 → 4.

**Next prompt attempt (if any):** prompt v1 plus only the type rules that fixed things (theft = security; slip
vs fall from height; struck-by; electrical), `lift` on its own line, and no new examples (no copyable content).
Measure as its own version.

A second run of v4 is still owed by this file's rule (2 runs). Replies were identical across launches in every
earlier pair on this board, so one run is very likely representative; it stays marked provisional until then.
