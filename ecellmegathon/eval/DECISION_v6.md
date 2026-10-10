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

## Result (BOARD, recorded after the runs)

Runs by Sarthak, code `b272764` (v6), QIDK SM8750P, ctx 2048, PROMPT=1, POSTFIX=on, fast path on, dev split,
`EVAL_HEAT_PAUSE=90:80` (0 pauses). Cooling not recorded. Baseline = v4 config (`ANSWER=full`), 2 runs, **identical
answers on all 68 LLM turns** (this also gives v4 its second run). Reports: `ANALYSIS_v6_run1.md`,
`ANALYSIS_v6-gemma4b_run1.md`.

| | baseline (v4) ×2 | v6 compact (1 run) |
|---|---|---|
| fields right /242 | 190 | 192 |
| tool right /157 | 153 | 151 |
| ended right /44 | 41 | **43** |
| turns with an extra value | 37 | 42 |
| output tokens per LLM call | 33.3 | 30.1 (−9.6%) |
| LLM call median | 1,643 ms | 1,485 ms |

| rule | result |
|---|---|
| 1. ≥ 15% fewer output tokens | **FAIL** (−9.6%) |
| 2. fields ≥ baseline − 2 (188) | PASS (192) |
| 3. tool ≥ baseline − 1 turn (152) | **FAIL** (151) |
| 4. ended right ≥ baseline − 1 (40) | PASS (43) |
| 5. extra turns ≤ baseline + 3 (40) | **FAIL** (42) |
| 6. LLM time per call not higher | PASS |

**Verdict: DROP compact as the default; `ANSWER=full` stays (v4 config).** The format did save the expected
~7 tokens per call where the answer was the same (20 turns: mean 7.0, 4–8), but without the "update_report"
framing the model wrote more fields (3.85 vs 3.18 per call), which ate most of the saving and added extras. The
two new tool errors are a filler-only turn ("um... uh...") and a cancel at the end of a sentence, both answered
as updates. `ANSWER=compact` stays available as a switch.

**Gemma 3 4B, v6 compact, 1 run** (GEMMA4B_RULE.md): fields 192/242, tool 149, ended 37/44, extras 38, LLM median
2,409 ms. Type **39/43 vs Llama's 31** (8 wins, 0 losses: slip vs fall, struck-by, chemical vs gas, fall from
height), persons 13 vs 10; location 34 vs 42, equipment 7 vs 10. Fails its rule 2 (tool ≥ 0.975): keep Llama.
Its prefill was 660 ms median with 494 new tokens per call: the system prompt was **not reused**, unlike Llama
(104 ms, 38 tokens). Likely cause, unverified: Gemma 3 uses sliding-window attention, and llama-server doesn't
reuse a cached prefix for such models unless started with `--swa-full`.
