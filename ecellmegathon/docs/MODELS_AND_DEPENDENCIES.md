# Models and dependencies

Everything the app needs, on each machine, with the exact file names and paths the scripts expect
(`board/board.py`, `board/up.sh`, `board/voice_up.sh`, `voice/laptop_up.sh`). Sizes marked ~ are approximate.

## 1. Models

### Must have

| stage | model | file | size | where it comes from | runs on |
|---|---|---|---|---|---|
| LLM | **Llama 3.2 3B Instruct**, pure Q4_0 (every matrix Q4_0, embedding + output Q8_0) | `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf` | 2,012,612,832 B | built with `board/build_candidate_gguf.sh` from `unsloth/Llama-3.2-3B-Instruct` @ `006f5dcd13` (BF16 safetensors). **Already built and on our lab QIDK** at `/data/local/tmp/llm/` | QIDK NPU (HTP0) |
| STT | **Whisper base.en** (whisper.cpp ggml) | `ggml-base.en.bin` | ~142 MB | `huggingface.co/ggerganov/whisper.cpp` (or `models/download-ggml-model.sh base.en` in the whisper.cpp repo) | QIDK CPU |
| TTS | **Piper en_US lessac medium** (sherpa-onnx packaging) | folder `vits-piper-en_US-lessac-medium/` holding `en_US-lessac-medium.onnx`, `tokens.txt`, `espeak-ng-data/` | ~65 MB | `github.com/k2-fsa/sherpa-onnx/releases`, tag `tts-models`, `vits-piper-en_US-lessac-medium.tar.bz2` | QIDK CPU |

The LLM file's sha256 must be `5aa3ece50ab33d09a7181888a75f8755f924c662dc99626e7f45440adfeadcdb`.
`board/board.py` refuses to start any file whose sha256 **on the board** differs.

**Don't use a downloaded "Q4_0" GGUF on the board** (e.g. bartowski's). Published Q4_0 files carry Q6_K / Q4_1
tensors. A downloaded one is fine for LAPTOP testing only.

### For comparisons (only if time allows)

| stage | model | file | size | why |
|---|---|---|---|---|
| LLM | Qwen3 1.7B, pure Q4_0 | `Qwen3-1.7B-Q4_0-pure-embq8.gguf` (sha256 `4a4ebf10…09c56c`) | 1,460,395,904 B | 1.5× faster decode than the 3B on the NPU; run with thinking off (the app does it). Built from `Qwen/Qwen3-1.7B` @ `70d244cc86cc`; also on the lab QIDK |
| STT | Whisper tiny.en | `ggml-tiny.en.bin` | ~75 MB | faster, less accurate |
| STT | Whisper small.en | `ggml-small.en.bin` | ~466 MB | more accurate, slower. Pick one with `eval/wer.py` |
| STT | multilingual `ggml-small.bin` | ~466 MB | only if we decide to support Hinglish |

Not needed: Gemma 3 1B and Qwen2.5 0.5B (FieldMind candidates; both are on the board, unused here).

## 2. Software

### On the QIDK (the board)

| what | path on the board | from |
|---|---|---|
| llama.cpp Android package, tag `b11371`, commit `99b95488` (`bin/llama-server`, `lib/libggml-hexagon.so`, `lib/libggml-htp-v75.so`, …) | `/data/local/tmp/llm/llama.cpp/` | **already on the lab QIDK** (134 files). For a fresh board: built on the Fedora laptop, below |
| the GGUF(s) above | `/data/local/tmp/llm/` | `adb push`, then compare sha256 |
| `whisper-server` (arm64-v8a) + its `lib/*.so` | `/data/local/tmp/stt/bin/`, `/data/local/tmp/stt/lib/` | built on the Fedora laptop; `board/voice_up.sh` pushes it |
| `ggml-base.en.bin` | `/data/local/tmp/stt/` | pushed by `board/voice_up.sh` |
| `sherpa-onnx-offline-tts` (arm64-v8a) + its `lib/*.so` | `/data/local/tmp/tts/bin/`, `/data/local/tmp/tts/lib/` | sherpa-onnx Android arm64 build (its repo has `build-android-arm64-v8a.sh`); pushed by `board/voice_up.sh` |
| the Piper voice folder | `/data/local/tmp/tts/vits-piper-en_US-lessac-medium/` | pushed by `board/voice_up.sh` |

The board has no Python. The TTS HTTP wrapper runs on the laptop and calls the board over adb.

### On the laptop that drives the board (Mac or Fedora)

| what | version | used for |
|---|---|---|
| Python | 3.12+ | the app is stdlib-only |
| `.venv` with `pytest` | — | tests (`python3 -m venv .venv && .venv/bin/pip install pytest`) |
| `adb` | Android platform-tools (`brew install --cask android-platform-tools` on Mac) | everything on the board |
| git, curl | any | — |

For laptop-only development (no board), also:

| what | how | used for |
|---|---|---|
| llama.cpp `llama-server` | `brew install llama.cpp` (Mac) | the LLM on the laptop (LAPTOP numbers only) |
| a GGUF for the laptop | the board's pure file (`adb pull /data/local/tmp/llm/Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`), or any Llama 3.2 3B Q4_0 | same |
| whisper.cpp `whisper-server` + `ggml-base.en.bin` | `brew install whisper-cpp`, or a release build | STT on the laptop |
| sherpa-onnx prebuilt for your OS + the Piper voice | release archive from `github.com/k2-fsa/sherpa-onnx/releases` | TTS on the laptop |

Layout `voice/laptop_up.sh` and `board/voice_up.sh` expect (`ENGINES`, default `../../engines` next to the repo):

```
engines/
  whisper/whisper-server              (or whisper/Release/whisper-server)   laptop STT binary
  whisper/ggml-base.en.bin                                                 shared by laptop and board
  sherpa/sherpa-onnx-<version>/bin/sherpa-onnx-offline-tts                 laptop TTS binary
  voices/vits-piper-en_US-lessac-medium/                                   shared by laptop and board
  android/stt/bin/whisper-server       android/stt/lib/*.so                board STT (arm64-v8a)
  android/tts/bin/sherpa-onnx-offline-tts  android/tts/lib/*.so            board TTS (arm64-v8a)
```

### On the Fedora laptop (only to build for the board; not needed if the lab QIDK is already set up)

Qualcomm's QAIRT/QNN host tools don't run on macOS, so board builds happen here.

| what | version | used for |
|---|---|---|
| Docker or Podman + `ghcr.io/snapdragon-toolchain/arm64-android:v0.7` | Android NDK r29, Hexagon SDK 6.6.0.0 inside | building llama.cpp for Android with the Hexagon backend (preset `arm64-android-snapdragon-release`, `-DLLAMA_BUILD_SERVER=ON -DLLAMA_OPENSSL=OFF`) |
| llama.cpp checkout at tag `b11371` / commit `99b95488`, plus a host build of `llama-quantize` from the same commit | pinned | the Android package and the pure-Q4_0 GGUFs |
| Python venv with llama.cpp's `requirements-convert_hf_to_gguf.txt` | — | `convert_hf_to_gguf.py` (safetensors → GGUF) |
| `hf` CLI (`huggingface_hub`) | — | downloading the source weights |
| Android NDK r26+ and CMake | — | building `whisper-server` for arm64-v8a (the command is in `board/voice_up.sh`'s header) |

Full recipe with every hash: `docs/reference/phase0b_board_setup.md`.

## 3. Checks after setting up

1. `board/up.sh`: LLM on the NPU. It must print `OK: placement confirmed` (all layers offloaded, nonzero HTP0 buffer).
2. `board/voice_up.sh` (or `voice/laptop_up.sh` on the laptop): `ok :8082` and `ok :8083`.
3. `.venv/bin/python eval/smoke_llm.py --label BOARD --model Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`:
   the schema is accepted and every reply parses.
4. `.venv/bin/python -m pytest -q`: all tests pass.
