# Severity fallback (`sevfill`): the keep/drop rule, written before the board runs

Written 2026-10-11, after the code and its tests were committed on `severity-fallback` and **before** any
BOARD run of it. Copied from the task agreed with Shubham.

Baseline: v2 (runs 1 and 2 identical): teacher-forced `s` 36/42, tool 0.975, field 0.657, free-running ended
right 29/44. (v2 teacher-forced turns with an extra value: 51.)

Keep the change only if **all** hold, on **both** runs (`run_bench.py --tag sevfill --runs 2`, same board,
model, ctx 2048, fast path on, cool-down to <= 50 °C before each run, dev split only):

1. teacher-forced severity >= 40/42;
2. zero new wrong severities (a filled value that differs from gold = fail);
3. every other field's count, tool accuracy and the extra-turn count equal to v2 (v2's two runs were
   identical, so any change elsewhere is a bug: investigate, don't explain it away);
4. free-running "ended right" not below 29/44.

Note on rule 3: the task text quotes v2's extra-turn count as 71, which is v1's number; v2 measured 51 in
both runs (`COMPARISON.md`). The comparison uses v2's measured 51.
