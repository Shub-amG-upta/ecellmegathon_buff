# Presentation benchmark (single agent vs multi-agent, same 6 episodes)

Six reporting episodes, one per family: ep_N01_normal, ep_A01_fcv_seize, ep_B01_tube_leak, ep_C01_wet_coal,
ep_D01_high_cv_coal, ep_E01_fouling_drift. Model: Llama 3.2 3B (`Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`), NPU
lane, `-c 4096 -np 1 -fit off --cache-ram 0 -lv 4`, temperature 0, seed 0, answer cap 256. Agent code runs on the
laptop; only model calls run on the board. Wi-Fi on: not the offline proof. Energy not measured.

**File names (renamed 2026-10-05).** Every result file carries a suffix before its extension:
`_single` in the single-agent folder, `_multi` in the multi-agent folders (`ep_A01_fcv_seize_single.summary.json`,
`ep_A01_fcv_seize_multi.summary.json`, `temps_single.jsonl`, ...), so a file copied out of its folder still says
which agent made it. File contents were not changed: a name written inside a file (for example `timing_report` in
a single-agent summary) is the name the file had when it was written, without the suffix. The names below are the
names at the time of the runs.

`single_agent_llama32-3b/`
- `ep_N01_normal.*`: copied unchanged from the campaign's reporting run (`results/board/D/llama32-3b/`, 2026-10-05
  13:05, campaign runner).
- every other episode: run with `scripts/presentation_run.sh <episode>` = `bench/stage_monitor.py` (live stage
  display) through `LlamaServerBackend`. Same request body and server flags as the campaign. Differences from the
  campaign runner: a call that times out (120 s) or errors is recorded and the agent falls back to its deterministic
  answer (the campaign stops and retries instead); no reply cache; no memory guard; no per-call JSONL.
  `scripts/presentation_all.sh` runs the five in a row (shortest first, skips saved ones), logs chip temperature
  before and after each into `temps.jsonl`, and waits before each until the chip is within 5 C of the reading taken
  at the start of that batch (at most 900 s). That baseline is whatever the board read then, not a measured idle.
  Each episode gets `<ep>.run.json.gz`, `<ep>.summary.json` (evaluation) and `stage_timing_<ep>_llamaserver_*.txt`.

The 3B was chosen by HUMAN OVERRIDE; it did not pass the pre-registered model-choice rule
(`reports/phase0b_board_setup.md`).

`multi_agent_*` folders (multi-agent, `configs/fast.yaml`: Llama 3.2 3B on the NPU lane, Gemma 3 1B on the CPU lane)
- `multi_agent_LOCKSTEP_llama32-3b_gemma3-1b/`: back to back (lockstep), `scripts/benchmark_multi_lockstep_all.sh`,
  BEFORE answer grammars (`multi.grammar` off). A01 and B01 complete; C01 was stopped part-way (tick file only).
- `multi_agent_LOCKSTEP_grammar_llama32-3b_gemma3-1b/`: the same script with answer grammars on.
- `multi_agent_grammar_llama32-3b_gemma3-1b/`: real time (a tick every 30 s), `scripts/presentation_multi_all.sh`,
  answer grammars on. `multi_agent_llama32-3b_gemma3-1b/` held the real-time runs made before grammars (deleted).
