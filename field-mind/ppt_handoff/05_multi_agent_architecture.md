# 05. The multi-agent architecture

Code: `fieldmind/multi/`. The full description is `reports/multi_agent_architecture.md`; this file is the version for
slides, with the NPU and scheduling angle first.

---

## 1. Design goals (from the single agent's failures, `04`)

| problem in the single agent | design answer |
|---|---|
| the tick waits for the model (16–23 s median, 16% of ticks over budget) | **the tick never waits**: a code-only "hard path" publishes every tick within 200 ms; model work is scheduled as jobs |
| one long, decode-bound call (~1,900 tokens in, ~160–190 out) | **many small jobs:** ~750-token prompts, answers capped at 60 tokens, written as line numbers |
| free-form JSON breaks (14–34 unusable answers per episode) | **a grammar** sent with every request: the server can only produce a valid answer |
| one model on one unit, other compute idle | **two lanes** (NPU and CPU) with **different models**, used at the same time |
| the model's order overwrote a better code ranking | **merge rules** that decide how far a model answer may move the published ranking |

## 2. The pieces

```
           LAPTOP (agent, Python)                                  QIDK BOARD
 ┌───────────────────────────────────────────────┐     ┌──────────────────────────────────────┐
 │ HARD PATH (code, every tick, < 200 ms)        │     │                                      │
 │  sensor → triage → retriever + belief → gate  │     │  NPU lane :8080  (HTP0, Llama 3.2 3B)│
 │                                               │ ──▶ │   water diagnostician                │
 │ SCHEDULER: jobs with priorities and deadlines,│     │   heat diagnostician                 │
 │  one queue per lane, fixed placement          │ ──▶ │  CPU lane :8081  (6 threads, Gemma 1B)│
 │                                               │     │   verifier                           │
 │ BLACKBOARD: shared state, one writer per      │     │   (note reader, retired in v3)       │
 │  section, checked in code                     │     │                                      │
 └───────────────────────────────────────────────┘     └──────────────────────────────────────┘
```

| agent | runs on | job |
|---|---|---|
| sensor | laptop code | facts from the sensor window |
| triage | laptop code | urgency level; QUIET means no model work at all |
| retriever + belief | laptop code | candidate cases from the library, belief's log-odds ranking |
| **water diagnostician** | **NPU** (Llama 3.2 3B) | ranks the candidate causes using water-side facts (drum level, feed and steam flow) |
| **heat diagnostician** | **NPU** (Llama 3.2 3B) | the same for heat-side facts (bed temperature, drum pressure, steam temperature) |
| verifier | CPU (Gemma 3 1B) | passes or fails each of the top 3 claims; can only lower confidence |
| scheduler | laptop code | queues jobs per lane, priorities P1–P4, deadlines |
| gate + memory | laptop code | checks every answer, applies the merge rule, picks actions, publishes |

## 3. How the NPU and CPU are scheduled

- **One request at a time per lane.** Each lane is a single-slot `llama-server`, so the scheduler keeps one queue
  per lane.
- **Fixed placement** (current): both diagnosticians on the NPU; verifier (and the old note reader) on the CPU.
  - The scheduler also implements **earliest predicted finish** placement: predicted finish = when the lane frees +
    prompt / prefill rate + answer cap / decode rate. It was built and tested but not used in the measured runs.
  - Two different models per lane mean a job can't simply move lanes, which constrains that study.
- **Priorities and deadlines:**

  | priority | job | deadline after its tick |
  |---|---|---|
  | P0 | the hard path (code, never queued) | 200 ms |
  | P1 | urgent diagnosis (trip predicted or critical fact) | 10 s |
  | P2 | diagnosis | 30 s |
  | P3 | verification; note reading (P2 when the note concerns an active sensor) | 60 s |

- **Ask only when something changed.** Each side has an evidence fingerprint: its signature items, open findings,
  the cases it is shown and its notes. A side is asked again **only when its fingerprint changes**; otherwise its
  last checked answer is reused. This halved diagnosis calls in development (2,005 → 1,049 on the dev set).
- **Split by plant side.** Two shorter, focused prompts instead of one long one. When both sides change on the same
  tick they queue on the NPU, one after the other (the demonstration setting; the original plan put the heat side
  on the CPU).

### Two execution modes

| mode | how a tick runs | used for |
|---|---|---|
| **lockstep** | the tick waits for its own model jobs, then the next tick starts at once (back to back) | all accuracy benchmarks; comparable with the single agent, which works the same way |
| **real time** | ticks fire every 30 s on the wall clock and never wait. One worker thread per lane runs its queue; **both lanes work at the same time**; answers are folded in at the next tick | the live demonstration |

In real time, a newer job for the same side replaces one still waiting. An answer more than one tick old is dropped
as stale, unless its side's evidence is unchanged since it was asked.

## 4. Keeping model work small and NPU-friendly

| technique | effect (measured unless marked) |
|---|---|
| compact prompts with numbered lines (facts ≤ 8, cases ≤ 4, notes and records ≤ 4 each) | median diagnosis prompt 749 tokens (single agent: 1,931) |
| answers as line numbers, capped at 60 tokens | mean answer 34 tokens (single agent: 159) |
| **answer grammar (GBNF)** built per request from the lines shown; its longest possible answer fits the cap | 0 unusable answers in all multi-agent runs (single agent: 71 on four episodes) |
| all of it together | median diagnosis call **3.1 s** (single agent 13.8 s) |
| QUIET ticks skip the model; unchanged sides reuse answers | the NPU works ~1.5–1.7 s per non-quiet tick on average, i.e. idle most of each 30 s tick |

**Headroom:** with model work averaging under 2 s per 30 s tick, most of the tick's NPU time is unused. That is the
main opportunity identified for future work (`reports/multi_llm_impact_design.md`): spend the idle NPU time on more
targeted questions when the code-only ranking is unsure.

## 5. Safety and correctness rules built into the architecture

- **Blackboard, one writer per section, enforced in code.** Writing a section you don't own raises an error, and
  model jobs on worker threads are refused any write. A model answer reaches the published result only through the
  gate.
- **Fact IDs carry their tick** (`t84.F1`), so a late answer can't cite a fact from the wrong moment.
- **The gate checks every answer:** citations must exist in the answer's own tick; answers are expanded from line
  numbers through the prompt's own line map; actions come only from the catalogue.
- **The plant state and alarms are always computed by code,** so a model failure can never hide an alarm.

## 6. Merge rules: how a model answer reaches the published ranking

`multi.merge_rule`. None of them writes into belief.

| rule | behaviour | runs |
|---|---|---|
| `model` | the model's order wins; belief supplies confidences (same as the single agent) | `multi_v2_6mark` |
| `tiebreak` | belief's order; the model may only reorder causes within 0.35 log-odds of belief's leader | `multi_tiebreak_6mark` |
| `nudge` | each fresh answer boosts the model's top cause by +0.35 and its second by +0.175 (log-odds), capped at 1.2 per cause per episode | `multi_nudge_6mark`, `multi_nudge_6.1mark` |
| **`hybrid`** | belief **weak** (best log-odds < 0) or **tied** (top two within 0.35): the model's ranking of belief's candidate causes leads. Otherwise nudge. Plus a **guard:** the model may never lead with, or boost, a "filler" case | `multi_v3` |

**Filler cases** are three library cases whose signature is flat on every sensor (faults the six sensors can't see).
They fit almost any quiet moment, and were the model's most common wrong first choice.

## 7. The configurations benchmarked

| run folder | prompt version | merge rule | notes | models (NPU / CPU) |
|---|---|---|---|---|
| `multi_v2_6mark` | v2: group letters on case lines | model | Gemma note reader | Llama 3B / Gemma 1B |
| `multi_tiebreak_6mark` | v2 | tiebreak | Gemma note reader | same |
| `multi_nudge_6mark`, `multi_nudge_6.1mark` | v2 | nudge | Gemma note reader | same |
| **`multi_v3`** | **v3: no group letters** | **hybrid** | **raw note text, no reader** | same |

The v2 → v3 prompt change and why it mattered are in `06`.
