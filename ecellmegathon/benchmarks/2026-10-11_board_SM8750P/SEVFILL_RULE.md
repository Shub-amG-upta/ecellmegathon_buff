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

## Result (BOARD, SM8750P, Llama 3.2 3B, ctx 2048, fast path on, dev split), measured after the rule above

Code: branch `severity-fallback`, commit `d785098` (runs recorded at `9c4a8c8`, which only adds this file).
Three runs with `run_bench.py --tag sevfill` (cool-down to <= 50 °C and a fresh server before each).

| run | s (turn) | tool | other fields vs v2 | turns with extras | turns changed vs v2 (non-s) | wrong severities | ended right | field (dialog) |
|---|---|---|---|---|---|---|---|---|
| v2 #1 = #2 | 36/42 | 0.975 | - | 51 | - | - | 29/44 | 63.7% |
| sevfill #1 | **42/42** | 0.975 | identical | 51 | 0 | 0 | **34/44** | 66.7% |
| sevfill #2 | **42/42** | 0.975 | identical | 51 | 0 | 0 | 25/44 * | 48.7% * |
| sevfill #3 | **42/42** | 0.975 | identical | 51 | 0 | 0 | **34/44** | 66.7% |

\* Run 2: **the board rebooted** during the free-running phase (~01:43:35; uptime 167.7 s at 01:46:24,
`sys.boot.reason=reboot`; NPU peak 105.8 °C). llama-server's log stops mid-request with no error; every later
call was refused. Its teacher-forced half had finished and is identical to runs 1 and 3. Recorded in its
`.meta.json` (`infrastructure_failure`). Run 3 was added to replace it.

The fixed turns are exactly the six targets (el-01, sl-01, fk-01, ch-01, cv-01, fi-04, all turn 0). In
free-running, 5 more dialogs end right (ch-01, cv-01, el-01, fk-01, sl-01), none end worse; the one remaining
free-running severity miss (lw-01) is a blank in a dialog that also misses type, location, time and
description, as in v2. ld-01 and fi-04 get their severity but still miss the time, so they don't end.

| rule | run 1 | run 2 | run 3 |
|---|---|---|---|
| 1. teacher-forced severity >= 40/42 | PASS (42/42) | PASS (42/42) | PASS (42/42) |
| 2. zero new wrong severities | PASS (0) | PASS (0) | PASS (0) |
| 3. everything else equal to v2 | PASS | PASS (teacher-forced) | PASS |
| 4. ended right >= 29/44 | PASS (34/44) | FAIL (25/44, board reboot) | PASS (34/44) |

**Verdict: keep**, on the two valid runs (1 and 3), which agree exactly. Run 2's only failure is the board
rebooting under load, not the change; the decision is Shubham's.
