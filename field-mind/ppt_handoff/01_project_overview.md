# 01. Project overview

## The use case (keep this short in the deck)

- **Plant:** a 67 tonnes-per-hour coal-fired fluidised-bed (AFBC) boiler. The plant data is **simulated** by the
  project's own physics-based generator, compared against a real boiler dataset (known gaps remain, e.g. the
  simulated sensor noise is not yet realistic); the boiler is the use case, not the contribution.
- **Sensors:** six process tags, sampled every 5 s:

  | tag | quantity |
  |---|---|
  | `drum_level` | drum level |
  | `feed_water_flow` | feed water flow |
  | `steam_flow` | steam flow |
  | `drum_pressure` | drum pressure |
  | `bed_temp_avg` | bed temperature |
  | `ms_temperature` | main steam temperature |

  Engineer notes and plant records (coal lab report, maintenance history) arrive as text.
- **Task:** every 30 s (a **tick**), state the plant condition and name the most likely fault from a **library of
  13 real failure reports** (root-cause analyses of real boiler incidents), with checks and actions from a fixed
  catalogue of 16. It is an **advisor**: it never controls anything.
- **Why on-device:** industrial sites are often offline or must keep data on site, and a 30-second cadence needs
  local inference. The system is **designed to need no internet**: models are served on the board and called over
  USB. Offline operation was not separately demonstrated; the laptop's Wi-Fi was on during the board runs.

## The research question (the real contribution)

**How should heterogeneous agentic LLM workloads be scheduled across a phone-class SoC's NPU and CPU under offline,
latency and power constraints?**

| concern | what it means here |
|---|---|
| latency | a fixed 30 s tick: the assessment must be published every tick, and the code-only part (the "hard path") within 200 ms |
| compute | one Hexagon NPU and an 8-core Kryo CPU; each runs one model request at a time |
| power and thermal | sustained inference heats the chip; power was not measured, chip temperature was logged throughout |
| memory | an 11.1 GiB board shared by up to two model servers |

## Two rules the design follows

1. **The model never sees raw numbers.** Code turns sensor windows into short textual **facts** ("drum level falling
   2.7 %/min"), so a prompt carries about 150–300 tokens of evidence instead of thousands of numbers. That keeps
   prompts short enough for fast NPU inference.
2. **The model never has the last word.** Code checks every cited fact and case, picks actions only from the fixed
   catalogue, and decides the plant state. The model ranks causes; code keeps it honest.

## Phases of the project (the deck's arc)

| phase | what was done | where it is described |
|---|---|---|
| 1. Problem and data | boiler simulator, 30 reporting episodes and 36 development episodes, case library, evaluator | this file |
| 2. Models on the board | llama.cpp built for the Hexagon NPU, four models quantized and verified to run fully on the NPU, server settings fixed by measurement | `02`, `03` |
| 3. Model choice | an automatic campaign tested four models on the NPU for speed and answer quality; Llama 3.2 3B chosen | `03` |
| 4. Single agent | one diagnostician (and a verifier) per tick on the NPU; the measured baseline | `04` |
| 5. Multi-agent | specialised small jobs on two lanes (NPU and CPU), blackboard, scheduler, real-time mode | `05` |
| 6. Refinement | measured problems and fixes: answer grammar, merge rules, removing a prompt shortcut, raw notes | `06` |
| 7. Results | single agent against multi-agent versions on the board | `07`, `08` |

## Episodes (the test cases)

An **episode** is a simulated stretch of plant operation (45–200 min) containing one fault, or none.

| family | fault type | examples |
|---|---|---|
| A | water side: feed control | A01 feed valve seizure, A02 fast version, A03 feed-pump suction |
| B | water side: tube leak | B01 tube leak, B02 fast, B03 slow |
| C | heat side: fuel supply | C01 wet coal, C02 feeder trip, C03 mild wet coal |
| D | heat side: combustion | D01 high-heating-value coal |
| E | slow drift (fouling) | E01; no library case, not scored for cause |
| N | normal operation, no fault | N01; scored for false alarms |

There are **two episode sets**, the same 24 fault scenarios simulated with different random seeds:
- the **reporting set** (`ep_*`, 30 episodes): every board benchmark in this pack used it;
- the **dev set** (`dev_*`, 36 episodes): meant for tuning decisions.

See `07_benchmark_data_guide.md` for why that matters when quoting accuracy.
