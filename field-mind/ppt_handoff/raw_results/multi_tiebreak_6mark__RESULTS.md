# Benchmark `multi_tiebreak_6mark`

Architecture **multi**, mode **lockstep**, overlay `configs/fast.yaml`, merge rule `tiebreak`, models npu: `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`, cpu: `gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf`. Agent code ran on the laptop and only model calls on the board, so tick times are laptop times. Energy not measured. All plant data is synthetic.

Group top-1 is the share of scored ticks (fault onset onward) whose published rank-1 cause is in the true case's look-alike group; belief group top-1 is the same for the code-only ranking. A held-out true case has no exact top-1. Unusable = a model answer that was not used (wrong format, failed call, rejected note reading). Settings and every run, stopped ones included: `test.json`.

| episode | run | finished | ticks | model calls | unusable | tokens in | group top-1 | belief group top-1 | exact top-1 | faithfulness | actions P / R | lead time min | false alarms /h | tick p95 ms | wall s | chip C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ep_A01_fcv_seize | 1 | 2026-10-07 19:48 | 130 | 96 | 0 | 56,143 | 0.57 | 0.55 | 0.57 | 0.76 | 0.29 / 1.00 | 20.2 | – | 5041 | 257 | 44.8 → 62.1 |
| ep_B01_tube_leak | 1 | 2026-10-07 19:52 | 170 | 71 | 0 | 46,919 | 0.36 | 0.56 | 0.36 | 1.00 | 0.40 / 1.00 | – | – | 4520 | 218 | 58.3 → 63.7 |
| ep_C01_wet_coal | 1 | 2026-10-07 19:54 | 170 | 30 | 0 | 21,594 | 0.98 | 0.98 | – | 1.00 | 0.67 / 1.00 | – | – | 3465 | 104 | 60.1 → 64.7 |
| ep_D01_high_cv_coal | 1 | 2026-10-07 20:01 | 310 | 103 | 0 | 77,164 | 0.85 | 0.82 | 0.85 | 0.99 | 0.57 / 1.00 | 108.3 | – | 3826 | 368 | 60.1 → 64.8 |
| ep_E01_fouling_drift | 1 | 2026-10-07 20:02 | 390 | 10 | 0 | 4,035 | – | – | – | 1.00 | – | – | – | 7 | 30 | 62.1 → 62.9 |
| ep_N01_normal | 1 | 2026-10-07 19:44 | 80 | 5 | 0 | 2,013 | – | – | – | 1.00 | – | – | 0.00 | 6 | 17 | 47.3 → 47.5 |
