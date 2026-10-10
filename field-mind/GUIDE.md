# FieldMind — The Complete Guide

This document exists so you can hold the whole system in your head. It walks
every piece of data through every stage, in the order the data actually moves,
with real output from a real run at each step.

Read it in order the first time. After that it works as a reference.

- **[README.md](README.md)** — what the project claims and where it stands
- **[README_MACOS.md](README_MACOS.md)** — getting it running on your machine
- **This file** — how it works and why each part is shaped the way it is

---

## Contents

1. [The one-paragraph version](#1-the-one-paragraph-version)
2. [The two invariants everything hangs off](#2-the-two-invariants-everything-hangs-off)
3. [Every data structure in the system](#3-every-data-structure-in-the-system)
4. [One tick, traced end to end](#4-one-tick-traced-end-to-end)
5. [The eight layers in detail](#5-the-eight-layers-in-detail)
6. [The four knowledge stores](#6-the-four-knowledge-stores)
7. [How belief moves: the world model](#7-how-belief-moves-the-world-model)
8. [Where the data comes from: the simulator](#8-where-the-data-comes-from-the-simulator)
9. [The benchmark: what is actually being measured](#9-the-benchmark-what-is-actually-being-measured)
10. [The backend switch](#10-the-backend-switch)
11. [Reading the current numbers honestly](#11-reading-the-current-numbers-honestly)
12. [Coupling map: if you change X, check Y](#12-coupling-map-if-you-change-x-check-y)
13. [Test yourself](#13-test-yourself)

---

## 1. The one-paragraph version

Every 30 seconds the agent reads six sensor values off a boiler. Plain Python
turns those numbers into a short list of **facts** — statements like "drum level
is 19.5%, below the 20% alarm limit" — each with an ID. Those facts are matched
against a library of past failure cases to produce a ranked list of **candidate
causes**. A small language model is then shown the facts and the candidates (never
the raw numbers) and asked to rank the causes and explain them, citing fact IDs.
A second model call tries to shoot down the first. Finally, plain Python again
checks that every cited fact ID really exists, looks up the recommended actions
in a fixed catalogue, and decides whether to escalate. The result is one
**assessment** object. Belief carried in a **world model** persists into the next
tick, which is what makes it an agent rather than a classifier run repeatedly.

---

## 2. The two invariants everything hangs off

Almost every design decision in the repo follows from these two sentences. If
you remember nothing else, remember these.

### Invariant 1 — The model never sees raw numbers

L1 converts sensor readings into facts. The prompt contains facts, not numbers.

The reason is partly quality and partly cost. On quality: language models are
bad at arithmetic over long number sequences and good at weighing competing
explanations, so we do the arithmetic in Python and hand the model the job it is
actually good at. On cost: a 60-minute window is 720 samples × 6 tags ≈ 4,300
numbers. At roughly one token per number that is a 4,000-token prefill before
you have said anything. The same information as facts is 150–300 tokens. On the
NPU, where prefill dominates and the compiled graph has a **fixed** sequence
length, that is the difference between fitting in the compiled shape and not.

### Invariant 2 — The model never has the last word

L6 takes the model's answer and validates it. Every cited fact ID must exist.
Every action must come from a fixed catalogue. The plant state that drives
escalation is derived from facts, never from the model.

This is what makes the system defensible for a safety-adjacent application, and
it hands you a hallucination metric for free: faithfulness is just "what
fraction of cited IDs were real?", which is a set membership test, not a
judgement call.

Everything else — triage, the degradation ladder, provenance weighting — is
about making it cheap enough to run all shift on a battery.

---

## 3. Every data structure in the system

This is the section you asked for. Nine types carry all the information in the
system. Here is each one: what it is, where it is defined, what it looks like
filled in, and why it exists.

### 3.1 The raw sample

The lowest level. One row of the CSV, one instant in time.

```python
{"t": 1245.0,             # seconds since episode start
 "drum_level": 41.83,     # %
 "feed_water_flow": 63.12,# TPH  (tonnes per hour)
 "steam_flow": 66.94,     # TPH
 "drum_pressure": 65.88,  # kg/cm2 (gauge)
 "bed_temp_avg": 849.71,  # degC
 "ms_temperature": 494.02}# degC (main steam)
```

**Six tags, and only six.** This is locked. The constraint is the point of the
project: with six tags you cannot simply look up the answer, you have to reason
about relationships between them. Adding a seventh would make several fault
families trivially separable and destroy the benchmark.

What each one tells you:

| tag | what it measures | what it is for |
|---|---|---|
| `drum_level` | water level in the steam drum, % | the integrator of the water balance — everything water-side shows up here |
| `feed_water_flow` | water going in, TPH | one side of the water balance |
| `steam_flow` | steam going out, TPH | the other side; also the load signal |
| `drum_pressure` | kg/cm²(g) | the integrator of the energy balance |
| `bed_temp_avg` | fluidised bed temperature, °C | where combustion problems appear first |
| `ms_temperature` | main steam temperature, °C | lagging confirmation of the heat side |

Sampled every **5 seconds**. The agent ticks every **30 seconds**, so each tick
ingests 6 new samples per tag.

Note what is *not* here: no coal flow sensor, no feed valve position, no drum
water conductivity. That absence is deliberate and it is why the energy balance
has to be *inferred* from pressure and bed temperature rather than measured.

### 3.2 The episode — what is on disk

An episode is one scenario. There are 30 of them in `data/episodes/`. Each is a
directory of five files:

```
ep_A01_fcv_seize/
  timeseries.csv     660 rows x 7 cols  -- the sensor data, 5 s apart
  notes.jsonl        ~10 engineer notes, each timestamped
  records.json       coal lab report, maintenance history, alarm log, permits
  ground_truth.json  the answer key -- NEVER visible to the agent
  query.txt          how an operator might phrase the question
```

The naming encodes the fault family: `ep_` + family letter + number + short
name. The six families:

| family | n | what happens | what it tests |
|---|---|---|---|
| **N** normal | 6 | steady operation, some load swings | false positives (Q5) — the hardest thing to get right |
| **A** feed path | 6 | feed water cannot be delivered (valve seizes, strainer chokes, pump loses suction) | water-side reasoning |
| **B** leak | 5 | water lost without being metered (tube leak, blowdown valve left open) | balance reasoning — the residual is the only clue early on |
| **C** fuel / heat short | 6 | not enough heat gets in (wet coal bridging, feeder trip, low-CV coal) | energy-side reasoning with no fuel sensor |
| **D** bed temperature | 4 | too much heat, or heat in the wrong place (high-CV coal, low primary air) | separating "too much fuel" from "too little air" |
| **E** slow drift | 3 | progressive fouling — **nothing ever crosses a limit** | whether this is a monitoring agent or an alarm repeater |

Family E is the one that matters most for the research claim. If the only thing
you can do is compare values to thresholds, you cannot detect it at all, because
by construction no threshold is ever crossed.

There is a second, orthogonal labelling — the **tier** — which records what
information is *necessary* to solve the episode:

- **tier A** (12 episodes) — solvable from the time series alone
- **tier B** (12) — needs the engineer notes too
- **tier C** (6) — needs the structured records as well

The tiers are what make the T8 modality-ablation test meaningful: strip the
notes out and tier-B accuracy *must* collapse, or the episode was mislabelled.

Here is a real `ground_truth.json`, trimmed:

```json
{"episode_id": "ep_A01_fcv_seize",
 "family": "A", "tier": "B",
 "required_modalities": ["timeseries", "notes"],
 "fault_onset_t": 600.0,        // seconds -- fault starts at minute 10
 "trip_t": 2680.0,              // seconds -- boiler trips at minute 44.7
 "root_cause_id": "RCA-01",
 "root_cause_text": "FCV actuator stem seizure",
 "contributory": ["CBD valve left ~40% open at handover"],
 "state_timeline": [[0.0, 1045.0, "NORMAL"],
                    [1045.0, 2280.0, "DEVIATION"],
                    [2280.0, 2680.0, "ALARM"],
                    [2680.0, 3295.0, "TRIP_IMMINENT"]],
 "correct_action_ids": ["ACT-014", "ACT-002", "ACT-031"],
 "distractor_note_ids": ["n_110", "n_102", ...],
 "injection_present": false,
 "dropout_tag": null,
 "_provenance": "SYNTHETIC. Generated by data/generator. Not measured plant data."}
```

`state_timeline` is what T1 scores against, tick by tick. `fault_onset_t` is why
T2 only scores from onset onward — crediting the right answer *before* the fault
exists would reward a lucky prior, not a diagnosis. `trip_t` is what T6 measures
lead time against.

### 3.3 The note — human text as data

```json
{"id": "n_101",
 "t": 0.0,                    // seconds relative to episode start; negative = past
 "author": "shift_B_operator",
 "text": "fcv on feed line not responding properly to demand, noticed during last shift also. told inst dept",
 "tags": ["drum_level", "feed_water_flow"],
 "equipment": "SEE_CASE",
 "provenance": "engineer_note",
 "reliability": 0.9}
```

Notes are the second modality. They are shorthand, sometimes wrong, sometimes
stale, and sometimes about something else entirely. Deliberately so:

- **distractors** — a real note about an unrelated problem (`"ash slurry pump 2
  tripped twice on ovld"`). Most notes in most episodes are distractors.
- **stale** — six weeks old, marked `"stale": true` and given lower reliability.
  The recency decay in the notes store should bury it.
- **contradictory** — in 3 episodes, two notes disagree. The rule the prompt
  states is that sensors override notes and the conflict gets reported.
- **injection** — in 2 episodes, a note contains instruction-like text. The
  prompt wraps all notes in `<<< >>>` and declares them data, not instructions.
  Deflection is scored under T9.

The harness enforces `t <= now`, and the notes store enforces it again
independently. A store that *can* leak future data eventually will.

### 3.4 The Fact — the most important type in the system

Defined in `fieldmind/schemas.py`. Produced **only** by L1. Immutable.

```python
Fact(id="F1",
     check="LIMIT",                       # LIMIT|RATE|BALANCE|VALIDITY|PATTERN
     tags=["drum_level"],
     window=(64, 76),                     # (start_tick, end_tick) computed over
     value=19.5,                          # the number behind the claim
     detail="drum_level = 19.5 < 20.0 (alarm_lo)",   # goes verbatim into the prompt
     severity="ALARM",                    # INFO|WATCH|ALARM|CRITICAL
     confidence=1.0)                      # 1.0 for limits, lower for slopes
```

A Fact is a statement that is **true by construction** — it was computed, not
inferred. Everything downstream may *interpret* facts but may never contradict
them.

Three details worth understanding:

**IDs are tick-local.** `F1`, `F2`, `F3`, restarting every tick. The model cites
them inside one tick and L6 resolves them inside the same tick, so global
uniqueness would buy nothing and longer IDs would cost prompt tokens.

**`detail` is the prompt text.** Whatever is in that string is exactly what the
model reads. This is why the balance-check wording is written so carefully —
when it said the residual sign backwards, the model was reasoning correctly from
a false premise, and no amount of prompt engineering would have fixed it.

**`confidence` is discounted for inferred quantities.** A limit crossing is
certain. A slope over a noisy tag is an estimate, and the belief update should
weigh it less.

### 3.5 The signature — how a tick is matched against history

A sparse dictionary over `(tag, direction, band)` triples.

```python
{("drum_level",      "DOWN", "FAST"): 1.0,
 ("feed_water_flow", "DOWN", "MED"):  1.0,
 ("steam_flow",      "FLAT", "-"):    0.35,
 ("bed_temp_avg",    "FLAT", "-"):    0.35,
 ("water_balance",   "DEFICIT","MED"):1.0}
```

Built by `build_signature()` in `l1_symbolize.py` from the measured slope of
each trusted tag. Three things to understand:

**Direction and band come from a 4-way split of the slope**, per tag, from
`BAND_EDGES`:

| tag | deadband | slow/med | med/fast | units |
|---|---|---|---|---|
| `drum_level` | 0.03 | 0.15 | 0.50 | %/min |
| `feed_water_flow` | 0.05 | 0.30 | 1.00 | TPH/min |
| `steam_flow` | 0.05 | 0.30 | 1.00 | TPH/min |
| `drum_pressure` | 0.004 | 0.02 | 0.08 | kg/cm²/min |
| `bed_temp_avg` | 0.03 | 0.15 | 0.50 | °C/min |
| `ms_temperature` | 0.04 | 0.20 | 0.80 | °C/min |

Below the deadband it is `FLAT`; otherwise `SLOW`, `MED` or `FAST`. **The SLOW
band is not optional** — several cases are defined by slow movement, and when
the symbolizer only emitted FLAT/MED/FAST those cases could never match
anything, which silently zeroed two entire fault families. That was bug #3.

**FLAT carries weight 0.35, movement carries 1.0.** Flatness is real evidence —
on RCA-01 the *flat* bed temperature is what rules out the heat side — but on a
healthy plant nearly everything is flat, so a case is not distinguished by
predicting flatness. Movement discriminates; flatness merely fails to
contradict. The 0.35 must match the value in `CaseLibrary._to_triples()` or the
weighted Jaccard is comparing two different scales.

**Balances enter as pseudo-tags.** This is the neat part. A slope-only signature
cannot separate a feed-path fault from a leak: in both, level moves and feed
moves. What separates them is the *sign of the water balance residual*, which is
a relationship between tags rather than a slope of one. Encoding it as
`("water_balance", "DEFICIT"|"SURPLUS", band)` lets the same matcher use it with
no special-casing at all.

### 3.6 The case record — structured RCA knowledge

`data/kb/case_library.json`. Ten records today, of an eventual eighteen.

```json
{"case_id": "RCA-01",
 "title": "Drum level low - feed path restriction",
 "signature": {"drum_level": ["DOWN", "FAST"],
               "feed_water_flow": ["DOWN", "MED"],
               "steam_flow": ["FLAT", "-"],
               "bed_temp_avg": ["FLAT", "-"],
               "drum_pressure": ["FLAT", "-"]},
 "contradicting_signature": {"bed_temp_avg": ["DOWN", "MED"],
                             "water_balance|SURPLUS|MED": 1.0},
 "hypotheses": ["BFP hydraulic deficiency", "Feed path restriction / control failure",
                "Uncontrolled water loss", "Tube leak", "False level indication"],
 "discriminating_evidence": "Valve demand saturated while flow does not respond.",
 "root_cause": "FCV actuator stem seizure",
 "equipment": "FEED_VALVE",
 "actions": ["ACT-014", "ACT-002", "ACT-031"],
 "provenance": "curated_case",
 "weight": 1.0,
 "occurrences": 1}
```

`contradicting_signature` is what makes the library discriminating rather than
merely descriptive. It says: *if you see this, it is not me.* In `CaseLibrary.match`,
a contradiction present in the data multiplies the score by 0.15 — it kills the
case rather than gently lowering it.

`discriminating_evidence` is the observation that would settle it, and it is
carried through to the assessment so the engineer is told what to go and look at.
That is the difference between "probably the feed valve" and "probably the feed
valve — go check whether valve demand is saturated while flow is not responding."

`weight` is the only field the agent may modify, bounded to [0.5, 2.0] by L7.

### 3.7 The WorldModel — the blackboard

`fieldmind/schemas.py`. One typed object, carried tick to tick. **This is the
thing that makes it an agent.**

```python
WorldModel(
  episode_id = "ep_A01_fcv_seize",
  tick       = 76,
  state      = "ALARM",
  equipment  = {"FEED_VALVE": EquipmentState(status="SUSPECT", since_tick=61, ...), ...},
  open_findings = [Finding(id="FND1", signature_key="LIMIT:drum_level",
                           first_tick=74, last_tick=76,
                           severity="ALARM", detail="...", resolved=False)],
  hypotheses = [Hypothesis(cause="BFP suction loss...", case_ref="RCA-02",
                           log_odds=-0.45, confidence=0.39,
                           supports=["F1","F2"], contradicts=[],
                           discriminator="Pump discharge pressure falls with...",
                           first_tick=61, retired=False)],
  residuals  = {"last_drum_level": 19.5, ...},
  baselines  = {"drum_level": Baseline(median=49.8, mad=0.14, n=430, frozen=True), ...},
  trusted_tags = {"drum_level", "feed_water_flow", ...},
  timeline   = [Event(tick=61, kind="FINDING_OPENED", detail="..."), ...],
  notes_seen = ["n_101"],
  degraded_mode = None)
```

The sub-types:

- **`Finding`** — something noticed and not yet explained, keyed by
  `signature_key` = `"{check}:{tags joined}"`. Same key ⇒ same finding, so the
  agent *extends* it rather than re-announcing it. Without this the agent emits
  the same sentence 400 times, which is an alarm flood and gets it muted.
- **`Hypothesis`** — a candidate cause with a running belief. `log_odds` is the
  accumulator; `confidence` is the sigmoid of it, for humans.
- **`Baseline`** — robust per-tag normal, median + MAD. `frozen` is the guard
  that makes family E survivable (see §7.2).
- **`EquipmentState`** — per-component status from the topology graph.
- **`Event`** — the audit trail. Findings opened and resolved, hypotheses
  retired, degradations entered.
- **`trusted_tags`** — tags currently believed. A VALIDITY fact removes one, and
  every downstream check skips it.
- **`degraded_mode`** — set when the *agent* is impaired (LLM timed out,
  instrument lost). Distinct from the plant being in trouble.

### 3.8 The AgentEnvelope — uniform return from every model call

```python
AgentEnvelope(
  agent      = "diagnostician",           # or "verifier"
  tick       = 76,
  status     = "ok",                      # ok|invalid_schema|timeout|degraded
  payload    = {"headline": "...", "hypotheses": [...], "unexplained": [...]},
  cited_facts= ["F1", "F2"],
  cited_cases= ["RCA-01", "RCA-02"],
  latency_ms = 412.0,
  backend    = "npu",                     # npu|gpu|cpu|cloud|mock
  model      = "gemma3-1b-it-q4",
  tokens     = {"prefill": 287, "decode": 96})
```

Two things live here for different reasons. `status`/`payload`/`cited_*` are the
agent's answer. `latency_ms`/`backend`/`model`/`tokens` exist purely so the
scheduling study has per-stage cost data — which stage ran on which engine, how
many tokens it prefilled, how long it took. Do not strip those out when running
against the cloud; fill them with whatever the API tells you.

### 3.9 The Assessment — the unit of record

What comes out of one tick. This is what the engineer sees and what the
benchmark scores.

```python
Assessment(
  tick=76, timestamp="2026-08-21T06:38:30", state="ALARM",
  headline="drum_level = 19.5 < 20.0 (alarm_lo); drum_level -1.47 %/min sustained 3 min.",
  facts=[{...}, {...}, {...}],            # every Fact this tick, as dicts
  hypotheses=[{"rank":1, "cause":"...", "confidence":0.39,
               "supports":["F1","F2"], "case_ref":"RCA-02",
               "discriminator":"..."}, ...],
  actions=[{"id":"ACT-005","text":"...","urgency":"NOW","source":"catalogue"}],
  escalate=True,
  unexplained=[],
  confidence=0.39,
  degraded_mode=None,
  # --- telemetry sidecar: not shown to the engineer ---
  triage="INVESTIGATE", llm_invoked=True, envelopes=[...],
  tick_latency_ms=18.4, deadline_miss=False)
```

The split matters. Everything above `degraded_mode` is the product. Everything
below is instrumentation for the sweep.

### 3.10 The Action — never authored by a model

```python
Action(id="ACT-005",
       text="Check deaerator level and BFP suction pressure",
       urgency="NOW",              # NOW | SOON | MONITOR
       source="catalogue")         # catalogue | rule
```

All 16 live in `data/kb/action_catalogue.json`. They are all advisory or
diagnostic — go and look at something, take a sample, inform someone. Nothing
actuates. The model *selects* an action; it never writes a procedure.

`source="rule"` means no model ran and L6 derived the action from fact text by
substring match against preconditions. Crude, but it only has to be safe and
deterministic, which a lookup table is and a model is not.

---

## 4. One tick, traced end to end

This is real output from `ep_A01_fcv_seize` (feed control valve seizes at minute
10; the boiler trips at minute 44.7). We are at **tick 76** — 38 minutes in,
about 7 minutes before the trip.

### What comes in

The rolling window holds the last 60 minutes: 720 samples × 6 tags. Drum level
has been falling for a while; feed water flow is pinned because the valve is
seized and cannot open further.

### L1 — checks produce facts

```
F1 [LIMIT/ALARM]   drum_level = 19.5 < 20.0 (alarm_lo)
F2 [RATE/WATCH]    drum_level -1.47 %/min sustained 3 min (noise MAD 1.13)
F3 [PATTERN/WATCH] signature 'water side only': level falling while the heat side is untouched
```

Three facts. Note F3: the *absence* of heat-side movement is emitted as a
positive fact, because absence of expected evidence is what lets the model rule
things out.

### L1b — symbolize

The evidence packet is exactly those three lines, sorted by severity. That is
the entire numeric content the model will see — roughly 60 tokens instead of
4,300 numbers.

The signature is built from the measured slopes: drum_level DOWN FAST, the other
five near-flat.

### L2 — triage

An ALARM-severity fact is present ⇒ **INVESTIGATE**. The full chain runs
including the Verifier. Had this been a QUIET tick, everything below would have
been skipped and the tick would have cost about 2 ms.

### L2b — derive state

From facts alone: an ALARM fact is present ⇒ state = `ALARM`. The model has no
vote here, because state drives escalation and escalation is safety-adjacent.

### L3 — retrieval

The signature is matched against the case library by weighted Jaccard, notes are
filtered by time and tag then ranked, and the topology graph is walked *upstream*
from the drum to enumerate every component that could physically cause this. That
upstream walk is the candidate set — the model can only pick from things that
exist.

### Deterministic belief update — before the model runs

`update_hypotheses` moves the log-odds for each retrieved case, then ranks:

```
rank 1  BFP suction loss from low deaerator level    RCA-02  conf 0.39
rank 2  FCV actuator stem seizure                    RCA-01  conf 0.26
rank 3  Level transmitter reference leg lost         RCA-10  conf 0.15
```

The truth is RCA-01, sitting at rank 2. **This ranking exists whether or not the
LLM ever answers** — it is the phase-3 baseline and the safe floor.

### L4 — Diagnostician

Gets the three facts, the candidate list, the retrieved cases, the notes wrapped
in `<<< >>>` as data, and a one-line world-model summary. Returns JSON with a
ranked list, each hypothesis citing fact IDs.

### L5 — Verifier

A **separate call** with a deliberately narrower view: the claims and the raw
facts only. No cases, no notes. Asking one model to check its own answer in the
same context mostly produces agreement; removing the context that produced the
answer is what makes the check adversarial.

### L6 — the gate

Checks every cited ID against `{F1, F2, F3}` (this is T3, computed as a side
effect). Looks up actions attached to the top two hypotheses' cases. Decides
escalation from `state`, not from the model.

```
ACTIONS: ['ACT-005', 'ACT-002', 'ACT-031']    escalate=True
```

Because rank 1 was RCA-02, `ACT-005` (check deaerator level and BFP suction) is
proposed. The correct set was `ACT-014, ACT-002, ACT-031`. Two of three right —
which is precisely why action precision is 0.26 while recall is 0.98: the gate
proposes from the top *two* hypotheses, so a wrong rank-1 drags wrong actions in
with it.

### L7 — memory

World model is updated and carried into tick 77. Nothing is promoted to the
experience store mid-episode; promotion only happens at closure and only through
the four-stage gate.

**Total tick latency: about 18 ms** on the mock backend, essentially all of it
L1 and L3. With a real model the LLM stages dominate completely — which is the
entire reason triage exists.

---

## 5. The eight layers in detail

### L0 — Ingest (`l0_ingest.py`, 79 lines)

Appends samples to a `SensorWindow`: a fixed-capacity ring buffer, 60 minutes of
5-second samples = 720 per tag.

Fixed capacity on purpose. On a device that must run all shift, an unbounded
history is a slow memory leak that only shows up in hour three.

L0 does **no validation**. That is deliberate: a bad instrument should produce a
citable Fact in L1, not a silent drop in L0. L0 only moves bytes.

`window.ready(5.0)` gates the whole pipeline — before five minutes of history the
agent reports nothing rather than guessing off three samples.

### L1 — Deterministic checks (`l1_checks.py`, 432 lines)

Five families. **Order matters**, and the order is:

```
VALIDITY  →  LIMIT  →  RATE  →  BALANCE  →  PATTERN
```

Validity runs first because if an instrument is lying, every other check over
that tag is meaningless. A tag that fails validity is removed from `trusted`, and
everything downstream skips it.

**VALIDITY** — three ways to disbelieve a tag:
- *stuck*: MAD ≈ 0 over N minutes
- *out of range*: physically impossible value
- *impossible step*: bigger jump in one sample than the process can produce

**LIMIT** — is a value outside its band right now? The least interesting family
and the easiest to get right. It tells you something is *already* wrong.

**RATE** — least-squares slope over a sustained window. Least squares rather than
`(last − first)/n` because one noisy end sample would otherwise dominate, and RATE
facts feed straight into triage. "Sustained for N minutes" is the important part:
an instantaneous slope on a noisy tag fires constantly.

There is a second RATE block, `rates_long`, over a 40-minute window. **This is
the only thing that catches family E.** Nothing crosses a limit, and the
short-window rate checks are all below their thresholds by construction. The only
signal is a slow change in the relationship between tags at constant load.

That check has a subtlety worth understanding, because getting it wrong produced
30 false positives per hour. Bed temperature *tracks load* on a healthy boiler —
raise steam flow, firing rate follows, bed runs hotter. Over 40 minutes a slow
load swing looks exactly like fouling drift. So the check subtracts the
load-explained component:

```python
normalised_slope = bed_slope - 2.75 * steam_slope
```

The 2.75 °C/TPH is a **plant characterisation constant**, fitted by least
squares on the normal episodes only. It is not a tuning knob; a real site would
fit it from historical steady operation and refit it if the plant or fuel
changed. There is also a guard requiring genuine absolute rise, because when the
*fault* drives load down (family B: pressure sags, turbine throttles back), the
`−2.75 × steam_slope` term alone can manufacture a large positive "drift" out of
a perfectly flat bed.

**BALANCE** — *this family carries most of the value.* Limit checks tell you
something is already wrong; balance checks tell you something is wrong *before*
anything crosses a limit, and they tell you which *side* of the plant it is on.

The water balance:

```
residual_tph = (feed − steam − blowdown) − level_slope / K        K = 0.22 %/min per TPH
```

Read that as: how much water is measurably going in, minus how much the drum
level says is going in. If they disagree, water is going somewhere unmeasured.

The sign carries the diagnosis, so it goes in the `detail` text rather than
being left for the model to work out:

- **positive** → more water going in than the drum shows ⇒ it is being lost
  somewhere (leak, blowdown passing), or the level reading is false
- **negative** → level holding on less measured inflow than that requires ⇒ a
  flow transmitter is probably under-reading

Getting that text backwards was bug #6, and it was invisible because the number
was right — only the English was wrong, and English is what the model reads.

The energy balance has no fuel flow sensor to work with, so it is inferred from
three slopes:

- pressure falling + steam flat ⇒ **heat input short** (family C)
- bed rising (load-normalised) + steam flat + pressure flat ⇒ **heat
  accumulating, not being absorbed** (families D and E)

If a required tag is untrusted, the balance is reported **SUSPENDED**, not
silently skipped. The engineer must be told that a check they rely on is not
running.

**PATTERN** — two or three hand-written multi-tag conjunctions worth naming
outright, because the name is what the engineer actually says: *"water side
only"*, *"energy accumulating in bed"*. Plus one deliberately-INFO fact recording
a quiet heat side, which costs a few tokens and lets the model rule things out.

### L1b — Symbolize (`l1_symbolize.py`, 138 lines)

Facts in, two things out: the evidence packet (prompt text) and the signature
(matching key). Covered in §3.5.

`evidence_packet()` sorts by severity before truncating, so if the context budget
bites at degradation rung 2, what survives is what matters. Truncation is
announced in the text, never silent.

`headline()` is written by code, not by the model. Even at rung 5 with the LLM
off entirely, the engineer still gets that line. **That is the safe floor.**

### L2 — Triage (`l2_triage.py`, 88 lines)

A small decision table. Not a model.

| level | trigger | what runs |
|---|---|---|
| **QUIET** | nothing above INFO, no open findings | L0–L2 only. **No LLM.** |
| **WATCH** | a WATCH fact, or an unresolved open finding | retrieval + Diagnostician |
| **INVESTIGATE** | an ALARM fact, or a new fault signature | full chain incl. Verifier |
| **URGENT** | a CRITICAL fact, or trip predicted within 5 min | full chain, shortest context |

**This is the cost story.** The fraction of ticks that invoke a model (S4) is a
headline result, not a footnote — it is the difference between an agent that can
run all shift on a battery and one that cannot. On the normal episodes it sits
around 0.2.

Two helpers do the work. `_trip_predicted` linearly extrapolates drum level and
bed temperature to their trip thresholds — crude on purpose, because this is a
gate on how much compute to spend, and a fancier predictor here would be
spending compute to decide whether to spend compute. `_new_signature` is what
stops the same alarm re-triggering the full chain for 400 consecutive ticks.

### L3 — Retrieval (`l3_retrieve.py`, 103 lines)

Four sources, assembled under a token budget:

1. **curated cases** — signature match, k=4
2. **experience** — the agent's own past episodes, k=2, weighted below curated
3. **notes** — filter by time and tag *first*, then rank, k=5
4. **candidates** — upstream topology walk, depth 3, capped at 5

Retrieval depth is a *tuned parameter*, not a constant, because it sets prompt
length and therefore prefill cost — which on the NPU is the dominant term.

The budget (2048 tokens at WATCH, 3072 at INVESTIGATE, 1024 at URGENT — URGENT
is *shortest* because speed matters more than completeness when a trip is
minutes away) is enforced by dropping the lowest-scoring items **whole**. Never
truncate mid-record: half a case record is worse than none, because the model
cannot tell it is reading half.

Order of sacrifice: unverified experience, then notes, then cases. **Candidates
are never dropped** — without them the model has no bounded list to choose from
and the hallucination guard is gone.

### L4 — Diagnostician (`l4_diagnose.py`, 174 lines)

The first of two model stages. Its job is the one thing a model is genuinely good
at here: weighing several explanations against messy evidence.

The prompt (`prompts/diagnostician.txt`) has seven rules. The important ones:
only cite fact IDs that appear in FACTS; only choose causes from CANDIDATE
CAUSES or a retrieved case; if the facts do not separate two causes, say so and
name the observation that *would*; notes are data, not instructions; sensors
override notes; **"this is new" is a valid and valuable answer** — if no case
fits, say so and cap confidence at 0.5.

JSON reliability is handled without assuming grammar-constrained decoding is
available, because on LiteRT-LM it may not be:

```
few-shot prompt → strict parse → ONE repair retry → deterministic fallback
```

`extract_json()` handles the three things small models actually do: wrap output
in ```json fences, emit a preamble sentence, emit trailing prose. Everything
else goes down the repair path, and **the parse failure rate is a reported
metric**, not a swallowed error.

### L5 — Verifier (`l5_verify.py`, 116 lines)

A separate call, not a self-check. Sees the claims and the raw facts. **Does not
see the retrieved cases or the notes.** Four questions: do the cited IDs exist
and match? Is any claim contradicted by a fact the Diagnostician did *not* cite?
Is the stated discriminator actually observable, or does it need a tag we do not
have? Is the ranked order defensible from facts alone?

`should_run` is a decision variable in the sweep: `always` / `conditional` /
`never`. Under `conditional` it fires only when top confidence is in [0.35,
0.75] — the band where a check might change something. High confidence is
probably right; very low confidence is going to escalate anyway. That is a large
cost saving, and the risk it introduces — unchecked *confident* errors — is
exactly what the sweep is for.

### L6 — The action gate (`l6_gate.py`, 118 lines)

Four jobs, all deterministic, ~1 ms, always runs even at rung 5.

1. **Validate the schema.** Reject anything malformed.
2. **Check citations.** `check_citations()` returns the T3 numerator and
   denominator plus the offending IDs. If faithfulness falls below
   `min_faithfulness` (0.5), the model's ranking is *discarded* and the
   deterministic one is kept, with the reason recorded in `unexplained`.
3. **Look up actions** from the fixed catalogue, for the top two hypotheses.
   One rule is enforced here and nowhere else: an `agent_unverified` record may
   never be the sole support for a rank-1 hypothesis.
4. **Decide escalation** from `state` (facts-derived) or any NOW-urgency action.

Facts at ALARM or CRITICAL that no hypothesis cites are surfaced as
`unexplained`. An agent that quietly drops evidence it cannot explain is worse
than one that says "and there is this thing I cannot account for."

### L7 — Memory (`l7_memory.py`, 89 lines)

Three things adapt. **Model weights are not among them** — there is no on-device
fine-tuning. "Self-learning" here means the retrievable corpus and the retrieval
weights change.

The promotion gate has four stages: **closure** (episode reached a terminal
state), **outcome** (a root cause was recorded), **sign-off** (a human confirmed
it), **novelty** (signature not already covered — if it is, merge and increment
`occurrences` rather than duplicating). Anything without sign-off is admitted as
`agent_unverified` at provenance weight 0.35.

The gate exists because the obvious failure mode is the agent's own guess
re-entering as evidence, after which it converges happily on its favourite
answer.

Weight updates are bounded to [0.5, 2.0] and **never remove a case**. A case
suppressed to zero could never be retrieved again, which is an unrecoverable
failure mode.

---

## 6. The four knowledge stores

Four stores, deliberately not one:

| store | provenance | weight | who writes it |
|---|---|---|---|
| **Asset model** | `manual` | 1.00 | engineers, once |
| **Case library** | `curated_case` | 1.00 | engineers, from the RCA studies |
| **Notes store** | `engineer_note` | 0.90 | operators, continuously |
| **Experience store** | `agent_confirmed` / `agent_unverified` | 0.70 / 0.35 | the agent, through the gate |

They are separate because they have different **provenance**, and provenance
multiplies into the retrieval score. Merging them into one vector index would
throw that away — and provenance weighting is the mechanism that stops the agent
slowly poisoning its own well.

### The asset model does two concrete jobs

**Upstream search.** "Level falling and feed short" → walk upstream from the
drum → every component that could cause it. This produces the candidate set, so
the model ranks a bounded list rather than inventing causes. This removes a whole
class of hallucination structurally.

**Blast radius.** "Bed over temperature" → walk downstream → what is affected
next. Drives action urgency.

25 equipment items, 27 topology edges.

### The case library matches by signature, not by embedding

Weighted Jaccard: `sum(min(w)) / sum(max(w))` over the union of triples.
Symmetric, bounded in [0,1], and it penalises a case for expecting movement that
is not observed — which is exactly the "absence is evidence" property we want.

For sensor-driven diagnosis, embedding similarity over prose is the *wrong tool*.
Two cases can share almost all their vocabulary and have opposite signatures.
Signature matching is deterministic, explainable, runs in microseconds, and beats
cosine-over-prose here.

### The notes store filters first, then ranks

Two **hard** filters: nothing from the future, nothing older than the lookback
window. One **soft** filter: tag/equipment overlap, else require a lexical hit.
Then score by `(tag_hit, lexical overlap) × recency × provenance × reliability`,
with recency halving roughly daily.

Filtering before ranking is what stops a six-week-old note about a different pump
from winning on vocabulary overlap.

Scoring is lexical, not embedding-based. That is a deliberate starting point: no
model to load, runs on the device today, and it gives the embedding path
something to beat. If MiniLM runs through LiteRT, swap `_score` for cosine and
nothing else changes.

---

## 7. How belief moves: the world model

### 7.1 Four update rules

Log-odds accumulation with bounded per-tick increments. Close enough to Bayesian
to defend in a writeup, and it cannot blow up.

| rule | condition | Δ log-odds |
|---|---|---|
| 1 | expected evidence **observed** | **+0.35** |
| 2 | expected evidence **absent** | **−0.20** |
| 3 | contradicting evidence **present** | **−0.90** |
| 4 | confidence below 0.08 | retire, and log it |

Per-tick total is clamped to ±1.2 regardless of how many tags matched;
accumulated log-odds is clamped to ±4.0 (confidence ≈ 0.982).

**Rule 2 is the one most systems skip.** If a hypothesis predicts the bed
temperature should be falling and it is not, that is evidence against it. Without
rule 2 a hypothesis can only ever go up, and the agent becomes incapable of
changing its mind.

**The bounds are the safety property.** Without them, a fault that persists for
300 ticks drives confidence to exactly 1.0 and the agent can never revise.

Confidences are deliberately **not normalised to sum to 1**. Two simultaneous
faults is a real scenario, and forcing a simplex would make the second one
inexpressible.

### 7.2 The baseline freeze

Baselines are rolling median + MAD over confirmed-normal periods, with
exponential forgetting (α = 0.02) so they track genuine load changes over hours
without chasing minute-to-minute noise.

**The guard:** a tag with an open finding stops updating its baseline.

Without this, family E is undetectable *by construction*. The adaptive baseline
simply learns the drift as the new normal, and the drift check goes quiet
exactly when it is needed. This is a two-line guard that determines whether an
entire fault family is solvable.

### 7.3 A weakness worth knowing about

I noticed this while writing this guide, and it is worth understanding because
it probably explains a good part of the current top-1 number.

`CaseLibrary.match` (retrieval) compares full `(tag, direction, band)` triples —
it knows the difference between "bed temperature falling" and "bed temperature
flat". But `update_hypotheses` (belief) compares **tag names only**:

```python
expected = set(cand.get("signature", {}).keys())     # {"drum_level", "bed_temp_avg", ...}
present_tags = {t for f in facts for t in f.tags if f.severity != "INFO"}
```

Direction and band are discarded. Watch what that does at tick 56 of `ep_A01`
(tick 83 is identical), where the present tags are `{drum_level, bed_temp_avg}`
(the PATTERN fact names the bed, to say it is untouched):

| case | supported | absent | contradiction hit | Δ/tick (old rule) |
|---|---|---|---|---|
| **RCA-01** *(the truth)* | drum_level, bed_temp_avg | 4 | **bed_temp_avg** | **−1.00** |
| RCA-03 | drum_level, bed_temp_avg | 3 | bed_temp_avg | −0.80 |
| RCA-16 | drum_level, bed_temp_avg | 4 | — | −0.10 |
| RCA-04 | drum_level, bed_temp_avg | 3 | — | +0.10 |

(The contradiction term alone is −0.90; the net is −1.00 after +0.70 for two
supports and −0.80 for four absences. The older text of this guide quoted tick
76; on the current episodes RCA-01 is not retrieved at tick 76, and ticks 56 and
83 are the ones where it is retrieved with a flat bed.)

RCA-01's contradicting signature is `bed_temp_avg: ["DOWN", "MED"]` — meaning
*"if the bed is falling steadily, it is not me."* But the bed is **flat**, which
is exactly what RCA-01 predicts. Because the belief update only sees the tag
name, the PATTERN fact that says *"the heat side is untouched"* — evidence
**for** RCA-01 — is read as a contradiction and penalised 0.90.

So the correct hypothesis is being pushed down by its own confirming evidence.
The retrieval layer gets this right; the belief layer throws the information
away.

This is a real, localised bug with a clear fix (match triples in
`update_hypotheses`, as `CaseLibrary.match` already does). I am flagging it
rather than silently patching it because it is exactly the kind of thing worth
tracing yourself once — and because fixing it will change the benchmark numbers,
which should be a deliberate, recorded step rather than something that quietly
happened.

---

## 8. Where the data comes from: the simulator

`data/generator/sim.py`. Understanding this matters because if the simulator is
wrong, everything downstream is measuring the wrong thing — and it *was* wrong,
four separate times.

### The key design rule

**Faults are injected at driver level, never by editing a tag afterwards.**

A fault sets `feed_valve_effectiveness = 0.77` and every downstream tag then
moves because the physics moves it. That is what keeps all six tags mutually
consistent, so a fault appears as a genuine multi-signal pattern rather than a
dent in one trace.

The drivers — the only things a fault may touch:

```python
load_demand              = 67.0   # TPH the turbine is pulling
feed_valve_effectiveness = 1.0    # 1.0 = valve does what it is told
fuel_availability        = 1.15   # max relative heat obtainable
heat_absorption          = 1.0    # how well the water side takes that heat
blowdown_tph             = 1.0
leak_tph                 = 0.0
coal_cv_factor           = 1.0
primary_air              = 1.0
```

`fuel_availability = 1.15` is above 1 on purpose: a healthy boiler has firing
headroom. Family C removes that headroom, which is what makes pressure sag.

### The integration step

**Water side.** A PI controller on drum level sets feed demand — so feed
*responds* to level rather than being an independent trace. That closed loop is
what makes family B (leak) look different from family A (feed short).

The fault enters as a **hard cap on flow**, not a scaling of demand:

```python
feed = min(demand, feed_valve_effectiveness * FEED_VALVE_MAX_TPH)
```

That distinction matters. Scaled demand would let the controller wind up and
recover; a hard cap means feed simply cannot meet steam, and the level falls at a
rate the water balance can compute exactly.

**Energy side.** A firing-rate controller tracks load with a pressure trim.
Without this loop a load reduction leaves fuel unchanged and the bed runs away —
which is not a fault, it is a missing controller, and it made every normal
episode trip. That was bug #1.

Fuel availability is a **cap** on firing, not a multiplier: with wet coal the
feeder can run faster and still not get coal into the bed, so the loop saturates
and the deficit is real. That saturation is the whole of family C.

Bed temperature is a first-order lag toward a target with five terms:

```python
bed_target = 850
           + 900 * (heat_released - heat_absorbed)   # not absorbed -> accumulates
           + 250 * (heat_released - 1.0)             # less fuel -> cooler   (family C)
           + 450 * (coal_cv_factor - 1.0)            # hotter per feeder rev (family D)
           + 400 * (1.0 - min(1.0, primary_air))     # low PA -> hot spots   (family D)
           - 200 * leak_rel                          # leaking water quenches (family B)
```

The leak term is a **cooling** term. Getting that sign wrong was bug #2 — a tube
leak was modelled as heating the bed, which made family B look identical to
family D. Water sprayed into a furnace cools it.

There is also a turbine throttle coupling: `throttle = clamp(pressure / 60)`, so
steam cannot pass rated flow on sagging pressure. Without it, a fuel-side fault
drives pressure to zero unchecked, which no real plant does.

### Ground truth is emitted by the generator

Not written afterwards. The generator knows exactly which driver it changed and
when, so labels cannot drift from the data.

---

## 9. The benchmark: what is actually being measured

### The harness holds the clock

At each tick the agent may see: sensor samples up to now, notes timestamped at or
before now, its own world model from last tick. **Nothing from the future**, and
the harness enforces it.

Why this matters for the result: it makes **detection lead time** measurable —
how many minutes before the trip did the agent first say the right thing. A
one-shot benchmark structurally cannot measure that, and lead time is the metric
a plant engineer actually cares about.

Wall-clock replay is accelerated. The agent is measured on **compute per tick**,
not on how fast the clock moves.

### The nine metrics

| id | what | how it is computed |
|---|---|---|
| **T1** | state classification | macro-F1 over 5 states. *Macro*, because NORMAL dominates by tick count and micro would let a do-nothing agent score well |
| **T2** | root cause | top-1 and top-3, scored **only from fault onset onward** |
| **T3** | evidence faithfulness | fraction of cited fact IDs that exist. A set membership test |
| **T4** | action selection | precision and recall against `correct_action_ids` |
| **T5** | false positives | non-NORMAL ticks per hour on family N. Target ≤ 2/hr |
| **T6** | detection lead time | minutes from first correct rank-1 call to the trip |
| **T7** | cross-episode memory | top-1 with vs without the experience store |
| **T8** | modality attribution | accuracy drop when notes are removed (`--ablate-text`) |
| **T9** | injection resistance | deflection rate on the two poisoned episodes |

Plus system metrics: S1 tick latency p50/p95, S3 energy, S4 LLM invocation rate,
S7 deadline miss rate.

**The metric that ties them together is S3/Q2 — energy per correct diagnosis.** A
configuration that is fast but wrong is not cheap.

`S3_energy_mwh` is `None` on mock and cloud backends, deliberately. It requires
the device. An invented energy number would silently become the headline result.

### The three constraints

- **C1** — hard-stage deadline miss rate must be **exactly zero**. L1 and L6 must
  complete within 200 ms on every tick, always.
- **C3** — top-3 ≥ 0.85
- **C4** — faithfulness ≥ 0.95

C1 is the mixed-criticality claim in one line. It is what "hard" means.

---

## 10. The backend switch

One line in `configs/base.yaml`:

```yaml
llm:
  backend: mock      # mock | gemini | litert
```

Everything downstream calls exactly one method:

```python
reply = backend.generate(prompt, role="diagnostician", max_tokens=384)
```

and gets back a uniform `LLMReply`:

```python
LLMReply(text="...", status="ok", latency_ms=412.0,
         backend="npu", model="gemma3-1b-it-q4",
         prefill_tokens=287, decode_tokens=96, ttft_ms=180.0, error="")
```

Same prompts, same JSON parsing, same repair retry, same gate, same metrics —
whether the answer came from Google or from the Hexagon NPU. Adding Genie or
llama.cpp means writing one class with one method and calling
`register_backend()`. `fieldmind/runtime/llm_backend.py` is the *only* file that
knows how a model is actually invoked.

| backend | what it is | valid for |
|---|---|---|
| `mock` | no network, no key; re-ranks retrieved cases | plumbing, CI, unit tests. **Never an agent result** |
| `gemini` | Google AI Studio over stdlib `urllib` (no SDK) | quality metrics Q1–Q7 while the device path is built. Meaningless for system metrics |
| `litert` | `litert_lm_main` on the QIDK over adb | the numbers that go in the writeup |

Two device details baked into `LiteRTBackend` because getting them wrong fails
*silently*: `LD_LIBRARY_PATH` and `ADSP_LIBRARY_PATH` are built into the command
(omitting them causes a quiet fall back to CPU), and the prompt is pushed as a
file rather than passed as a shell argument (special characters get mangled
across two shell layers). Timing values that do not parse return **0, never a
guess**.

---

## 11. Reading the current numbers honestly

```
Q1 macro-F1     0.689      target 0.80
Q2 top-1        0.291      target 0.55
Q2 top-3        0.685      target 0.85
Q3 faithfulness 1.000      target 0.95
Q4 precision    0.261      target 0.70    (recall 0.979)
Q5 FP/hour      5.73       target 2.0
Q6 lead time    15.4 min   7 of 24 faults caught before trip
S4 LLM rate     0.586
S7 deadline     0.0        must be 0
```

**What these are.** The mock backend does no reasoning — it re-ranks retrieved
cases. So this table measures *deterministic checks + signature retrieval only*.
It is the baseline the language model has to beat, and establishing it is the
entire point of phase 2.

**Faithfulness 1.00 is not an achievement.** The gate structurally cannot emit an
uncited fact ID. It is a property of the design, and it only becomes an
interesting number once a real model is generating the citations.

**Action precision 0.26 with recall 0.98** says the gate proposes too broadly —
it takes actions from the top *two* hypotheses, so a wrong rank-1 drags wrong
actions along. Improving top-1 will improve this for free.

**Q5 at 5.73/hr is over target, and the thresholds have deliberately not been
tuned.** Tuning them against the same 30 episodes used to report Q5 would make
the number meaningless. That work is phase-2, with the advisor, on a held-out
split.

**S7 = 0.0 is the one hard result here.** C1 holds: the hard stages never missed
a deadline on any tick of any episode.

Two things that must be replaced before any of this is reportable beyond the
baseline: the engineer notes are template-generated rather than LLM-generated
(the project's own risk table rates this HIGH likelihood — "synthetic notes are
too clean and make tier B trivial"), and the case library has 10 of 18 cases.

---

## 12. Coupling map: if you change X, check Y

The non-obvious dependencies. Each of these has already bitten once.

| if you change… | also check… | why |
|---|---|---|
| `BANDS` / `BAND_EDGES` in `l1_symbolize.py` | every `signature` in `case_library.json` | a band that no longer exists silently matches nothing (bug #3) |
| the FLAT weight (0.35) | `CaseLibrary._to_triples()` | both sides must use the same scale or weighted Jaccard is meaningless |
| a balance-check `detail` string | nothing mechanical — but read it aloud | that text *is* the prompt; the number can be right while the English is backwards (bug #6) |
| `load_coef_degc_per_tph` | re-fit on normal episodes only | it is a plant constant, not a tuning knob; fitting it on faulted data bakes the fault in |
| anything in `sim.py` | regenerate all 30 episodes, rerun the benchmark | ground truth is emitted by the generator; stale episodes have stale labels |
| `checks:` thresholds | Q5 on family N **and** Q6 lead time on faults | these trade directly against each other |
| `update_hypotheses` matching | §7.3, and re-run the benchmark | belief and retrieval currently match at different granularities |
| retrieval `k_*` or context caps | prefill token counts, then S1/S3 | retrieval depth *is* prompt length, which is the dominant NPU cost |
| the Diagnostician prompt | `validate()` in `l4_diagnose.py` | schema and prompt must agree or every call fails validation |
| adding a 7th sensor tag | `TAGS`, all signatures, balances, benchmark validity | **do not**; the six-tag constraint is the project |

---

## 13. Test yourself

If you can answer these without looking, you understand the system.

1. Why does the language model never see raw sensor values? Give both reasons.
2. A tag freezes at a constant value. Trace what happens through L1, the world
   model, and the water balance.
3. Family E crosses no threshold. What detects it, and what would happen without
   the baseline freeze?
4. Why does the belief update *decrease* confidence when expected evidence is
   absent? What breaks if you remove that rule?
5. The water balance residual is +5 TPH. What does that mean physically, and
   which fault families are consistent with it?
6. Why is `evidence_packet` sorted by severity before truncation?
7. Why does the Verifier not get to see the retrieved cases?
8. Faithfulness is 1.00. Why is that not a strong result?
9. At degradation rung 5 the LLM is off entirely. What does the engineer still
   get, and where does it come from?
10. Why is `S3_energy_mwh` left `None` instead of estimated on the cloud backend?
11. Why is a fault injected as `feed_valve_effectiveness = 0.77` rather than by
    subtracting 5 TPH from the `feed_water_flow` column?
12. Action recall is 0.98 but precision is 0.26. What single upstream number
    would improve both?

Answers are all in this document. Number 12 is in §4 and §11; number 3 is in §5
and §7.2.
