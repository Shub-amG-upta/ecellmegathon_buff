# v6 (compact answers): the keep/drop rule, written before any board run

Written 2026-10-11, after the code and its tests were committed and **before** any BOARD run of v6. Results must
not change this rule; a human override is recorded here with its date and reason.

## What is being decided

**v6 = v5 + compact answers (`ANSWER=compact`, default on the `v6` branch).** The model writes only the fields
object (`{"t":"fire","l":"lab"}`) for an update, or `"S"` / `"C"` / `"N"` for submit / cancel / nothing. Code turns
it back into the same tool call (`app/form.py` `to_tool_call`), so the gate, fixes, dialog, UI and eval are
unchanged. Reason: in v3's 68 LLM calls (BOARD), 51% of the answer's characters were the forced wrapper
`{"tool":"update_report","args":{…}}`, and every forced token still costs a decode step (~48 ms).
**PROJECTION** from v3's real answers: ~26% fewer answer characters, ~33 → ~25 output tokens per LLM call.

## Conditions

Same as `eval/DECISION_v4_v5.md`: QIDK SM8750P, Llama 3.2 3B pure Q4_0, context 2048, fast path on, dev split,
`benchmarks/run_bench.py` with cool-down, **2 runs**, same cooling for every compared run,
`EVAL_HEAT_PAUSE=90:80`. **Baseline = the configuration kept by DECISION_v4_v5.md** (v5 if kept, else v4, else v3),
run in the same session if possible, with `ANSWER=full`. v6 = the same with `ANSWER=compact`.

## Rule. Keep compact answers only if all hold on both runs

1. mean **output tokens per LLM call ≥ 15% lower** than the baseline (`decode_tokens` in the eval output);
2. fields right (teacher-forced) **≥ baseline − 2**, and **no field** loses more than 2;
3. tool accuracy **≥ baseline − 1 turn** (the risk: deciding update / submit / nothing without the word "tool");
4. free-running ended right **≥ baseline − 1**;
5. turns with an extra value **≤ baseline + 3**;
6. mean LLM time per call not higher than the baseline's.

If it fails, `ANSWER=full` stays the default and the numbers are recorded here.
