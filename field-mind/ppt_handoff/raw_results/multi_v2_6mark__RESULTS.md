# Benchmark `multi_v2_6mark`

Architecture **multi**, mode **lockstep**, overlay `configs/fast.yaml`, merge rule `model`, models npu: `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`, cpu: `gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf`. Agent code ran on the laptop and only model calls on the board, so tick times are laptop times. Energy not measured. All plant data is synthetic.

Group top-1 is the share of scored ticks (fault onset onward) whose published rank-1 cause is in the true case's look-alike group; belief group top-1 is the same for the code-only ranking. A held-out true case has no exact top-1. Unusable = a model answer that was not used (wrong format, failed call, rejected note reading). Settings and every run, stopped ones included: `test.json`.

| episode | run | finished | ticks | model calls | unusable | tokens in | group top-1 | belief group top-1 | exact top-1 | faithfulness | actions P / R | lead time min | false alarms /h | tick p95 ms | wall s | chip C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ep_A01_fcv_seize | 1 | 2026-10-07 15:32 | 130 | 72 | 0 | 46,871 | 0.59 | 0.55 | 0.22 | 1.00 | 0.31 / 1.00 | 20.2 | – | 4740 | 204 | 38.2 → 60.6 |
| ep_B01_tube_leak | 1 | 2026-10-07 15:41 | 170 | 95 | 0 | 57,167 | 0.09 | 0.56 | 0.09 | 1.00 | 0.57 / 1.00 | – | – | 5001 | 248 | 38.7 → 62.9 |
| ep_C01_wet_coal | 1 | 2026-10-07 15:48 | 170 | 30 | 0 | 21,594 | 0.79 | 0.98 | – | 1.00 | 0.43 / 0.75 | – | – | 3136 | 94 | 38.0 → 56.7 |
| ep_D01_high_cv_coal | 1 | 2026-10-07 15:58 | 310 | 147 | 0 | 96,584 | 0.03 | 0.82 | 0.03 | 1.00 | 0.40 / 1.00 | -12.7 | – | 5326 | 435 | 39.1 → 65.5 |
| ep_E01_fouling_drift | 1 | 2026-10-07 16:04 | 390 | 10 | 0 | 4,035 | – | – | – | 1.00 | – | – | – | 4 | 27 | 38.4 → 50.8 |
| ep_N01_normal | 1 | 2026-10-07 15:28 | 80 | 5 | 0 | 2,013 | – | – | – | 1.00 | – | – | 0.00 | 3 | 17 | 34.5 → 44.6 |
