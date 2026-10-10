# STT model choice: the decision rule (written before any result)

Written 2026-10-10, before any WER sweep was run on human recordings. Results must not change this rule;
if a human changes it after seeing results, record the change here as an override, with its date and reason.

**Candidates:** whisper.cpp `tiny.en`, `base.en`, `small.en`, each with the `data/vocab.txt` prompt off and on.

**Data:** `eval/speech_subset.jsonl` (36 dev lines) recorded by the 3 speakers, quiet pass
(`eval/wer.py --subset`). The noisy pass is reported but is not part of the rule. No test-split line is used.

**Metrics** (`eval/wer.py`): overall WER, and the key-word error rate (numbers and times, names, meaning-flip
words), pooled over the 3 speakers.

## Rule

1. **Accuracy gate (LAPTOP numbers are fine for this; accuracy carries over to the board):**
   key-word error **≤ 10%** and overall WER **≤ 15%**.
2. **Speed gate (BOARD numbers only):** median STT time **≤ 3.0 s per 8 s clip** on the QIDK CPU, while the LLM
   is loaded on the NPU.
3. **Pick the smallest model** (tiny < base < small) that passes both gates.
4. **Vocab prompt:** on, if it lowers key-word error by **≥ 2 points** without raising WER by more than
   **1 point** for the picked model; otherwise off.
5. **If no model passes:** pick the one with the lowest key-word error among those with BOARD time ≤ 3.0 s per
   8 s clip, and report that it failed the rule (and which gate).
6. Until BOARD timings exist, a LAPTOP choice is **provisional** and is labelled so.

The thresholds (10%, 15%, 3.0 s, 2 points, 1 point) are design choices made before the data: the LLM call
already takes about 2–3 s per turn (ESTIMATE from FieldMind's decode rate), so STT should not add more than
that, and a wrong digit, name or "not" usually means a wrong field.
