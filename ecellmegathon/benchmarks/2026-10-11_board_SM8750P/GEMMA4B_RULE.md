# Gemma 3 4B (`gemma4b`): the keep/drop rule, written before any board run

Written 2026-10-11 ~02:25, while the weights were still downloading: no Gemma 3 4B output of any kind had been
seen. DRAFT until a human confirms the thresholds; once confirmed, they don't change after a result is seen.

**Question:** should Gemma 3 4B Instruct replace Llama 3.2 3B as the extraction model?

**Candidate:** `gemma-3-4b-it-Q4_0-pure-embq8.gguf`, built with `board/build_candidate_gguf.sh` (every matrix Q4_0,
token embedding and output Q8_0) from `unsloth/gemma-3-4b-it` @ `bf46152c47f5` (BF16; `google/gemma-3-4b-it` is
gated), with `--override-kv tokenizer.ggml.eos_token_id=int:1` as for FieldMind's Gemma 3 1B. Text model only.

**Baseline:** `sevfill` on the same board, model Llama 3.2 3B, same code (`severity-fallback`), ctx 2048, fast path on.
Runs 1 and 3 (run 2's free-running phase is void: the board rebooted at 105.8 °C):
teacher-forced tool 0.975, field 0.682, turns with an extra value 51; free-running field 0.667, ended right 34/44;
LLM call median 1.55-1.64 s; model replies identical in all three runs.

**Same conditions as the baseline:** same code commit as sevfill plus only the allow-list row, same dev split,
`run_bench.py --tag gemma4b --model <file> --runs 2`, ctx 2048, cool-down to <= 50 °C before each run.
**A fan on the board** (the 3B already rebooted it once).

## Stop before measuring (any one = stop and report, no accuracy numbers)
1. Not every layer on HTP0 (`offloaded N/N`, nonzero HTP0 model buffer) in the startup log.
2. The tokenizer check fails (llama-server `/tokenize` vs the Hugging Face tokenizer on every dev prompt), or a
   call doesn't stop by itself.
3. The board reboots or the server dies during a run: that run is void. Don't rerun without more cooling.

## Keep Gemma only if ALL hold on BOTH runs
1. teacher-forced field accuracy >= **0.712** (the 3B's 0.682 + 0.03: switching models has to buy something real);
2. teacher-forced tool accuracy >= **0.975**;
3. turns with an extra value <= **51**;
4. free-running ended right >= **34/44**;
5. zero errors, zero unparseable replies;
6. LLM call median <= **3.0 s** (BOARD), so a whole turn still fits the 6 s target with STT and TTS.

If 1-5 pass and only 6 fails: report it as "more accurate but too slow", don't switch. Otherwise: keep Llama 3.2 3B.
Dev split only; the test split stays locked.
