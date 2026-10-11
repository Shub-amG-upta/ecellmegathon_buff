# How to run the latest code

For Track B (Sarthak). Everything below is on `main`. Run commands from the `ecellmegathon/` folder.
Python is `.venv/bin/python` on macOS/Linux and `.venv\Scripts\python` on Windows (written `PY` below).

---

## 1. Update and check

```
git checkout main && git pull
cd ecellmegathon
PY -m pytest -q                      # expect: 197 passed
```

No `.venv` yet? `python3 -m venv .venv` then `PY -m pip install pytest`.

## 2. What changed since your last pull

| change | what it means for you |
|---|---|
| **Form = the problem statement's 10 fields, 15 incident types** (`fire`, `slip_trip`, `fall_height`, …), asked in the order type → location → time → severity | UI field names and spoken questions are unchanged; types show with `_` as a space |
| **Fast path:** code answers about half of all turns without the LLM (bare "medium", exact type names, pure times, short places, "submit", "cancel") | the turn result has `fast_path: true` and `null` LLM timings for those turns |
| **The form comes back before the voice:** the server returns the turn at once and makes the speech in the background | `timings_ms.tts` is `null` in the turn result; `GET audio_url` waits for the audio and sends `X-TTS-Ms`; `GET /api/state` has the TTS time afterwards |
| **Sentence-by-sentence speech** (`TTS_SPLIT=on`, off by default) | the result also has `audio_parts`: one URL per sentence, in order; play them one after another (code in §7) |
| **Silence trimming** before STT (`STT_TRIM`, on by default) | voice turns return `audio_trim: {in_ms, out_ms}` |
| **LLM context 2048** (was 4096); optional `SPEC=1` copy-ahead decoding and `LLM_CONSTRAINT=gbnf` (no-whitespace grammar) | board-day comparisons, nothing to do now |
| **Eval set:** `eval/nlu/` (65 dialogs, 250 turns); recording list `eval/speech_subset.jsonl` (36 lines, Case 1–25) | your `wer.py` reads it as before |

## 3. Start the voice servers (STT :8082, TTS :8083) on the laptop

```
voice/laptop_up.sh                                    # prints ok :8082, ok :8083 and export lines
eval "$(voice/laptop_up.sh | grep '^export')"         # same, and sets STT_URL / TTS_URL in this shell
```

Stop them with `voice/laptop_up.sh stop`. (On Windows, run the scripts from Git Bash.)

## 4. Run the app and the UI

**Without an LLM (no model on this laptop yet).** Speech in and speech out work; extraction answers only
through the fast path, and everything else says it got nothing:

```
LLM=mock PY -m app.server                             # http://localhost:8000
```

Try it (checked): say anything first ("hello"), then answer each question with just the answer:
"fire" → "meter room" → "3 PM" → "medium" → "submit". The fast path handles all of those in code, so a full report
goes through with the mock and no model. Answering before you're asked (e.g. "fire" as the first thing) needs the
real LLM.

**With an LLM on the laptop** (once a GGUF is here; LAPTOP numbers only):

```
llama-server -m <path>/<model>.gguf --port 8080 -c 2048 -np 1 --cache-ram 0      # terminal 1
LLM=llamaserver LLM_LANE=laptop LLM_MODEL=<model>.gguf PY -m app.server         # terminal 2
PY eval/smoke_llm.py --label LAPTOP --model <model>.gguf                          # schema accepted? timings
```

**Switches** (environment variables for `app.server`):

| variable | default | meaning |
|---|---|---|
| `LLM` | `mock` | `mock` or `llamaserver` |
| `LLM_URL` / `LLM_LANE` / `LLM_MODEL` | `http://localhost:8080` / `npu` / `unknown` | where the LLM is, and labels for the UI and results |
| `LLM_CONSTRAINT` | `schema` | `gbnf` = the no-whitespace grammar |
| `VOICE` | `on` | `off` = text only (no STT/TTS calls) |
| `STT_TRIM` | `on` | `off` = don't cut silence before STT |
| `TTS_SPLIT` | `off` | `on` = also serve `audio_parts` (needs §7 in the UI) |
| `PORT` | `8000` | the app's port |
| `POSTFIX` | `on` | the v4 post-model fixes (`app/postfix.py`) |
| `PROMPT` | `1` (`2` on the v5 branch) | system prompt version (v5's prompt v2 was dropped on the board) |
| `ANSWER` | `full` (`compact` on the v6 branch) | how the model writes its answer: the full tool call, or only the fields object / `"S"` `"C"` `"N"` (code turns it back into the same tool call) |

## 5. Record the speech eval

Script: `eval/RECORDING_SCRIPT.md` (Case 1–25, 36 lines; read only the bold lines). Guide: `eval/RECORDING.md`.

```
PY -m ui_dev.record_server                            # then open http://localhost:8001/record.html
eval/record_lines.sh <name>                           # or the terminal recorder (macOS/Linux)
eval/record_lines.sh <name> --noisy                   # the 10 noisy lines
```

Check: 36 files per speaker and 10 noisy (`ls eval/audio/*_<name>.wav | wc -l`). Zip for the shared drive; never commit
`eval/audio/`.

## 6. Run the evals (LAPTOP)

```
PY eval/wer.py --label LAPTOP --subset                                     # WER on the 36 lines
PY eval/wer.py --label LAPTOP --subset --sweep tiny.en,base.en,small.en    # model comparison (see --help)
PY -m voice.tts_check --label LAPTOP                                       # the fixed TTS phrases, timed
```

With an LLM running (§4):

```
LLM=llamaserver LLM_LANE=laptop LLM_MODEL=<model>.gguf PY eval/run_eval.py --label LAPTOP               # typed
LLM=llamaserver LLM_LANE=laptop LLM_MODEL=<model>.gguf PY eval/run_eval.py --label LAPTOP --audio eval/audio   # typed vs spoken
```

Useful flags: `--no-fast-path` (every turn to the LLM), `--no-trim` (audio not trimmed), `--split test`
(locked until the decision rules exist), `--mode teacher|free`.

## 7. Your UI to-dos

1. **Play `audio_parts` in order** when a turn has them (for `TTS_SPLIT=on`):

   ```js
   async function playParts(urls) {
     for (const u of urls) {
       if (player) player.pause();
       player = new Audio(u);                 // the server holds each part until it's ready
       await new Promise(done => { player.onended = done; player.onerror = done; player.play().catch(done); });
     }
   }
   // in render(r):  if (r.audio_parts?.length) playParts(r.audio_parts); else if (r.audio_url) play(r.audio_url);
   ```

2. **Latency panel:** the TTS time now arrives after the turn. Read `X-TTS-Ms` from the audio response, or
   `GET /api/state` after it plays (`timings_ms.tts`, and `tts_first` with `TTS_SPLIT=on`). For `fast_path: true`
   turns, show "answered by code" instead of LLM times.
3. **Keep the TTS voice loaded** (`voice/tts_server.py`): today each sentence starts `sherpa-onnx-offline-tts`
   again (over adb on the board). Measure on the board; if loading is most of the time, make it a resident process.

## 8. On the QIDK (the one-session checklist)

Full list with pass checks: `BUILD_PLAN.md` §9. In short:

```
board/up.sh                         # LLM on the NPU (checks all layers on HTP0), then STT + TTS via board/voice_up.sh
PY eval/smoke_llm.py --label BOARD --model Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf
LLM=llamaserver LLM_LANE=npu LLM_MODEL=Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf PY -m app.server
```

Then the evals with `--label BOARD`, and the speed comparisons in §9 (`SPEC=1`, `TTS_SPLIT=on`,
`LLM_CONSTRAINT=gbnf`, `--no-fast-path`), one at a time. Stop: `board/voice_up.sh stop`, `PY -m board.board stop`.

## 9. Rules (unchanged)

Only edit Track B files (`BUILD_PLAN.md` §5); ask before touching `app/server.py`, `app/dialog.py`, the schema or
`eval/nlu/`. No cloud APIs. Label every number LAPTOP or BOARD. Never commit recordings. Run the tests before every
commit, and push your own branch.

## 10. v4 and v5 on the board (rules: `eval/DECISION_v4_v5.md`, written before these runs)

`v4` = v3 + post-model fixes (`POSTFIX=on`). `v5` = v4 + prompt v2 (`PROMPT=2`). Both branches contain the same
code; the `v5` branch only switches the prompt default. Any version can be run from either branch with the
switches, so check out `v5` and run all of them:

```
git fetch origin && git checkout v5
EVAL_HEAT_PAUSE=90:80 POSTFIX=on PROMPT=1 PY benchmarks/run_bench.py --tag v4 --runs 2
EVAL_HEAT_PAUSE=90:80 POSTFIX=on PROMPT=2 PY benchmarks/run_bench.py --tag v5 --runs 2
EVAL_HEAT_PAUSE=90:80 COOL_CPU=1 PROMPT=2 PY benchmarks/run_bench.py --tag v5-coolcpu --runs 2   # on the kept one
EVAL_PACE=4 EVAL_HEAT_PAUSE=90:80 PROMPT=2 PY benchmarks/run_bench.py --tag v5-paced --runs 1      # realistic load
PY benchmarks/compare.py                   # the table
PY benchmarks/analyze.py <run.json> --compare <other run.json> --out <file.md>   # the detailed report
```

PowerShell: set each variable first, e.g. `$env:POSTFIX='on'; $env:PROMPT='2'; $env:EVAL_HEAT_PAUSE='90:80'`.
The eval result records `postfix`, `prompt` and `throttle` (pauses, NPU peak); `COOL_CPU` shows in the server
command saved by run_bench. **Use the same cooling (fan) for every run you compare, and write it down.**
`python -m board.board temp` now also prints Android's thermal status (NONE … SHUTDOWN).

## 11. v6 on the board (rule: `eval/DECISION_v6.md`, written before these runs)

`v6` = **v4** + compact answers (v5's prompt v2 failed its rule, so v6 uses prompt v1): the model writes `{"t":"fire","l":"lab"}` instead of
`{"tool":"update_report","args":{"t":"fire","l":"lab"}}`, and `"S"` / `"C"` / `"N"` for submit / cancel / nothing.
Half of a full answer was that forced wrapper, and each forced token still costs ~48 ms of decoding on the NPU.
Compare it with the configuration DECISION_v4_v5.md kept, in the same session, same cooling:

```
git fetch origin && git checkout v6
EVAL_HEAT_PAUSE=90:80 ANSWER=full    PY benchmarks/run_bench.py --tag v6-baseline --runs 2
EVAL_HEAT_PAUSE=90:80 ANSWER=compact PY benchmarks/run_bench.py --tag v6 --runs 2
PY benchmarks/compare.py
```

The baseline is v4 (`ANSWER=full`); v6 changes only `ANSWER`. The eval output records
`answer`; compare `decode_tokens` per LLM call as well as accuracy.

## 12. v7 on the board: Gemma decides the incident type (rule: `eval/DECISION_v7.md`, written before these runs)

`v7` = the v4 configuration + a type router: Llama answers every turn; on the first description Gemma 3 4B decides
the incident type (it beat Llama on type 8–0 on the board). Gemma runs as a second llama-server on the NPU
(lane `type`, port 8084, `--swa-full`); `run_bench.py` starts it when `TYPE_MODEL` is set. The Gemma file must be
on the board (`/data/local/tmp/llm/gemma-3-4b-it-Q4_0-pure-embq8.gguf`, sha256 in `board/board.py`).

```
git fetch origin && git checkout v7
EVAL_HEAT_PAUSE=90:80 PY benchmarks/run_bench.py --tag v7-baseline --runs 1
EVAL_HEAT_PAUSE=90:80 TYPE_MODEL=gemma-3-4b-it-Q4_0-pure-embq8.gguf PY benchmarks/run_bench.py --tag v7 --runs 2
PY benchmarks/compare.py
```

The eval prints `type router: … calls · type changed … · failures …` and saves it under `router`.
Same cooling (fan) for both; write it down. Then, in the same session if time allows, the CPU heat test:
`COOL_CPU=1 EVAL_HEAT_PAUSE=90:80 PY benchmarks/run_bench.py --tag v7-coolcpu --runs 1` (with or without
`TYPE_MODEL`, whichever v7's rule keeps).

## 13. v8 on the board: the type router and the speed switches PREFILL and SPEC (rule: `eval/DECISION_speed.md`, written before these runs)

`PREFILL=on`: the fixed start of the answer goes into the prompt instead of being decoded (~330 ms less per LLM call,
projected). `SPEC=1`: llama-server guesses tokens copied from the prompt and checks them in one batch. One session,
same cooling, on `v8`:

```
git fetch origin && git checkout v8
EVAL_HEAT_PAUSE=90:80                                          PY benchmarks/run_bench.py --tag v8-baseline --runs 1
EVAL_HEAT_PAUSE=90:80 PREFILL=on                               PY benchmarks/run_bench.py --tag v8-prefill --runs 1
EVAL_HEAT_PAUSE=90:80 SPEC=1                                   PY benchmarks/run_bench.py --tag v8-spec --runs 1
EVAL_HEAT_PAUSE=90:80 PREFILL=on SPEC=1                        PY benchmarks/run_bench.py --tag v8-prefill-spec --runs 1
EVAL_HEAT_PAUSE=90:80 TYPE_MODEL=gemma-3-4b-it-Q4_0-pure-embq8.gguf PY benchmarks/run_bench.py --tag v8-router --runs 2
PY benchmarks/compare.py
```

(This baseline also serves section 12's router comparison.) If `SPEC=1` stops with "no --spec-type ngram-simple flags", the board's
llama.cpp is too old for it: skip that run and note it. The eval output records `prefill`; the server command saved by
run_bench shows the `--spec-…` flags.
