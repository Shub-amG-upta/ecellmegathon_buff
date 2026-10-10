# Benchmark `multi_v3`

Architecture **multi**, mode **lockstep**, overlay `configs/v3.yaml`, merge rule `hybrid`, models npu: `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`, cpu: `gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf`. Agent code ran on the laptop and only model calls on the board, so tick times are laptop times. Energy not measured. All plant data is synthetic.

Group top-1 is the share of scored ticks (fault onset onward) whose published rank-1 cause is in the true case's look-alike group; belief group top-1 is the same for the code-only ranking. A held-out true case has no exact top-1. Unusable = a model answer that was not used (wrong format, failed call, rejected note reading). Settings and every run, stopped ones included: `test.json`.

| episode | run | finished | ticks | model calls | unusable | tokens in | group top-1 | belief group top-1 | exact top-1 | faithfulness | actions P / R | lead time min | false alarms /h | tick p95 ms | wall s | chip C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ep_A01_fcv_seize | 1 | 2026-10-08 01:09 | 130 | 74 | 0 | 46,958 | 0.89 | 0.55 | 0.89 | 0.86 | 0.29 / 1.00 | 22.2 | – | 4669 | 212 | 34.9 → 59.0 |
| ep_A02_fcv_seize_fast | 1 | 2026-10-08 01:30 | 80 | 58 | 0 | 32,723 | 0.58 | 0.25 | 0.06 | 0.99 | 0.31 / 1.00 | -5.9 | – | 5113 | 160 | 55.2 → 64.5 |
| ep_A03_bfp_suction | 1 | 2026-10-08 01:33 | 110 | 64 | 0 | 39,087 | 0.81 | 0.45 | 0.11 | 0.99 | 0.36 / 1.00 | -0.5 | – | 5101 | 194 | 60.8 → 64.5 |
| ep_B01_tube_leak | 1 | 2026-10-08 01:18 | 170 | 60 | 0 | 41,120 | 1.00 | 0.56 | 1.00 | 1.00 | 0.80 / 1.00 | – | – | 3075 | 173 | 83.7 → 62.1 |
| ep_B02_tube_leak_fast | 1 | 2026-10-08 01:35 | 80 | 25 | 0 | 18,606 | 1.00 | 0.86 | 1.00 | 1.00 | 1.00 / 0.75 | – | – | 3058 | 80 | 61.2 → 64.1 |
| ep_B03_tube_leak_slow | 1 | 2026-10-08 01:42 | 290 | 123 | 0 | 79,580 | 0.60 | 0.28 | 0.60 | 1.00 | 0.33 / 1.00 | – | – | 5442 | 388 | 60.1 → 63.7 |
| ep_C01_wet_coal | 1 | 2026-10-08 01:20 | 170 | 24 | 0 | 18,255 | 0.63 | 0.98 | – | 1.00 | 0.33 / 0.75 | – | – | 3532 | 90 | 58.5 → 62.5 |
| ep_C02_feeder_trip | 1 | 2026-10-08 01:45 | 100 | 59 | 0 | 39,190 | 0.32 | 0.53 | 0.32 | 0.99 | 0.43 / 1.00 | – | – | 6086 | 184 | 59.7 → 62.9 |
| ep_C03_wet_coal_mild | 1 | 2026-10-08 01:47 | 190 | 26 | 0 | 20,282 | 0.38 | 0.47 | – | 0.93 | 0.50 / 1.00 | – | – | 3577 | 100 | 58.9 → 61.7 |
| ep_D01_high_cv_coal | 1 | 2026-10-08 01:26 | 310 | 96 | 0 | 71,934 | 0.83 | 0.82 | 0.83 | 0.99 | 0.44 / 1.00 | 114.8 | – | 3793 | 346 | 58.9 → 65.1 |
| ep_E01_fouling_drift | 1 | 2026-10-08 01:27 | 390 | 0 | 0 | 0 | – | – | – | 1.00 | – | – | – | 3 | 10 | 61.2 → 57.1 |
| ep_N01_normal | 1 | 2026-10-08 01:05 | 80 | 0 | 0 | 0 | – | – | – | 1.00 | – | – | 0.00 | 5 | 10 | 33.3 → 35.6 |
