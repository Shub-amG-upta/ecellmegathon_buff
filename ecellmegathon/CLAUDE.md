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

## Board facts (from field-mind/bench/board.py; verify, don't assume)

- llama.cpp `b11371` / `99b95488`, package at `/data/local/tmp/llm/llama.cpp`, GGUFs in `/data/local/tmp/llm/`.
- Every launch: `LD_LIBRARY_PATH` and `ADSP_LIBRARY_PATH` = `/data/local/tmp/llm/llama.cpp/lib`.
- llama-server flags: `-c 4096 -np 1 -fit off --cache-ram 0 -lv 4`. NPU: `--device HTP0 -ngl 99`.
- Prove NPU placement from the startup log: `offloaded N/N layers` and a nonzero `HTP0 model buffer size`.
- Only use pure-Q4_0 GGUFs (`field-mind/bench/build_candidate_gguf.sh`). Published Q4_0 files silently fall back
  to the CPU.
- After any `adb push`, compare sha256 on the board with the laptop.
