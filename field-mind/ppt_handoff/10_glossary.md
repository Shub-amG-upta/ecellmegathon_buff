# 10. Glossary

| term | meaning |
|---|---|
| **QIDK** | Qualcomm Innovators Development Kit; here with a Snapdragon 8 Gen 3 (SM8650) |
| **Hexagon NPU / HTP** | Qualcomm's neural processing unit (Hexagon Tensor Processor); llama.cpp shows it as device `HTP0`; v75 is the library version |
| **llama.cpp Hexagon backend** | llama.cpp's code path that runs model operations on the Hexagon NPU (`ggml-hexagon`) |
| **llama-server** | llama.cpp's HTTP server; one persistent process per lane |
| **lane** | one `llama-server` bound to one compute unit (NPU lane on port 8080, CPU lane on 8081); handles one request at a time |
| **GGUF** | llama.cpp's model file format |
| **Q4_0 / Q8_0** | 4-bit and 8-bit block quantization types; "pure Q4_0" means every weight matrix is Q4_0 (here with a Q8_0 embedding) |
| **K-quants (Q4_K, Q6_K …)** | other llama.cpp quantization types; avoided here for uniformity |
| **prefill** | processing the prompt (fast, parallel); measured in tokens/s |
| **decode** | generating the answer token by token (slow, sequential); measured in tokens/s |
| **GBNF / answer grammar** | a formal grammar sent with the request; the server can only generate text that fits it |
| **adb** | Android Debug Bridge; USB link from the laptop to the board (port forwarding, shell, file push) |
| **tick** | one 30-second step: the system publishes an assessment every tick |
| **hard path** | the code-only part of a tick (checks, belief, gate), with a 200 ms budget; never waits for a model |
| **episode** | a simulated stretch of plant operation (45–200 min) with one fault or none |
| **reporting set / dev set** | the same fault scenarios simulated with different random seeds; dev is for tuning, reporting for results. Every board benchmark here used the reporting set |
| **fact** | a short code-written statement about the sensors ("drum level falling 2.7 %/min"), with an ID like `F1` (tick-stamped `t84.F1` in the multi-agent system) |
| **case library** | 13 failure reports from real boiler incidents, each with an expected sensor signature (RCA-01 … RCA-18) |
| **look-alike group** | cases the six sensors can't tell apart (e.g. RCA-01 + RCA-16); either counts as right for group top-1 |
| **held-out case** | a true cause deliberately left out of the library (wet coal, RCA-06) to test generalisation |
| **belief** | the code-only ranking: a log-odds score per candidate cause, updated from evidence each tick |
| **triage** | the urgency level (QUIET, WATCH, INVESTIGATE, URGENT); QUIET means no model call |
| **diagnostician** | the model job that ranks causes (water side and heat side in the multi-agent system) |
| **verifier** | the model job that passes or fails the top claims; it can only lower confidence |
| **note reader** | a former model job (Gemma 1B) that coded engineer notes into fixed words; retired in v3 |
| **gate** | code that checks every model answer (citations, line numbers), picks actions from the catalogue, publishes |
| **blackboard** | shared state between agents; each section has exactly one writer, enforced in code |
| **scheduler** | queues model jobs per lane, with priorities (P1 urgent … P3 background) and deadlines |
| **lockstep / real time** | tick waits for its model jobs (back to back) / tick fires every 30 s and never waits; model jobs run in the background on both lanes |
| **merge rule** | how a checked model answer changes the published ranking: `model`, `tiebreak`, `nudge`, `hybrid` |
| **filler case** | one of three library cases whose signature is flat on every sensor; the model may not promote them under `hybrid` |
| **group top-1** | headline accuracy: share of scored ticks whose published top cause is in the true look-alike group |
| **lead time** | minutes between the first correct call and the trip; negative means after the trip |
| **v2 / v3** | multi-agent prompt and configuration versions; v3 = no group letters, raw notes, `hybrid` |
| **mock** | a fake model used for plumbing tests on the laptop; never an AI result |
