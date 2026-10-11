# AI Incident Report Assistant (Megathon X, Qualcomm PS1)

Setup and run instructions. All code is in [`ecellmegathon/`](ecellmegathon/); every command below runs from that
folder. Final report: [`deliverables/final_report.pdf`](deliverables/final_report.pdf).

---

## 1. Requirements

### Hardware
| | |
|---|---|
| **Qualcomm QIDK** | Snapdragon 8 Elite (SM8750P), Android, USB debugging on. All AI models run here |
| **Laptop** | macOS or Linux (Windows with Git Bash also works). Drives the board over USB and shows the web page |
| Microphone | the laptop's, for spoken input (a headset works best) |

### Software on the laptop
| what | version | install |
|---|---|---|
| Python | 3.12 or newer | python.org, or your package manager |
| adb | Android platform-tools | `brew install --cask android-platform-tools` (macOS), `sudo dnf install android-tools` (Fedora) |
| pytest | any | `pip install pytest` (only for tests) |
| A browser | Chrome or Edge | for the web page (microphone access) |

The app itself uses the Python standard library only; nothing else to `pip install`.

### Software on the QIDK (built once, pushed by the scripts)
| what | version | where it goes on the board |
|---|---|---|
| llama.cpp `llama-server` with the Hexagon (HTP) backend | tag `b11371`, commit `99b95488` | `/data/local/tmp/llm/llama.cpp/` |
| whisper.cpp `whisper-server`, Android arm64-v8a | any recent release | `/data/local/tmp/stt/` (pushed by `board/voice_up.sh`) |
| sherpa-onnx `sherpa-onnx-offline-tts` (+ `sherpa-onnx-keyword-spotter`), Android arm64-v8a | any recent release | `/data/local/tmp/tts/`, `/data/local/tmp/kws/` (pushed by `board/voice_up.sh`) |

Build recipes (Android NDK, Hexagon SDK container, pinned commits, hashes):
[`ecellmegathon/docs/reference/phase0b_board_setup.md`](ecellmegathon/docs/reference/phase0b_board_setup.md) and
[`ecellmegathon/docs/MODELS_AND_DEPENDENCIES.md`](ecellmegathon/docs/MODELS_AND_DEPENDENCIES.md).
Source code: [llama.cpp](https://github.com/ggml-org/llama.cpp), [whisper.cpp](https://github.com/ggml-org/whisper.cpp),
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx). Hexagon build container: `ghcr.io/snapdragon-toolchain/arm64-android:v0.7`.

---

## 2. Model weights (externally hosted)

None are in this repository. Download them, then build or copy as shown.

| use | model | source | file the scripts expect |
|---|---|---|---|
| **LLM (required)** | Llama 3.2 3B Instruct | [unsloth/Llama-3.2-3B-Instruct](https://huggingface.co/unsloth/Llama-3.2-3B-Instruct) (revision `006f5dcd13`) | `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`, sha256 `5aa3ece5…eadcdb` |
| Incident-type model (recommended) | Gemma 3 4B Instruct | [unsloth/gemma-3-4b-it](https://huggingface.co/unsloth/gemma-3-4b-it) (revision `bf46152c47f5`) | `gemma-3-4b-it-Q4_0-pure-embq8.gguf`, sha256 `ff8946f7…5677df` |
| **Speech-to-text (required)** | Whisper base.en | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp/tree/main) | `ggml-base.en.bin` |
| Speech-to-text, Hindi/Hinglish | Whisper base (multilingual) | [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp/tree/main) | `ggml-base.bin` |
| **Text-to-speech (required)** | Piper en_US lessac medium | [sherpa-onnx tts-models](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) | folder `vits-piper-en_US-lessac-medium/` |
| "Hey Qualcomm" keyword spotter (optional) | zipformer KWS gigaspeech 3.3M | [sherpa-onnx kws-models](https://github.com/k2-fsa/sherpa-onnx/releases/tag/kws-models) | folder `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01/` + `keywords.txt` |

**LLM files must be "pure Q4_0"** (every weight matrix Q4_0, embedding and output Q8_0); published Q4_0 GGUFs
are not, and the board scripts refuse any file whose sha256 differs from the allow-list in `board/board.py`.
Build them from the safetensors above:

```
board/build_candidate_gguf.sh Llama-3.2-3B-Instruct  <path to unsloth/Llama-3.2-3B-Instruct>
board/build_candidate_gguf.sh gemma-3-4b-it          <path to unsloth/gemma-3-4b-it> \
    --override-kv tokenizer.ggml.eos_token_id=int:1
adb push <file>.gguf /data/local/tmp/llm/           # then compare: adb shell sha256sum /data/local/tmp/llm/<file>.gguf
```

Keyword file for "Hey Qualcomm" (only with the optional spotter; needs `pip install sherpa-onnx sentencepiece`):
```
cd <kws model folder>
printf 'HEY QUALCOMM @HEY_QUALCOMM\nHI QUALCOMM @HEY_QUALCOMM\n' > keywords_raw.txt
sherpa-onnx-cli text2token --tokens tokens.txt --tokens-type bpe --bpe-model bpe.model keywords_raw.txt keywords.txt
```

### Folder layout for speech models and binaries
`ENGINES` defaults to `../../engines` relative to the repo's `ecellmegathon/` folder:
```
engines/
  whisper/ggml-base.en.bin   whisper/ggml-base.bin            (+ whisper/whisper-server for laptop-only runs)
  voices/vits-piper-en_US-lessac-medium/
  kws/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01/   (optional, with keywords.txt)
  android/stt/bin/whisper-server        android/stt/lib/*.so
  android/tts/bin/sherpa-onnx-offline-tts  android/tts/bin/sherpa-onnx-keyword-spotter  android/tts/lib/*.so
  sherpa/sherpa-onnx-<version>/bin/sherpa-onnx-offline-tts    (laptop-only runs)
```

### Datasets
The evaluation sets are in this repository (no external download): `ecellmegathon/eval/nlu/` (65 dialogs, 250 turns),
`ecellmegathon/eval/nlu15/` (80 dialogs, 323 turns), `ecellmegathon/eval/hinglish/` (10 dialogs). Voice recordings are not
published.

---

## 3. Setup

```
git clone https://github.com/Shub-amG-upta/ecellmegathon_buff.git
cd ecellmegathon_buff/ecellmegathon
python3 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q                         # all tests pass; no board or model needed
adb devices                                           # the QIDK must be listed
```

---

## 4. Run on the QIDK (the demo setup)

```
# 1. LLM + incident-type model on the NPU, then speech-to-text and text-to-speech on the board's CPU
TYPE_MODEL=gemma-3-4b-it-Q4_0-pure-embq8.gguf board/up.sh
#    must print "OK: placement confirmed" for each model; it prints the next commands

# 2. Export what board/up.sh (board/voice_up.sh) printed, e.g.
export STT_URL=http://127.0.0.1:8082 STT_DEVICE=cpu STT_MODEL=ggml-base.en.bin TTS_URL=http://127.0.0.1:8083

# 3. Start the app
TYPE_MODEL=gemma-3-4b-it-Q4_0-pure-embq8.gguf LLM=llamaserver LLM_LANE=npu \
    LLM_MODEL=Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf .venv/bin/python -m app.server

# 4. Open http://127.0.0.1:8000 in Chrome or Edge. Hold the button (or Space) and speak, or type.
```

Minimal setup (one model, no type model): `board/up.sh`, then step 3 without `TYPE_MODEL`.

Stop everything: `board/voice_up.sh stop` and `.venv/bin/python -m board.board stop`.

Useful board commands:
```
.venv/bin/python -m board.board check npu       # NPU placement from the server log
.venv/bin/python -m board.board temp            # chip temperatures
.venv/bin/python -m board.board log npu         # llama-server log
```

### Optional features
| feature | how to turn it on |
|---|---|
| Hindi / Hinglish speech | `HINGLISH=1 board/up.sh` (needs `ggml-base.bin`), then start the app with `HINGLISH=1`; choose the language on the page |
| "Hey Qualcomm" hands-free | on by default when speech-to-text is up (click **Hands-free** on the page); the keyword spotter is used when its model folder exists under `engines/kws/` (`board/voice_up.sh` prints `export KWS_URL=...`) |
| Text-only page (no voice) | start the app with `VOICE=off` |
| The page full-screen on the QIDK, with the board's microphone | `.venv/bin/python android/build_apk.py --install` (needs an Android SDK with build-tools and a JDK; `--sdk <path>` or `ANDROID_HOME`), then open **Incident Assistant** on the board |

### Run without the board (laptop only; numbers are not board numbers)
```
.venv/bin/python -m app.server                                    # mock LLM, text only: http://127.0.0.1:8000
llama-server -m <Llama 3.2 3B GGUF> -c 2048 --port 8080 &         # brew install llama.cpp
voice/laptop_up.sh                                                # laptop STT + TTS; export what it prints
LLM=llamaserver LLM_LANE=laptop LLM_MODEL=<file> .venv/bin/python -m app.server
```
Command line instead of the page: `.venv/bin/python -m app.cli`.

---

## 5. Configuration flags

Environment variables. Defaults are the demo configuration.

### App server (`python -m app.server`)
| flag | values (default first) | effect |
|---|---|---|
| `LLM` | `mock` \| `llamaserver` | language model backend |
| `LLM_URL` | `http://127.0.0.1:8080` | llama-server address |
| `LLM_LANE` | `npu` \| `cpu` \| `laptop` | label for where the LLM runs (shown in the page and saved reports) |
| `LLM_MODEL` | file name | model name for the page and reports |
| `PORT` | `8000` | web page / API port |
| `REPORT_DIR` | `reports_out/` | where submitted reports are saved |
| `VOICE` | `on` \| `off` | `off`: text only |
| `TYPE_MODEL` | unset \| GGUF name | second model decides the incident type |
| `TYPE_MODEL_URL` | `http://127.0.0.1:8084` | its llama-server address |
| `TYPE_PREFILL` | `off` \| `on` | shorter type-model answer |
| `TYPE_PARALLEL` | `0` \| `1` | type model runs at the same time as the main model |
| `PREFILL` | `off` \| `on` | fixed start of the answer written into the prompt |
| `LLM_CONSTRAINT` | `schema` \| `gbnf` | how the answer format is enforced |
| `PROMPT` | `1` \| `2` | system prompt version |
| `ANSWER` | `full` \| `compact` | answer format |
| `FORM` | `10` \| `15` | form with 10 fields (problem statement) or 15 |
| `POSTFIX` | `on` \| `off` | code corrections after the model (all of them) |
| `LISTFIX` | `on` \| `off` | list-field corrections |
| `PEOPLEFIX` | `on` \| `off` | persons / actions corrections |
| `HINGLISH` | `0` \| `1` | Hindi / Hinglish input |
| `STT_LANGUAGE` | `hi` | Whisper language for Hindi turns (`auto` detects) |
| `STT_TRANSLATE` | `1` \| `0` | translate Hindi speech to English |
| `STT_TRIM` | `on` \| `off` | cut silence at both ends of a clip |
| `TTS_SPLIT` | `off` \| `on` | also serve the reply sentence by sentence |
| `ACK` | `on` \| `off` | short spoken "Got it." while the model works |
| `WAKE` | `auto` \| `kws` \| `stt` \| `off` | "Hey Qualcomm" detector (`auto`: keyword spotter if `KWS_URL` is set, else Whisper) |

### Speech services (read by the app; printed by the voice scripts)
| flag | default | effect |
|---|---|---|
| `STT_URL` / `STT_DEVICE` / `STT_MODEL` | `http://127.0.0.1:8082` / `laptop` / `ggml-base.en.bin` | speech-to-text server and its labels |
| `TTS_URL` / `TTS_DEVICE` / `TTS_MODEL` | `http://127.0.0.1:8083` / `unknown` / `unknown` | text-to-speech server and its labels |
| `KWS_URL` / `KWS_DEVICE` | unset / `unknown` | keyword spotter server (port 8085) |
| `STT_TIMEOUT_S` / `TTS_TIMEOUT_S` / `KWS_TIMEOUT_S` | `60` / `60` / `10` | request timeouts |

### Board scripts (`board/up.sh`, `board/voice_up.sh`, `voice/laptop_up.sh`, `python -m board.board`)
| flag | default | effect |
|---|---|---|
| `CTX` | `2048` | llama-server context size |
| `TYPE_MODEL` | unset | `board/up.sh` also starts this model on the NPU (port 8084) |
| `SPEC` | `0` | speculative decoding (`SPEC_N`=3, `SPEC_M`=8) |
| `COOL_CPU` | `0` | fewer CPU threads for llama-server |
| `ANDROID_SERIAL` | first device | which board |
| `ENGINES` | `../../engines` | folder with speech models and binaries |
| `STT_MODEL_FILE` | `ggml-base.en.bin` (`ggml-base.bin` with `HINGLISH=1`) | Whisper model |
| `STT_THREADS` | `4` | Whisper CPU threads on the board |
| `TTS_VOICE` | `vits-piper-en_US-lessac-medium` | Piper voice folder |
| `KWS_MODEL` | `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01` | keyword spotter model folder |
| `HINGLISH` / `STT_LANGUAGE` / `STT_TRANSLATE` | `0` / `auto` / `1` | multilingual Whisper, language, translation |
| `BUILD` | `~/fieldmind-build` | llama.cpp checkout for `build_candidate_gguf.sh` |

### Evaluation (`eval/run_eval.py`, `benchmarks/run_bench.py`)
| flag | example | effect |
|---|---|---|
| `EVAL_HEAT_PAUSE` | `90:80` | pause at 90 °C NPU until 80 °C |
| `EVAL_PACE` | `4` | seconds before each model call |

---

## 6. Tests and evaluation

```
.venv/bin/python -m pytest -q                                              # unit and integration tests
.venv/bin/python eval/run_eval.py --label ORACLE --backend oracle          # scorer self-check (must be 100%)

# On the board (models up as in section 4):
EVAL_HEAT_PAUSE=90:80 TYPE_MODEL=gemma-3-4b-it-Q4_0-pure-embq8.gguf \
    .venv/bin/python benchmarks/run_bench.py --tag demo --runs 1           # text eval with cool-down, saved to benchmarks/
.venv/bin/python eval/wer.py --label BOARD --subset                        # speech-to-text accuracy and time
.venv/bin/python eval/turn_time.py --label BOARD --audio <recordings>      # whole spoken turn, stage by stage
.venv/bin/python eval/wake_eval.py --label BOARD --pos <wake clips> --neg <speech clips>
.venv/bin/python -m board.parallel_probe --cpu                            # two models at once on the board
```
Other eval sets: `--cases eval/nlu15/cases.jsonl`, `--cases eval/hinglish/cases.jsonl` (with `HINGLISH=1`).

---

## 7. License

MIT, see [LICENSE](LICENSE). Model weights keep their own licenses (Llama 3.2 Community License, Gemma Terms of
Use, Whisper MIT, Piper voice per its model card).
