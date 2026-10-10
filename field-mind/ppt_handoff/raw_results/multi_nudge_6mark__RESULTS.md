# Benchmark `multi_nudge_6mark`

Architecture **multi**, mode **lockstep**, overlay `configs/fast.yaml`, merge rule `nudge`, models npu: `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`, cpu: `gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf`. Agent code ran on the laptop and only model calls on the board, so tick times are laptop times. Energy not measured. All plant data is synthetic.

Group top-1 is the share of scored ticks (fault onset onward) whose published rank-1 cause is in the true case's look-alike group; belief group top-1 is the same for the code-only ranking. A held-out true case has no exact top-1. Unusable = a model answer that was not used (wrong format, failed call, rejected note reading). Settings and every run, stopped ones included: `test.json`.

| episode | run | finished | ticks | model calls | unusable | tokens in | group top-1 | belief group top-1 | exact top-1 | faithfulness | actions P / R | lead time min | false alarms /h | tick p95 ms | wall s | chip C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ep_A01_fcv_seize | 1 | 2026-10-07 20:08 | 130 | 90 | 0 | 54,500 | 0.68 | 0.55 | 0.68 | 0.86 | 0.29 / 1.00 | 5.7 | – | 5252 | 262 | 52.7 → 64.1 |
| ep_B01_tube_leak | 1 | 2026-10-07 20:13 | 170 | 71 | 0 | 46,917 | 0.50 | 0.56 | 0.50 | 1.00 | 0.44 / 1.00 | – | – | 3538 | 213 | 50.8 → 63.7 |
| ep_C01_wet_coal | 1 | 2026-10-07 20:15 | 170 | 30 | 0 | 21,594 | 1.00 | 0.98 | – | 1.00 | 0.80 / 1.00 | – | – | 3433 | 103 | 53.6 → 63.7 |
| ep_D01_high_cv_coal | 1 | 2026-10-07 20:22 | 310 | 103 | 0 | 77,282 | 0.82 | 0.82 | 0.82 | 0.98 | 0.57 / 1.00 | 103.3 | – | 3588 | 354 | 50.2 → 61.0 |
| ep_E01_fouling_drift | 1 | 2026-10-07 20:23 | 390 | 10 | 0 | 4,035 | – | – | – | 1.00 | – | – | – | 7 | 30 | 51.9 → 60.2 |
| ep_N01_normal | 1 | 2026-10-07 20:03 | 80 | 5 | 0 | 2,013 | – | – | – | 1.00 | – | – | 0.00 | 6 | 18 | 49.0 → 55.4 |
