# How to compare FieldMind's single-agent and multi-agent benchmark results

*For a Claude chat that receives this file together with benchmark result files from the QIDK laptop, and is then
asked for an analysis and a slide deck. Read all of it before opening the data. Written 5 October 2026.*

---

## 1. What was measured

**FieldMind** watches one industrial boiler (a 67 TPH coal-fired fluidised-bed boiler) from 6 sensors, every 30-second
**tick**, on a Qualcomm QIDK board (Snapdragon 8 Gen 3: CPU, GPU, NPU), with no internet. It names the likely cause of a
fault from a library of 13 real failure reports and suggests checks. It only advises. **All plant data is
simulated** ("episodes": generated time series with a known true fault).

Plain code turns sensor readings into **facts** (ids `F1`, `F2`, ...), decides how urgent the tick is (**triage**:
QUIET, WATCH, INVESTIGATE, URGENT), retrieves look-alike library cases and keeps a code-only **belief** score per
cause. A language model is called only at WATCH or above, to rank the causes and cite facts; a **gate** in code
checks every answer and picks actions from a fixed list.

Two systems are compared:

| | **single agent** (the baseline) | **multi-agent** (the new system) |
|---|---|---|
| model calls | one **diagnostician** call (~1,900-token prompt, answer up to 256 tokens), then a **verifier** call (~1,500 / 256), one after the other | a **water-side** and a **heat-side** diagnostician (~750 / up to 60 each), a **verifier** (~430 / 30) and a **text reader** for engineer notes (~400 / 50) |
| models and processors | Llama 3.2 3B on the NPU | Llama 3.2 3B on the NPU (both diagnosticians); Gemma 3 1B on the CPU (verifier, text reader) |
| timing mode | usually **lockstep**: each tick waits for its model calls | usually **real time**: ticks every 30 s of wall clock; model calls run in the background and their answers improve later ticks; the tick never waits |
| when the model is called | every tick at WATCH or above | only when a plant side's evidence changed; otherwise the last checked answer is re-used |

The multi-agent design's goals are shorter prompts and answers (faster calls), using the NPU and CPU at the same time,
and never delaying the plain-code assessment. The research question behind the project is how to schedule model calls
across processors; the headline metric planned for later is energy per correct diagnosis (no energy numbers exist yet).

---

## 2. The files you will receive

Check the `backend` field first: `llamaserver` means a real model on the board; `mock` means a fake model that does
no reasoning (its numbers measure only the code layers and **must never be presented as model results**).

### 2.1 `summary_<backend>_<tag>.json` (or `single_v3_real_<model>_npu_summary.json` from the board campaign)
One per run. Top level: `config_backend`, `summary` (all episodes rolled up), `per_episode` (one entry per episode).

Key fields of `summary`:

| key | meaning | better is |
|---|---|---|
| `Q2_library.group_top1` | **headline accuracy**: share of scored ticks (after fault onset) where the rank-1 cause is in the true cause's look-alike group | higher |
| `Q2_library.top1` / `top3` | exact true case at rank 1 / in the top 3 | higher |
| `Q2_library.sep_named` | the right group AND the separating check named | higher |
| `Q2_library.belief_group`, `belief_top1_tiefair` | the same, for the **code-only belief** ranking (no model) | reference |
| `Q2_heldout.*` | the same for faults whose case is NOT in the library (the model cannot name them; tests honest uncertainty) | see 4.6 |
| `Q2_by_family` | group top-1 per fault family (A water-side control, B pressure parts / tube leak, C coal / fuel, D air / calorific value, E slow fouling) | higher |
| `Q1_macro_f1` | plant-state classification (NORMAL / DEVIATION / ALARM / TRIP_IMMINENT) | higher |
| `Q3_faithfulness` | share of the model's cited fact ids that really exist | 1.0 |
| `Q3_rel` | share of the model's citations for a case that are facts the code also links to that case | higher |
| `Q4_action_precision` / `recall` | recommended actions against the correct ones | higher |
| `Q5_fp_per_hour` | false alarms per hour on normal stretches | lower |
| `Q6_lead_time_min_mean` | minutes between first correct rank-1 and the trip | higher |
| `S4_llm_invocation_rate` | share of ticks with any model call | lower is cheaper |
| `S7_deadline_miss_rate` | ticks whose plain-code path missed its 200 ms deadline | 0 |

`per_episode[i]` has the same per episode: `T2_root_cause` (with `target`, `true_group`, `n_scored`, `group_top1`,
`top1`, ...), `T3_faithfulness`, `T4_actions`, `T5_false_positives`, `T6_lead_time`, `S1_tick_latency_ms_p50` / `p95`,
`parse_failure_rate` (answers that were not valid JSON), `verifier_disagreement_rate`, `S3_energy_mwh` (null: not
measured; **never estimate it**).

### 2.2 `runs_<backend>_<tag>.json` (the board campaign writes `runs_<column>.json`)
A list, one entry per episode, every tick kept. Per episode: `episode_id`, `backend`, `n_ticks`, `diag_calls`,
`ver_calls`, `text_calls`, `parse_failure_rate`, `mean_prompt_tokens`, `mode` (`realtime` or absent = lockstep),
`ground_truth` (`root_cause_id`, `fault_onset_t`, `trip_t`, `state_timeline`), `assessments`, and for the multi-agent
system `multi`.

Per tick (`assessments[j]`): `tick`, `state`, `triage`, `headline`, `facts`, `hypotheses` (ranked; `case_ref`,
`cause`, `confidence_shown`, `supports`), `actions`, `escalate`, `llm_invoked`, `tick_latency_ms`, `belief_ranking`,
and `envelopes` (one per model call made for this tick): `agent`, `status`, `latency_ms`, `model`, `prompt_tokens`,
and `calls` (one per backend call: `prefill` and `decode` token counts, `prefill_ms`, `decode_ms`, `latency_ms`, all
from the server's own timers).

Multi-agent only, per tick `multi`: `p0_ms` (time the tick thread took, never waiting for a model), `submitted` (jobs
sent this tick: agent, side, lane, time), `results` (answers that arrived this tick: `agent`, `lane`, `queue_wait_ms`,
`start_s`, `finish_s`, `stale`), `compact` (per answer: side, cases and facts shown, the answer; a `merge` record with
the side order and which answers were re-used).

Multi-agent only, per episode `multi.realtime`: `calls` per lane, `busy_s` per lane, `both_lanes_busy_s`, `spans` (one
per model call: agent, lane, `submit_s`, `start_s`, `end_s` in seconds of wall clock), `tick_lag_s_max`,
`idle_at_end`, `workers_alive`; and `multi.stale_dropped` (answers dropped because they arrived too late),
`multi.accepted_late_unchanged_evidence`.

### 2.3 `bench/realtime_summary.py` output (may be pasted as text)
Per episode: calls per agent and lane, slowest call and slowest submit-to-answer time per agent, calls slower than a
tick, both-lanes-busy seconds, stale drops, worst tick time, parse failure rate, group top-1.

### 2.4 Per-call logs from the campaign (`*.calls.jsonl.gz`), board temperatures, `STATUS.md`
Optional. One JSON line per model call with server timings; chip temperature before and after episodes.

---

## 3. How to compare: the order of work

1. **Inventory.** List every file: backend, mode, model(s), episodes, number of ticks. If a file is `mock`, say so and
   do not use it as a model result.
2. **Match episodes.** Compare only episodes present in both runs. Report anything unmatched.
3. **Sanity check with belief.** The code-only belief layer is identical in both systems by design. For matched
   episodes, `T2_root_cause.belief_group` and `belief_top1_tiefair` should be equal (lockstep) or very close (real time,
   same code). If they differ a lot, the two runs did not see the same data: stop and report.
4. **Accuracy**, per episode and pooled (section 4.1).
5. **Speed and work** (section 4.2).
6. **Faithfulness and safety** (sections 4.3 and 4.4).
7. **Caveats** you must state (section 5).
8. Only then the deck (section 6).

Recompute any number you show from the files; do not copy numbers from this guide into the results. The numbers in
this guide are for orientation only.

---

## 4. Interpreting each comparison

### 4.1 Accuracy
- Headline: `Q2_library.group_top1`, single vs multi, plus `top1`, `top3`, `sep_named`, and per episode.
- **Decision bands agreed by the team before any real-model run:** a group top-1 difference of 0.05 or less is a tie
  (no difference); a `Q3_rel` difference of 0.02 or less is a tie. The multi-agent system "keeps accuracy" if it is not
  more than 0.05 below the single agent on group top-1, not more than 0.02 below on `Q3_rel`, and above the code-only
  floor.
- **The code-only floor:** the belief ranking's group score on the same episodes (`belief_group`). A model that scores
  below it adds nothing over plain code; say so if it happens.
- **Real time changes what accuracy means.** In real time the tick does not wait: on the first ticks after a change the
  published ranking is belief's, and the model's ranking appears once its answer arrives (seconds later, often the
  next tick). Per-tick accuracy in real time therefore includes this delay. When comparing a lockstep single agent with
  a real-time multi-agent run, say that the multi-agent number includes answer latency and the single-agent number
  does not.
- **Family E (slow fouling)** never leaves QUIET and calls no model; its ground truth is NORMAL throughout and it has no
  library case. It does not count for accuracy (`applicable` false). Do not present it as a success or a failure of
  either system.
- **Held-out cases** (`Q2_heldout`): the true cause is not in the library, so naming it is impossible; what matters is
  whether the system stays uncertain rather than confidently wrong. Report these separately, never pooled.

### 4.2 Speed and work
Use the right definition for each system; they are not the same thing:

| question | single agent (lockstep) | multi-agent (real time) |
|---|---|---|
| how long until an assessment is published | `tick_latency_ms` (includes waiting for the model) | `multi.p0_ms` (plain code only; the tick never waits) |
| how long until the model's ranking is in | the same `tick_latency_ms` | per answer: `finish_s - submit_s` from `multi.realtime.spans` (queue wait + call) |
| time per model call | `envelopes[].latency_ms` (and per backend call `calls[].latency_ms`) | `spans`: `end_s - start_s`; also `envelopes[].calls[]` |
| model calls per tick | (`diag_calls` + `ver_calls`) / ticks at WATCH or above | the same, plus `text_calls`; and the share of model ticks with no diagnosis call (answers re-used) |
| tokens per call | `prompt_tokens`, `calls[].prefill` / `decode` | the same, per agent |
| processor use | NPU only | `busy_s` per lane, `both_lanes_busy_s` (seconds both processors worked at once) |
| reading / writing speed | prefill tok/s = `prefill / (prefill_ms/1000)`, decode tok/s = `decode / (decode_ms/1000)`, per lane | the same, per lane and model |

Expect writing (decode) to dominate call time: on the NPU about 16 tokens per second was measured for Llama 3.2 3B in
a smoke test, so an answer capped at 60 tokens is much faster than one allowed 256. Compare the distributions (median,
p95, max), not only means. The Gemma 1B CPU lane's speed had never been measured before this run: report it plainly.

### 4.3 Faithfulness and answer quality
- `Q3_faithfulness` should be 1.0; anything lower means the model cited facts that do not exist. A citation like
  `t83.F2` names a fact of an earlier tick (real time) and is checked against that tick.
- `Q3_rel`: how often the model's citations are the facts the code also links to that cause.
- `parse_failure_rate`: share of answers that were not valid JSON (one repair call is made, then the answer is
  dropped). Small models fail here; compare the two systems and per agent.
- `verifier_disagreement_rate`: how often the verifier rejected a claim. In the multi-agent system a verifier that
  disagrees only lowers a claim's confidence; it never reorders the ranking.

### 4.4 Safety and operations
- `S7_deadline_miss_rate` and `multi.p0_ms`: the plain-code path must stay under 200 ms on every tick.
- `multi.stale_dropped`: answers dropped because they arrived too late (more than one tick old with changed evidence).
  Many stale drops mean a lane is too slow for the tick.
- `workers_alive` must be all true; `idle_at_end` should be true.
- `Q5_fp_per_hour`, `Q4_action_precision` / `recall`, `escalate`: false alarms and recommended actions.

### 4.5 Where the multi-agent design should help, and where it may not
- It should: shorter calls, the plain-code assessment always on time, fewer model calls (re-use on steady evidence),
  verifier and note reading moved off the NPU.
- It may not: accuracy could drop because each prompt is shorter (fewer cases and notes shown, notes summarised by the
  1B text reader), because two models are used, or because in real time answers arrive later. On this dataset faults
  that move both plant sides at once are rare (about 1% of ticks in development runs), so the two NPU-side
  diagnosticians rarely both run on one tick.

---

## 5. Caveats that must appear in the analysis and on a slide

- **Sample size.** With a handful of episodes, differences inside the 0.05 / 0.02 bands are ties, and even larger
  differences are not statistically established. Say how many episodes and scored ticks each number rests on.
- **Run-to-run variation.** In an earlier board test, two runs of the same prompts differed on 10 of 37 replies (the
  server settings differed between those runs, so the cause is unknown). Small differences may be noise.
- **Different modes.** Lockstep vs real time measure different things (4.1, 4.2); state which mode each number comes
  from.
- **Different models.** The multi-agent system uses Gemma 3 1B for the verifier and text reader; the single agent uses
  Llama 3.2 3B throughout.
- **Simulated data.** All episodes are synthetic.
- **No energy numbers.** `S3_energy_mwh` is null. Do not estimate energy from time or power figures.
- **Mock results** are never model results.
- **Laptop vs board.** The agent code runs on the laptop; model calls run on the board. Plain-code times (`p0_ms`) are
  laptop times.

If the data contradicts an expectation in this guide, report what the data shows and say that it differs; do not
explain it away without evidence.

---

## 6. Building the deck

Ask what the audience is if the request does not say (team, advisor, demonstration audience). A sensible default
outline (8 to 12 slides):

1. **Title**: FieldMind single agent vs multi-agent on the QIDK, the date, the setup in one line.
2. **The problem in one picture**: one long chain on one processor vs short calls on two processors.
3. **What was run**: models per processor, episodes, ticks, mode, tick length, backend.
4. **Headline accuracy**: group top-1 single vs multi with the 0.05 band shown, and the code-only floor.
5. **Accuracy per episode / family**: a small table or bar chart; note held-out cases separately.
6. **Speed**: time to an assessment, time to a model answer, time per call (median and p95) per agent and processor.
7. **Work split**: calls per tick, calls avoided by re-use, NPU and CPU busy time, both-busy time. A timeline of the
   `spans` for one episode is the clearest picture of the two processors working at once.
8. **Answer quality**: faithfulness, `Q3_rel`, parse failures, verifier disagreements.
9. **Safety**: deadline misses (0 expected), stale drops, false alarms.
10. **Caveats**: section 5, in plain words.
11. **What it means and what is next**: the remaining work is the scheduling study (placement policies, energy per
    correct diagnosis).

Rules for the deck:
- Every number on a slide comes from the files; put the source (file and key) in the speaker notes.
- Label every chart's units and the number of episodes behind it.
- Do not round a tie into a win: if a difference is inside the band, write "no difference".
- Plain language: "the right group of causes at rank 1" before "group top-1"; define each metric once.
- If something could not be computed from the files, say so on the slide rather than filling the gap.

---

## 7. A starting script (Python, standard library)

    import json, statistics as st
    load = lambda p: (lambda d: d if isinstance(d, list) else d.get("runs", d))(json.load(open(p)))

    def per_episode(summary_path):
        s = json.load(open(summary_path))
        return {e["episode_id"]: e for e in s["per_episode"]}

    def call_times(runs):
        """Per agent: model call times in seconds, from the server's own timers."""
        out = {}
        for r in runs:
            for a in r["assessments"]:
                for e in a.get("envelopes", []):
                    for c in e.get("calls", []):
                        if c.get("latency_ms") is not None:
                            out.setdefault(e["agent"], []).append(c["latency_ms"] / 1000)
        return {k: (len(v), round(st.median(v), 2), round(sorted(v)[int(.95 * len(v))], 2), round(max(v), 2))
                for k, v in out.items()}       # n, median, p95, max

    def rates(runs):
        """Prefill and decode tokens per second per agent (server timers)."""
        acc = {}
        for r in runs:
            for a in r["assessments"]:
                for e in a.get("envelopes", []):
                    for c in e.get("calls", []):
                        if c.get("prefill_ms") and c.get("decode_ms"):
                            acc.setdefault(e["agent"], []).append(
                                (c["prefill"] / (c["prefill_ms"] / 1000), c["decode"] / (c["decode_ms"] / 1000)))
        return {k: (round(st.median(x for x, _ in v)), round(st.median(y for _, y in v), 1)) for k, v in acc.items()}

    def answer_latency(runs):
        """Multi-agent real time: submit-to-answer seconds per agent."""
        out = {}
        for r in runs:
            for s in ((r.get("multi") or {}).get("realtime") or {}).get("spans", []):
                out.setdefault(s["agent"], []).append(s["end_s"] - s.get("submit_s", s["start_s"]))
        return {k: (len(v), round(st.median(v), 2), round(max(v), 2)) for k, v in out.items()}

    single, multi = per_episode("<single summary>.json"), per_episode("<multi summary>.json")
    for ep in sorted(set(single) & set(multi)):
        s, m = single[ep]["T2_root_cause"], multi[ep]["T2_root_cause"]
        if s.get("applicable"):
            print(ep, "group top-1", s["group_top1"], "->", m["group_top1"],
                  "| belief (should match)", s["belief_group"], m["belief_group"], "| n", s["n_scored"], m["n_scored"])

Adapt the paths. Fields that are null were not measured; leave them out rather than replacing them with zero.
