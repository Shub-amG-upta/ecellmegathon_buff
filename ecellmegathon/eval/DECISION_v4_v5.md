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
