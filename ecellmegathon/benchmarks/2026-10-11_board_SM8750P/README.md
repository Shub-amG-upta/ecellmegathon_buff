# Board benchmarks, 2026-10-10/11: QIDK SM8750P (Snapdragon 8 Elite)

Every number below is copied from the files in this folder, with its label. BOARD = measured on the QIDK,
LAPTOP = measured on Sarthak's Windows laptop (Ryzen 7 6800H). Nothing here is estimated.

## Setup

| item | value |
|---|---|
| board | QIDK `d5196d2a`, **SM8750P (Snapdragon 8 Elite, Hexagon v79)**, Android 16, 15 GB RAM. Not the SM8650 FieldMind used |
| LLM | Llama 3.2 3B Instruct, pure Q4_0 (`Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`, sha256 `5aa3ece5…`, matches `board.board.CANDIDATES`) |
| runtime | llama.cpp b11371 / `99b95488c`, `--device HTP0 -ngl 99 -c 4096 -np 1 -fit off --cache-ram 0 -lv 4` |
| placement | `offloaded 29/29 layers`, HTP0 model buffer 1,911.90 MiB (board log, every launch) |
| client | laptop, over USB (`adb forward tcp:8080`), at `http://127.0.0.1:8080` (see "localhost" below) |
| code | run 1: branch `track-b-speech-eval` at `594d593` (before the v2 merge; context 4096) |

## 1. Text eval, run 1 (BOARD): `eval_BOARD_dev_run1_20261011_001741.json`

`eval/run_eval.py --label BOARD`, split **dev** (43 dev + 1 demo dialogs, 157 turns). Test split not run.
Started 00:11:08, saved 00:17:41 (laptop time), 0 errors. Routed through `ui_dev/live_monitor.py`.

**Teacher-forced** (each turn from its gold form):

| metric | value |
|---|---|
| tool accuracy | **0.975** |
| field accuracy | **0.657** |
| by field | t 33/43 · s 36/42 · l 35/44 · d 29/42 · a 12/27 · e 6/20 · p 7/18 · w 1/6 |
| turns with an extra value | 71 of 157 |
| values dropped by the gate | 18 |
| LLM call, median | wall 917.5 ms · prefill 83.4 ms (36 tok) · decode 787.5 ms (17 tok) |
| LLM call, mean | wall 1,141.0 ms · prefill 84.7 ms · decode 1,008.5 ms |

**Free-running** (each dialog from an empty form):

| metric | value |
|---|---|
| final field accuracy | **0.629** |
| by field | t 33/43 · s 34/42 · l 33/43 · d 26/42 · a 10/26 · e 6/20 · p 7/18 · w 2/6 |
| dialogs ended right | 27/44 |
| dialogs exactly right | 2 |
| dialogs with an extra value | 38 |

**Chip temperature during run 1** (`live_monitor_20261011.log`, 22 readings, 00:11:06–00:17:29):
NPU 40.3 → **105.5 °C**, CPU 48.4 → **104.6 °C**. Free memory 8.9 GiB throughout. Run 1 ran hot for most of
its length; whether that throttled the later calls is not separated here.

**Run 2: not run yet.** The plan (M4) runs each configuration twice; temperature-0 replies drift between
server launches. Run 2 should start after a fresh `board.board start` with the NPU back near 40–50 °C (it was
49.6 °C at 00:20:55).

## 2. Schema smoke test (BOARD): `smoke_BOARD_20261010_233957.json`

`eval/smoke_llm.py --label BOARD`, 23:39 laptop time: the JSON schema is accepted and every constrained reply
parses. "it's medium": prefill 675 tok / ~452 ms, decode 13 tok / ~530 ms, wall ~3,065 ms. The wall time here
went through `http://localhost:8080`, which adds ~2 s per request on Windows (next section).

## 3. "localhost" costs ~2 s per request on Windows (LAPTOP + BOARD)

Measured 2026-10-10 23:55, GET `/health` through the adb forward: **~2,050–2,125 ms via `localhost`** vs
**~7–15 ms via `127.0.0.1`** (Windows tries IPv6 `::1` first; the adb forward listens on IPv4 only).
The same three dialog turns (BOARD, LLM call wall time): **4,130 / 2,711 / 2,431 ms via localhost →
1,976 / 639 / 350 ms via 127.0.0.1**. Track B's defaults now use 127.0.0.1; `app/server.py` still defaults to
`localhost`, so set `LLM_URL=http://127.0.0.1:8080` on Windows.

## 4. Stability (BOARD)

- An earlier eval attempt (before run 1, same build and model) ended with llama-server aborting
  `ggml-hexagon.cpp:4036: ggml-hex: dspqueue_read failed: 0x0000002e`, at 3 min 59 s server uptime (launched
  23:39:18, so about 23:43), on task 476. No result file came from it. Chip temperature at the time was not
  recorded (the monitor did not exist yet).
- Run 1 completed with 0 errors.

## 5. Laptop-only results (`laptop/`)

- `wer_sweep_LAPTOP_SYNTHETIC_20261010-203325.json`: the `eval/wer.py --sweep` pipeline check on **synthetic
  audio** (Piper TTS reading the 36 subset lines; 10 with white noise at ~10 dB SNR). Not human speech; **not
  used for the STT decision** (`eval/STT_DECISION.md`). These ran over `localhost`, so each STT time includes
  the ~2 s Windows delay. Table (LAPTOP):

  | model | vocab | WER | key-word err | STT ms median |
  |---|---|---|---|---|
  | tiny.en | off | 6.3% | 13.3% | 471 |
  | tiny.en | on | 8.8% | 14.7% | 651 |
  | base.en | off | 5.5% | 9.3% | 985 |
  | base.en | on | 7.7% | 13.3% | 1,280 |
  | small.en | off | 4.8% | 9.3% | 3,511 |
  | small.en | on | 4.7% | 8.0% | 4,274 |

- `tts_check_LAPTOP_20261010-201348_*`: `voice/tts_check.py` timings and the listening checklist (Piper
  en_US-lessac-medium, 11 phrases): engine 0.15–1.0 s per phrase; ~4.0–4.7 s fresh end to end (process start,
  voice load, and the localhost delay). The listening column is still to be filled in by humans.

## Files

| file | what |
|---|---|
| `eval_BOARD_dev_run1_20261011_001741.json` | full output of run 1 (every turn and dialog) |
| `smoke_BOARD_20261010_233957.json` | schema smoke test |
| `live_monitor_20261011.log` | every LLM call 00:06–00:29 (run 1 plus a few manual test turns) with tokens and timings, board temperature and memory every ~10–15 s |
| `laptop/` | the LAPTOP files above |
