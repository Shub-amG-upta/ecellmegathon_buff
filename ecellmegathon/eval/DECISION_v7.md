# v7 (type router): the keep/drop rule, written before any board run

Written 2026-10-11, after the code and its tests were committed and **before** any BOARD run of v7.

## What is being decided

**v7 = v4 configuration + a type router** (`app/router.py`, `TYPE_MODEL=gemma-3-4b-it-Q4_0-pure-embq8.gguf`):
Llama 3.2 3B answers every turn as in v4; on the first description (form has no type yet), when Llama's answer
gives a type, Gemma 3 4B gets the same prompt with an answer constrained to `{"t": <one of 15 types>}`, and its
type replaces Llama's. It never adds a type, never touches another field, and on failure Llama's answer stands.
Gemma runs as a second llama-server on the NPU (`board.board` lane `type`, port 8084, `--swa-full`).

Also in the v7 code, for both runs below: three fast-path rules (filler-only turn → nothing; "cancel this one" /
"…, cancel this" → cancel; "submit" before the form is complete → submit, which code refuses). On all 250 eval
turns they answer 138 turns with 0 wrong.

**PROJECTION** (v4's board answers with Gemma's board types, `DECISION_v6.md` runs): type 33 → 39/43 (7 fixed:
slip vs fall, struck-by ×3, fall from height, chemical vs gas, electrical; 1 broken: `oth-01` "burning smell",
gold `other`, Gemma chemical), fields 190 → 196/242. 43 router calls (at most one per report). Extra time per
report ESTIMATE ~0.46 s with prompt reuse (`--swa-full`), ~1.0 s without.

## Conditions

QIDK SM8750P, context 2048, PROMPT=1, POSTFIX=on, ANSWER=full, fast path on, dev split, `run_bench.py` with cool-down,
`EVAL_HEAT_PAUSE=90:80`, the same cooling for both. **Baseline = the v7 code without TYPE_MODEL** (so the only
difference is the router), 1 run (v4's replies were identical across launches in every pair so far). **v7 = the
same with TYPE_MODEL set, 2 runs.**

Stop and report, no accuracy numbers, if: Gemma's lane isn't fully on HTP0, the board runs out of memory or
reboots, or a server dies (that run is void).

## Rule. Keep the router only if all hold on both v7 runs

1. incident type right **≥ 37/43** (the projection is 39; this allows it to be 2 optimistic);
2. **every other field's count identical** to the baseline in the teacher-forced run (the router can only touch
   the type; anything else moving is a bug);
3. tool right **≥ the baseline's**;
4. free-running ended right **≥ the baseline's**;
5. median LLM time of **first turns** ≤ the baseline's **+ 1.0 s**, and mean LLM time per turn ≤ **+15%**;
6. both lanes 100% on HTP0 (startup logs), no crash, board free memory stays above 2 GB.

Report also: router calls / types changed / failures (eval `router`), Gemma's prefill ms per call (did `--swa-full`
restore prompt reuse?), NPU/CPU peaks.
