# Benchmark `multi_nudge_6.1mark`

Architecture **multi**, mode **lockstep**, overlay `configs/fast.yaml`, merge rule `nudge`, models npu: `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`, cpu: `gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf`. Agent code ran on the laptop and only model calls on the board, so tick times are laptop times. Energy not measured. All plant data is synthetic.

Group top-1 is the share of scored ticks (fault onset onward) whose published rank-1 cause is in the true case's look-alike group; belief group top-1 is the same for the code-only ranking. A held-out true case has no exact top-1. Unusable = a model answer that was not used (wrong format, failed call, rejected note reading). Settings and every run, stopped ones included: `test.json`.

| episode | run | finished | ticks | model calls | unusable | tokens in | group top-1 | belief group top-1 | exact top-1 | faithfulness | actions P / R | lead time min | false alarms /h | tick p95 ms | wall s | chip C |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ep_A02_fcv_seize_fast | 1 | 2026-10-07 20:51 | 80 | 52 | 0 | 30,887 | 0.30 | 0.25 | 0.00 | 0.99 | 0.25 / 0.75 | – | – | 4647 | 151 | 34.5 → 57.9 |
| ep_A03_bfp_suction | 1 | 2026-10-07 20:57 | 110 | 64 | 0 | 40,478 | 0.46 | 0.45 | 0.00 | 0.98 | 0.36 / 1.00 | – | – | 4158 | 186 | 39.7 → 61.4 |
| ep_B02_tube_leak_fast | 1 | 2026-10-07 21:15 | 80 | 38 | 0 | 25,090 | 0.88 | 0.86 | 0.88 | 1.00 | 0.80 / 1.00 | – | – | 4768 | 101 | 40.5 → 58.7 |
| ep_B03_tube_leak_slow | 1 | 2026-10-07 21:24 | 290 | 106 | 0 | 75,359 | 0.30 | 0.28 | 0.30 | 1.00 | 0.44 / 1.00 | – | – | 3528 | 327 | 40.5 → 63.7 |
| ep_C02_feeder_trip | 1 | 2026-10-07 21:04 | 100 | 56 | 0 | 40,240 | 0.55 | 0.53 | 0.55 | 1.00 | 0.50 / 1.00 | – | – | 5682 | 172 | 39.5 → 61.0 |
| ep_C03_wet_coal_mild | 1 | 2026-10-07 21:10 | 190 | 37 | 0 | 25,132 | 0.94 | 0.47 | – | 0.93 | 0.57 / 1.00 | – | – | 3353 | 117 | 40.3 → 58.7 |
