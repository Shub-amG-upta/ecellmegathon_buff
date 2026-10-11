# Speed switches PREFILL and SPEC: the keep/drop rules, written before any board run

Written 2026-10-11, after the code and its tests were committed and **before** any BOARD run of either switch.

## What is being decided

- **`PREFILL=on`, answer pre-fill.** The fixed start of an update answer, `{"tool":"update_report","args":{`, is written
  into the prompt (llama-server `/apply-template` + `/completion`) and read in one batch; the model writes only the
  fields, under a grammar for the rest. Skipped for turns that mention submit/cancel/discard; an empty answer is
  "nothing for the report". The model still sees the full answer format (unlike v6's compact answers). On all 250
  eval turns the scorer's self-check stays 100% with it on. **PROJECTION** (v4's board calls, and v6's measured
  7.0 tokens saved per call by dropping this wording): 65 of 68 calls pre-filled, output tokens 33.3 → 26.6 per
  call, mean LLM call 1,728 → ~1,412 ms (−18%).
- **`SPEC=1`, copy-ahead (prompt-lookup) speculative decoding** (`--spec-type ngram-simple`, lookup 3, draft 8;
  `SPEC_N`/`SPEC_M` to tune). llama-server drafts the next tokens by matching the text against the prompt and checks
  them in one batch. Measured property (v4, BOARD): 36% of answer characters are exact copies of the user's words and
  another 51% is fixed JSON wording that also appears in the prompt's examples. No projection is possible offline.
  `start_lane` refuses if the board's llama-server lacks the flags.

## Conditions

QIDK SM8750P, Llama 3.2 3B, context 2048, PROMPT=1, POSTFIX=on, ANSWER=full, fast path on, dev split, `run_bench.py`
with cool-down, `EVAL_HEAT_PAUSE=90:80`, the same cooling for every run, all in one session. **Baseline = the v7
code with none of PREFILL / SPEC / TYPE_MODEL**, 1 run (replies have been identical across launches every time).

## Rule PREFILL (PREFILL=on vs baseline). Keep only if all hold

1. output tokens per LLM call **≥ 12% lower** (the projection is 20%);
2. fields right **≥ baseline − 1**, no field loses more than 1;
3. tool right **≥ baseline**; 4. ended right **≥ baseline − 1**; 5. turns with an extra value **≤ baseline + 2**;
6. mean LLM call time **lower** than the baseline's.

## Rule SPEC (SPEC=1 vs baseline). Keep only if all hold

1. mean LLM call time **≥ 10% lower**;
2. model answers **identical** to the baseline on at least 66 of the 68 LLM turns (speculative decoding verifies
   every token, so answers should not change; a few may differ from numeric noise);
3. accuracy counts (fields, tool, ended right, extras) within ± 1 of the baseline;
4. the server survives the run (no crash lines, no reboot).

## Then together

If both are kept, one run with `PREFILL=on SPEC=1` to confirm they stack (no rule; report it). The type router's
rule (`DECISION_v7.md`) is decided separately, with the speed switches it keeps.

## Run 1 (BOARD, Sarthak, code 5edb25e): PREFILL=on SPEC=1, INVALID

Every LLM answer was `}}` → no_op (64/64, 2 decode tokens): tool 0.618, fields 19.4%, ended right 2/44. Not a
measurement of either switch. **Cause (found 2026-10-11): the pre-fill ended inside a token.** Llama 3 writes
`"args":{"t"` as `args | ":{" | t` (read from the model's GGUF vocab); the prefix ended `args | ": | {`, and after a
lone `{` the model closed an empty object. Fixed in `f514c6d`: the prefix ends after `args` (the model writes
`":{"`), the Gemma type prefix after `{"t`; a test keeps prefixes ending on a letter. SPEC=1 alone answered
normally in Sarthak's one-call check. **Re-run all PREFILL / SPEC runs on the fixed code.**

Also merged before those re-runs (`fix-tool`, Sarthak): teacher-forced tool = the action the app takes, and
`postfix.drop_parts`. Projected on saved answers: tool 153 → 155/157, extra 37 → 36; so compare new runs only with
a baseline run on the same code.
