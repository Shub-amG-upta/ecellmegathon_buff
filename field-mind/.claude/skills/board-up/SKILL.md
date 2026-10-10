---
name: board-up
description: Start the two persistent llama-server lanes (NPU and CPU) on the QIDK and forward their ports. Use when asked to run /board-up or before any run that calls the board's model lanes.
---

Bring up both model lanes on the QIDK. Model file to use: $ARGUMENTS (if empty, use `llm.llamaserver.model_file` in
`configs/base.yaml`; if that is null, ask).

Fixed facts (Phase 0b board setup, `reports/phase0b_board_setup.md`). If the board does not match them, stop and report.

- llama.cpp package on the board: `/data/local/tmp/llm/llama.cpp` (`bin/llama-server`, `lib/`). Built from llama.cpp
  tag `b11371`, commit `99b95488cac0f00ce3f05af113a8c1e287753f87`; `llama-server --version` must print `commit 99b95488c`.
- GGUFs: `/data/local/tmp/llm/<name>-Q4_0-pure-embq8.gguf`. Board logs: `/data/local/tmp/llm/logs/lane_<lane>.log`.
- Environment on every launch: `LD_LIBRARY_PATH=/data/local/tmp/llm/llama.cpp/lib` and
  `ADSP_LIBRARY_PATH=/data/local/tmp/llm/llama.cpp/lib`. That folder holds `libggml-hexagon.so` and
  `libggml-htp-v75.so` (the code the DSP loads; without `ADSP_LIBRARY_PATH` the NPU lane cannot start it).
- HUMAN DECISION: always `-c 4096 -np 1`, the same for every model and both lanes. Never change it per model.
- `-fit off` on both lanes (human decision), so the server cannot change the context size or the offload itself.
- `--cache-ram 0` on both lanes, every model (human decision): the host prompt cache otherwise grows by one KV copy
  per call until board memory runs out and a call stalls (soak test, 2026-10-03).
- `-lv 4` on every launch (human rule, measured: it costs under 5%; `bench.board.TIMED_LOG_LEVEL`). At the default log
  level this build prints no offload, buffer or tensor-type lines.
- Only the files in `bench.board.CANDIDATES` may be loaded, and only if `sha256sum` on the board matches. `start`
  refuses anything else, including the old impure file under `/data/local/tmp/geniex/`.
- Laptop side: run every script with `PYTHONPATH` unset (`bench.board` does this itself) and record laptop time only.
  The board's clock is wrong.

The launch commands live in `bench/board.py` (`lane_command`, tested in `tests/test_board_env.py`). Use them; do not
retype the flags.

1. `adb devices` must list exactly one device. Otherwise stop.
2. Check the model file is on the board (`start` verifies its sha256) and that `bench.gguf_types` says every matrix
   is Q4_0 or Q8_0:
   `adb exec-out "head -c 33554432 /data/local/tmp/llm/<model>" > <scratch>/head.gguf`, then
   `.venv/bin/python -m bench.gguf_types <scratch>/head.gguf`.
3. Stop any running lanes: `.venv/bin/python -m bench.board stop` (by process name only; never delete files).
4. Log chip temperature: `.venv/bin/python -m bench.board temp`.
5. NPU lane, port 8080 (`--device HTP0 -ngl 99`): `.venv/bin/python -m bench.board start npu <model>`.
   It forwards the port and waits for `/health`.
6. CPU lane, port 8081 (`--device none -ngl 0 -t 6`; thread count from `llm.llamaserver.cpu_threads`, record it):
   `.venv/bin/python -m bench.board start cpu <model>`.
7. Read both startup logs (`.venv/bin/python -m bench.board log npu` / `log cpu`) and confirm:
   - NPU: `offloading output layer to GPU`, `offloaded N/N layers` with both numbers equal, a nonzero
     `HTP0 model buffer size`, and `n_ctx_slot = 4096`, `n_slots = 1`;
   - the `llama_model_loader: - type ...` counts equal the `bench.gguf_types` counts for the same file;
   - CPU: no `HTP0 model buffer size` line.
8. Send one short identical prompt to each lane (`.venv/bin/python -m bench.board speed http://localhost:8080` and
   `:8081`) and report prompt tokens, prefill tok/s, decode tok/s and time to first token per lane, from the server's
   own timing fields. Report chip temperature again.
9. Report anything that did not match the expected state. Do not try workarounds without asking.
