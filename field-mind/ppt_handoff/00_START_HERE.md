# FieldMind presentation pack: start here

**Who this is for:** a Claude chat session that will help build a PowerPoint deck about the FieldMind project, slide
by slide, with the project member. Read this file first, then the others in order. Benchmark files will be uploaded
too; `07_benchmark_data_guide.md` explains how to read them.

**Who the deck is for:** engineers from **Qualcomm**. They care most about the technical specs and how the
**Hexagon NPU** on the Snapdragon board is used: model placement, quantization, measured speeds, scheduling across
NPU and CPU, memory, thermals. The boiler itself is only the use case; keep it to one or two slides.

---

## The project in five sentences

1. FieldMind is an **offline, on-device AI assistant** that watches an industrial boiler (simulated) through six
   sensors and, every 30 seconds, names the most likely fault from a library of 13 real failure reports.
2. It runs on a **Qualcomm QIDK board (Snapdragon 8 Gen 3, SM8650)**: the language models run on the board's
   **Hexagon NPU** (and its CPU) through **llama.cpp's Hexagon backend**, served by two persistent `llama-server`
   processes ("lanes"); the agent code runs on a laptop connected over USB (adb).
3. The research question is **how to schedule several language-model jobs across the NPU and CPU under latency and
   power limits**. The agent is the vehicle; the scheduling study is the contribution.
4. A **single-agent** baseline (one big prompt, one big answer per tick on the NPU) was built and measured first;
   then a **multi-agent** system (small specialised model jobs on two lanes, a shared blackboard, a scheduler)
   was built, refined through several measured iterations, and benchmarked against it.
5. Result: the multi-agent system runs about **10× faster per episode** (no tick over the 30 s budget, against 16% of
   ticks for the single agent), and its latest version (v3) is also **more accurate** than both the single agent and
   the code-only baseline on the episodes measured, with the caveats in `08_key_numbers.md`.

---

## Files in this pack

| file | what it gives you |
|---|---|
| `00_START_HERE.md` | this file: overview, rules for claims, upload list |
| `01_project_overview.md` | the problem, the research question, constraints, the phases of the project |
| `02_npu_hardware_and_software_stack.md` | the board, the Hexagon NPU, the llama.cpp build, how models are put on the NPU and proven to run there, the server settings and why, memory and thermal findings |
| `03_model_selection_and_npu_vs_cpu.md` | the four models tested on the NPU, measured speeds, the model-choice campaign, NPU vs CPU lane speeds |
| `04_single_agent.md` | the single-agent pipeline (L0–L7) and its measured cost and accuracy on the board |
| `05_multi_agent_architecture.md` | the multi-agent design: agents, blackboard, lanes, scheduler, real-time mode, prompts, answer grammar, merge rules |
| `06_refinement_story.md` | the chronological story of problems found and fixed, with the evidence; good for a narrative arc |
| `07_benchmark_data_guide.md` | how to read every uploaded data file, every metric, and the pitfalls |
| `08_key_numbers.md` | verified numbers for slides, each with its source |
| `09_slide_outline.md` | a suggested deck structure for this audience, with charts and data per slide |
| `10_glossary.md` | terms and abbreviations |
| `data/*.csv` | precomputed tables (per-episode metrics, configuration summaries, NPU model screening, lane speeds), generated from the run files by `tools/make_tables.py` |

---

## Rules for anything put on a slide

These are the project's own rules. Please hold every slide to them.

1. **Only measured numbers.** Every number must come from these files or the uploaded data. If a number is needed
   and not available, say so; do not estimate or round into a stronger claim.
2. **Energy was never measured.** Every energy field is empty (`null`). Do not state energy, power or battery
   figures. Chip temperature (°C) and time are measured; they may be shown, labelled as what they are.
3. **"Mock" results are not AI results.** Some development used a mock model on the laptop. No mock number is in
   the uploaded benchmark data, but if one appears (folders `*_mock`), it measures plumbing only.
4. **Tick times are laptop times.** The agent code runs on the laptop; only model calls run on the board. A
   "tick time" mixes laptop compute with board model time. Model call times and token rates are board measurements.
5. **Accuracy claims are on a small set of synthetic episodes,** mostly run once each, and several design choices
   were made after looking at these same episodes. Say "on the episodes measured", not "the system is X% accurate".
   `08_key_numbers.md` lists the caveats.
6. **All plant data is synthetic** (simulated boiler). The failure library comes from real case studies.
7. **The system is advisory only;** it never controls the plant.

---

## What to upload

**Everything needed is in this `ppt_handoff/` folder** (432 KB):

| part | files | what |
|---|---|---|
| explanations | `00`–`10` markdown files | context, architecture, guidelines |
| derived tables | `data/episode_metrics.csv`, `config_summary.csv`, `npu_model_screening.csv`, `lane_speeds_in_runs.csv` | every metric, precomputed from the tick-by-tick run files |
| raw results | `raw_results/` (68 small files) | copies of the benchmark outputs, renamed `<run>__<original name>` |

What `raw_results/` holds:

| prefix | origin in the repo | content |
|---|---|---|
| `multi_v3__` | `results/benchmarks/multi_v3/` | RESULTS.md, test.json, temps.jsonl, one summary.json per episode |
| `multi_v2_6mark__`, `multi_tiebreak_6mark__`, `multi_nudge_6mark__`, `multi_nudge_6.1mark__` | `results/benchmarks/<run>/` | same |
| `single_6mark__` | `results/benchmarks/single_6mark/` | same (single agent, 7 Oct) |
| `single_2026-10-05__` | `results/presentation_benchmark/single_agent_llama32-3b/` | summary.json per episode, temps (single agent, 5 Oct) |
| `npu_screening__<model>.json` | `results/board/A/<model>/screen.summary.json` | NPU screening of each candidate model |
| `model_choice_campaign__model_choice.txt` | `results/board/C/model_choice.txt` | the model-choice verdict table |

**If uploads are limited,** upload in this order:
1. the 11 markdown files;
2. the 4 CSVs;
3. the 6 `*__RESULTS.md` files;
4. the 4 `npu_screening__*` files;
5. everything else.

The CSVs already contain every number derived from the per-episode summaries.

**Optional reports** for more depth, from the repo's `reports/` folder: `multi_v3_results.md`,
`multi_agent_architecture.md`, `phase0b_board_setup.md`, `multi_llm_impact_design.md`.

**Not included on purpose:**
- the `*.run.json.gz` tick-by-tick files (large; their metrics are in the CSVs);
- `results/benchmarks_mock/` (plumbing checks, never AI results).

To regenerate the CSVs after new runs: `.venv/bin/python ppt_handoff/tools/make_tables.py` from the repo root.
