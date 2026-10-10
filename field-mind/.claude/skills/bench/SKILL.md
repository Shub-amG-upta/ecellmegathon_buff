---
name: bench
description: Run the FieldMind benchmark on all 30 episodes and compare against the saved baseline. Use when asked to run /bench or to benchmark a phase.
---

Run the FieldMind benchmark with these arguments: $ARGUMENTS
(for example `--arch multi --backend mock --mode lockstep`; if an argument is not supported yet, say so and stop).

1. Find the benchmark entry point in `bench/` (see README and CLAUDE.md). Do not invent a new one.
2. Note the git commit hash and whether the working tree is clean. If it is not clean, say so in the report.
3. If the backend uses the board, run `adb devices`, check both lanes answer
   (`curl -s http://localhost:8080/health` and `:8081/health`), and log chip temperature before the run.
4. Run all 30 episodes. Save results to `results/<arch>_<backend>_<mode>_<shorthash>.json`.
5. Report one table with these rows, comparing this run to `results/baselines/single_v1_summary.json` and to the previous
   phase's results file if one exists: Q1, Q2 top-1, Q2 top-3, group-level accuracy (if implemented), Q3, Q4 precision
   and recall, Q5, Q6, S4, S7, model calls per tick, stale-drop rate (multi only), mean and p95 diagnosis latency.
6. Under the table, list every metric that moved by more than 0.02 (or 10% for timings) and, for each, the change
   you believe caused it. If you cannot explain a move, write "unexplained" rather than guessing.
