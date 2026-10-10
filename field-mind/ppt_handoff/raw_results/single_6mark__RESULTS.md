# Benchmark `single_6mark`

Architecture **single**, mode **lockstep**, overlay `None`, merge rule `None`, models npu: `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`. Agent code ran on the laptop and only model calls on the board, so tick times are laptop times. Energy not measured. All plant data is synthetic.

Group top-1 is the share of scored ticks (fault onset onward) whose published rank-1 cause is in the true case's look-alike group; belief group top-1 is the same for the code-only ranking. A held-out true case has no exact top-1. Unusable = a model answer that was not used (wrong format, failed call, rejected note reading). Settings and every run, stopped ones included: `test.json`.

| episode | run | finished | ticks | model calls | unusable | tokens in | group top-1 | belief group top-1 | exact top-1 | faithfulness | actions P / R | lead time min | false alarms /h | tick p95 ms | wall s | chip C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ep_A01_fcv_seize | 1 | 2026-10-07 17:08 | 130 | 147 | 14 | 233,599 | 0.56 | 0.55 | 0.17 | 0.76 | 0.27 / 1.00 | 17.2 | – | 35370 | 2112 | 36.8 → 64.8 |
| ep_N01_normal | 1 | 2026-10-07 16:32 | 80 | 0 | 0 | 0 | – | – | – | 1.00 | – | – | 0.00 | 0 | 8 | 34.7 → 36.6 |
