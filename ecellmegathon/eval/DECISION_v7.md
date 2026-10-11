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

## Result (BOARD, recorded after the runs; 1 run each, the rule asks for 2 v7 runs)

Runs by Sarthak, code `f7dcce3` (v7), QIDK SM8750P, ctx 2048, dev split, `EVAL_HEAT_PAUSE=90:80` (0 pauses).
Files: `benchmarks/2026-10-11_board_SM8750P/eval_BOARD_dev_v7-baseline_run1_*` and `…_v7_run1_*`.

| | baseline (no router) | v7 (Gemma decides the type) |
|---|---|---|
| incident type right /43 | 33 | **37** |
| a · d · e · l · p · s · w (teacher-forced) | 12 · 39 · 8 · 41 · 8 · 42 · 6 | identical |
| fields right /242 | 189 | 193 |
| tool right | 0.975 | 0.975 |
| ended right /44 | 41 | 42 |
| first-turn LLM time, median | 1,988 ms | **3,344 ms (+1,356)** |
| LLM time per turn, mean | 735 ms | **1,091 ms (+49%)** |
| NPU / CPU peak | 77.9 / 100.3 °C | 79.1 / 101.5 °C |

| rule | result |
|---|---|
| 1. type ≥ 37 | **pass** (37, exactly the bound; projection was 39) |
| 2. other fields identical | **pass** |
| 3. tool ≥ baseline | **pass** |
| 4. ended right ≥ baseline | **pass** (42 vs 41) |
| 5. first turns ≤ +1.0 s, mean ≤ +15% | **FAIL** (+1.36 s, +49%) |
| 6. both lanes on HTP0, no crash | pass (Llama 29/29, 1,911.9 MiB; Gemma 35/35, 2,402.8 MiB; no crash). Free memory not recorded |

**v7 router: DROP as built (rule 5).** One more run cannot rescue it: the margins are large and timings repeat
on this board. `TYPE_MODEL` stays off (it already is in v8).

What the router did (43 calls teacher-forced, 86 with free-running; 2 failures, both `lf-01`, where Llama's
`lift` stood): **fixed 6** (na-01 fall → slip; fk-01, fk-03, wit-01 → struck_by; lw-01 water_leak → fall_height;
wit-02 gas → chemical), **broke 2** (ld-01 fall_height → struck_by; oth-01, an ambiguous "burning smell",
→ chemical). Still wrong with the router: sl-03, ld-01, sec-01, sec-03, oth-01, wit-03.

Why it is slow: the Gemma call costs **1,354 ms** mean (ESTIMATE was ~0.46 s). Prompt reuse works (`--swa-full`:
median 31 new prompt tokens, 129 ms prefill; only the first call read the whole 689-token prompt), so the cost is
**decode: 13.7 tokens at ~85 ms each** to write `{"t": "struck_by"}` through the JSON-schema grammar (it allows
spaces). Llama's own time did not change (first-turn Llama part, median 2,000 ms vs 1,988).

**Next attempt (its own version, its own rule before running):** the type call writes only what decides the
type: `{"t":"` pre-filled (already supported by `LlamaServerBackend.generate(prefix=…)`), a grammar of the 15
names only, stop as soon as the decoded prefix names one type. ESTIMATE ~1–3 decode tokens + ~130 ms prefill ≈
0.25–0.4 s per call, which would fit rule 5 (budget: ≤ 0.40 s per router call for +15% mean).
