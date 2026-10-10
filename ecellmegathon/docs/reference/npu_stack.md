# 02. The NPU hardware and software stack

This is the core of the deck for a Qualcomm audience. Every fact here is from the project's board logs and reports
(`reports/phase0b_board_setup.md`, `bench/board.py`, the lane startup logs saved per episode as `*.lanes.txt`).

---

## 1. The board

| item | value |
|---|---|
| board | Qualcomm Innovators Development Kit (QIDK), Snapdragon 8 Gen 3 (**SM8650**) |
| accelerator used | **Hexagon NPU**, exposed by llama.cpp as device `HTP0` (Hexagon Tensor Processor, v75 libraries) |
| CPU | 8-core Kryo; the CPU lane uses 6 threads (`-t 6`) |
| memory | 11.1 GiB total (`MemTotal` 11,633,360 kB), shared by everything on the board |
| thermal | 75 thermal zones read over adb; the project logs the hottest CPU zone and the hottest NPU zone (`nsp*` zones) |
| connection | USB to a Fedora laptop; model servers reached through `adb forward` on ports 8080 (NPU lane) and 8081 (CPU lane) |

## 2. The software stack

| layer | what |
|---|---|
| inference engine | **llama.cpp**, pinned to tag `b11371`, commit `99b95488c` |
| NPU backend | llama.cpp's **Hexagon backend** (`ggml-hexagon`); kernels in `libggml-htp-v75.so`, host side in `libggml-hexagon.so` |
| build | Snapdragon toolchain container `ghcr.io/snapdragon-toolchain/arm64-android:v0.7` (Android NDK r29, **Hexagon SDK 6.6.0.0**), preset `arm64-android-snapdragon-release`, server enabled |
| server | `llama-server` (OpenAI-style HTTP API), one persistent process per lane |
| environment on every launch | `LD_LIBRARY_PATH` and `ADSP_LIBRARY_PATH` pointing at the package's `lib/` (without `ADSP_LIBRARY_PATH` the DSP cannot load its code and the server quietly runs on the CPU) |
| model format | GGUF, built by the project from the original 16-bit weights (section 4) |
| client | the agent's own stdlib HTTP client (`LlamaServerBackend`) on the laptop |

The same commit builds the host-side `llama-quantize`, so quantization and inference use identical code.

## 3. The two lanes

A **lane** is one `llama-server` process bound to one compute unit. Each lane handles **one request at a time**
(`-np 1`), so a lane is a queue the agent's scheduler must plan around.

| lane | port | device flags | model in the current multi-agent setup | jobs |
|---|---|---|---|---|
| **NPU lane** | 8080 | `--device HTP0 -ngl 99` (all layers on the NPU) | Llama 3.2 3B Instruct | both diagnosticians |
| **CPU lane** | 8081 | `--device none -ngl 0 -t 6` | Gemma 3 1B (QAT) | verifier (and earlier the note reader) |

Every launch, both lanes, every model:

```
llama-server -m <model.gguf> -c 4096 -np 1 -fit off --cache-ram 0 -lv 4  <device flags>
```

| flag | why (each was a measured or recorded decision) |
|---|---|
| `-c 4096` | one context size for every model and lane. Context size changes NPU speed, so it is never varied per model; 4096 fits the largest single-agent prompt (~2,500 tokens) plus its answer |
| `-np 1` | one slot: one request at a time per lane |
| `-fit off` | the server may not silently change context size or layer offload |
| `--cache-ram 0` | turns off the server's host prompt cache. This fixed a real failure (section 6) |
| `-lv 4` | verbose loader log, needed to prove the NPU placement on every launch. Measured cost: prefill −0.09%, decode +1.37% (within noise), so kept everywhere |
| temperature 0, seed 0 | greedy decoding, for reproducibility (section 7 shows its limits) |

**Safety checks before a model is used:** a lane only loads a file listed in the project's allow-list
(`bench.board.CANDIDATES`), and only if the sha256 of the copy **on the board** matches the recorded value.

---

## 4. Getting a model onto the NPU: the quantization recipe

**Decision (2026-10-03), the same for every model:** every weight matrix **Q4_0**; the token embedding and output
layer **Q8_0**. Built from the original BF16 weights:

```
convert_hf_to_gguf.py  ->  llama-quantize --pure --token-embedding-type q8_0 --output-tensor-type q8_0 <src> <dst> Q4_0
```

**Why build our own files:** a widely used published "Q4_0" GGUF of Llama 3.2 3B was not pure. Its header held
`token_embd.weight` at Q6_K and three `ffn_down` tensors at Q4_1. The project's rule was "only Q4_0 and Q8_0 on the
HTP", so every file was rebuilt to one uniform, reproducible recipe.

| model | file size | tensor types (from the GGUF header, confirmed by the server's own loader) |
|---|---|---|
| Llama 3.2 3B Instruct | 2,012,612,832 B | 196 Q4_0, 1 Q8_0 (token embedding, tied to the output), 58 F32 (norms) |
| Qwen3 1.7B | 1,460,395,904 B | 196 Q4_0, 2 Q8_0, 113 F32 |
| Gemma 3 1B (QAT) | 720,425,280 B | 182 Q4_0, 1 Q8_0, 157 F32 |
| Qwen2.5 0.5B Instruct | 352,154,624 B | 168 Q4_0, 1 Q8_0, 121 F32 |

**Honest caveat to keep:** the rule's original reason was that K-quant tensors fall back to the CPU on the HTP.
Reading the pinned llama.cpp source showed its Hexagon matmul accepts Q4_0, Q4_1, Q8_0, IQ4_NL, MXFP4 and Q2_K–Q6_K
weights. So at this commit the K-quant file might also run fully on the NPU. **That was not tested.** The recipe was
kept because it gives one uniform file per model. Present it as "we standardised on pure Q4_0", not "K-quants don't
run on the NPU".

## 5. Proving the model runs on the NPU

Every launch's startup log is checked by code before the lane is used. The lines are saved per episode in
`*.lanes.txt`, so every benchmark carries its own proof.

| check | Llama 3.2 3B result |
|---|---|
| layers offloaded | **29 of 29** (27 repeating layers, plus the output layer: "offloading output layer to GPU") |
| HTP0 model buffer | **1,911.90 MiB** (nonzero) |
| HTP0 KV-cache buffer / compute buffer | 448.00 MiB / 64.01 MiB |
| CPU model buffer | 399.23 MiB: a CPU copy of the Q8_0 embedding table for the input lookup (128,256 × 3,072 at 34 bytes per 32 values = exactly 399.23 MiB) |
| slots, context | `n_slots = 1`, `n_ctx_slot = 4096` |
| CPU lane | **no** HTP0 buffer line, and 0 layers offloaded |

**Where each operation runs** (one diagnostic launch with the scheduler debug log, 10 graphs):
- **everything on HTP0:** 197 matrix multiplications per graph (196 weight matrices plus the final logits), and
  RMS_NORM, MUL, ROPE, ADD, SWIGLU and FLASH_ATTN;
- **the final logits step on HTP0:** node `result_output [HTP0]`;
- **the only CPU operation:** the input embedding lookup (`GET_ROWS`), once per graph.

This is the scheduler's assignment as printed by the server, not a hardware trace of the DSP.

All four candidate models passed the same check: Llama 3B 29/29, Qwen3 1.7B 29/29, Gemma 1B 27/27, Qwen2.5 0.5B
25/25 layers on HTP0.

## 6. A real failure found and fixed: memory exhaustion from the host prompt cache

- **Symptom.** During the first overnight campaign, model calls stalled for more than 120 s after about 31–37 calls
  and the runs died.
- **Diagnosis.** `llama-server`'s **host prompt cache** (`--cache-ram`, default 8,192 MiB) stored a KV copy of every
  finished prompt, although no prompt was ever reused. On the 3B that cost about 240 MiB per call on an 11.1 GiB
  board.
- **Controlled soak test** (same 50 real prompts, fresh 3B NPU lane each run):

  | | default flags | `--cache-ram 0` |
  |---|---|---|
  | calls completed | 36 of 50; call 37 got no reply in 300 s | **50 of 50** |
  | longest call | 300 s (stall) | 18.5 s |
  | server memory (RSS) | 512 MB → 6,598 MB | 512 → 535 MB |
  | board free memory | 3.5 GB → **0** at the stall | 6.29 → 6.24 GB |
  | decode speed | fell from ~16 to 11 tok/s before the stall | steady 15.8–16.3 tok/s |
  | chip peak (CPU / NPU) | 72.1 / 66.8 °C | 67.4 / 64.8 °C |

- **Fix:** `--cache-ram 0` on every lane, every model. Heat alone did not reproduce the stall; memory did.

## 7. Other measured behaviour worth knowing

- **Speed depends on prompt length.** Llama 3B on the NPU:

  | prompt size | prefill | decode | source |
  |---|---|---|---|
  | 700 tokens | 986 tok/s | 21.0 tok/s | screening, no grammar |
  | ~750 tokens | 809 tok/s | 15.9 tok/s | multi-agent runs, grammar on, warm chip |
  | ~1,900 tokens | 666–678 tok/s | 13.6–14.4 tok/s | single-agent runs |

  Shorter prompts and shorter answers help on both counts. Grammar, temperature and run length also differ between
  these rows, so this is a trend, not a controlled curve (`03` has the table).
- **Answers are decode-bound.** In the single agent, 82–89% of model time was decode (writing the answer), not
  prefill. Cutting answer length is the biggest single speed lever, and it drove the multi-agent design (`05`).
- **Not perfectly deterministic.** At temperature 0 and seed 0, 13–22% of replies differed between two runs of
  identical prompts (e.g. 76 of 97 identical in one re-run). Small run-to-run differences are expected and are not
  evidence of anything.
- **Thermal.** Continuous single-agent inference took the chip from about 37 °C to about 65–70 °C per episode;
  multi-agent episodes ended around 57–65 °C. Between episodes the benchmark tool waits until the chip is back within
  5 °C of its starting reading. Power was **not measured**.
- **Tokenizer check (Gemma).** The project's Gemma GGUF and the Hugging Face tokenizer produced identical token IDs
  on all 2,084 distinct prompts tested; a difference against Google's own older GGUF was traced to one missing
  metadata key in that file.

## 8. Where the agent code runs

The agent's own logic (sensor checks, belief, scheduler, gate) runs in **Python on the laptop**. Only the model
calls go to the board, over USB. This was deliberate for development speed; moving the agent onto the board is
planned, not done. Consequences for the deck:
- **model call times and token rates are real board numbers;**
- **"tick time" and "code path" times are laptop numbers.** The code-only path takes about 2–90 ms per tick on the
  laptop, against a 200 ms budget.
