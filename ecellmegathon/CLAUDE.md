# ecellmegathon: project instructions

AI Incident Report Assistant for Megathon X (Qualcomm PS1). Read PLAN.md first.

## Non-negotiables (carried over from FieldMind)

- **Every AI model runs on the QIDK.** No cloud API anywhere in the request path, not even as a fallback.
- **No invented numbers.** Every latency or accuracy figure comes from an actual run and is labelled BOARD or
  LAPTOP. Estimates are labelled ESTIMATE. Laptop timings are never reported as board timings.
- **The model never has the last word.** Code decides whether mandatory fields are present, and code checks every
  free-text value against the transcript. Tools come only from `schema/incident_form.json`, and follow-up actions
  only from the catalogue.
- **Grammar on every LLM call.** The model can only emit a valid tool call for the fields allowed in that turn.
- **Keep output short.** Decode dominates NPU time. Don't add fields, prose or whitespace to the model's output
  without measuring the cost.
- The agent core (`app/`) is stdlib-only, so it can move onto the device.

## Ownership

- **Read `BUILD_PLAN.md` first.** It is the shared build spec. Track A (extraction core, server, board) and Track B
  (STT, TTS, `app/voice.py`, `ui/`) own separate files (§5 there). Never edit the other track's files; the contracts
  in its §4 are frozen unless both humans agree.

## Python

- `.venv/bin/python` (tests: `.venv/bin/python -m pytest -q`). Board scripts: `.venv/bin/python -m board.board ...`.

## Board facts (from board/board.py; verify, don't assume)

- llama.cpp `b11371` / `99b95488`, package at `/data/local/tmp/llm/llama.cpp`, GGUFs in `/data/local/tmp/llm/`.
- Every launch: `LD_LIBRARY_PATH` and `ADSP_LIBRARY_PATH` = `/data/local/tmp/llm/llama.cpp/lib`.
- llama-server flags: `-c 2048 -np 1 -fit off --cache-ram 0 -lv 4` (2048: human decision 2026-10-10). NPU: `--device HTP0 -ngl 99`.
- Prove NPU placement from the startup log: `offloaded N/N layers` and a nonzero `HTP0 model buffer size`.
- Only use pure-Q4_0 GGUFs (`board/build_candidate_gguf.sh`, checked by `board/gguf_types.py`). Published Q4_0
  files carry other tensor types; FieldMind standardised on pure Q4_0 + Q8_0 embedding/output.
- Full build recipe (container, preset, pinned commit, model sources and hashes): `docs/reference/phase0b_board_setup.md`.
- After any `adb push`, compare sha256 on the board with the laptop.
