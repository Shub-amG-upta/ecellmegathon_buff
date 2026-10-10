# 04. The single agent (the baseline)

Code: `fieldmind/agent/`. It is the baseline every multi-agent number is compared with, and it is never changed by
the multi-agent work.

## 1. Pipeline per tick (layers L0–L7)

| layer | what it does | code or model |
|---|---|---|
| L0 ingest | reads the sensor samples for this tick into a 60-minute window (it never sees the future) | code |
| L1 checks | turns the window into **facts**: limit breaches, rates of change, water and energy balances, sensor validity, patterns | code |
| L2 triage | decides the urgency level: QUIET, WATCH, INVESTIGATE or URGENT | code |
| L3 retrieve | matches the facts' signature against the 13-case failure library; searches engineer notes and records | code |
| world model / **belief** | keeps a running log-odds score per candidate cause, updated from evidence every tick | code |
| **L4 diagnose** | one model call: rank the candidate causes and cite the facts that support each | **model (NPU)** |
| **L5 verify** | at INVESTIGATE or above, when confidence is middling: a second model call checks the claims | **model (NPU)** |
| L6 gate | checks every cited fact exists; picks actions only from the catalogue; decides escalation | code |
| L7 memory | records the tick in the world model and event log | code |

On a QUIET tick (nothing abnormal) no model is called at all.

## 2. Model calls

| | diagnostician | verifier |
|---|---|---|
| model / lane | Llama 3.2 3B, NPU lane | same |
| prompt | ~1,900 tokens (all facts, up to 4 cases with full text, notes, records, world summary) | ~1,100 tokens |
| answer | free JSON up to **256 tokens** (ranked causes with reasons, citations, discriminators) | free JSON up to 256 tokens |
| answer grammar | none | none |
| merge with belief | the model's order wins; belief supplies confidences | caps a failed claim's confidence |

The calls run **one after another, inside the tick:** the tick waits for both.

## 3. Measured on the board (Llama 3.2 3B, NPU)

Source: `data/episode_metrics.csv` (config `single`). A01 and N01 ran on 7 October (`results/benchmarks/single_6mark`),
B01, C01, D01 and E01 on 5 October (`results/presentation_benchmark/single_agent_llama32-3b`). Same model file,
server flags and agent code.

| episode | model calls | median call | median non-quiet tick | slowest tick | ticks over 30 s | unusable answers | wall time |
|---|---|---|---|---|---|---|---|
| A01 | 147 | 12.9 s | 16.1 s | 47.2 s | 19 of 106 | 14 | 2,113 s |
| B01 | 196 | 13.2 s | 23.3 s | 40.3 s | 33 of 128 | 34 | 2,837 s |
| C01 | 43 | 16.4 s | 16.4 s | 41.3 s | 5 of 43 | 3 | 785 s |
| D01 | 250 | 16.6 s | 17.5 s | 38.2 s | 24 of 223 | 20 | 4,181 s |
| E01, N01 | 0 | – | – | – | 0 | 0 | quiet throughout |

**Cost profile** (all single-agent calls pooled, 834 calls):
- prefill 666–678 tok/s, decode 13.6–14.4 tok/s;
- **82–89% of model time is decoding the answer** (mean answer 159–193 tokens);
- the chip warmed from about 37 °C to about 65–70 °C per long episode.

**Accuracy** (group top-1 = the published top cause is the true case or its look-alike, from fault onset onward):

| episode | single agent | belief alone (code only) | exact case at rank 1 | right group in the top 3 | cited facts that exist |
|---|---|---|---|---|---|
| A01 | 0.557 | 0.547 | 0.17 | 0.81 | 0.76 |
| B01 | 0.656 | 0.555 | 0.66 | 0.91 | 0.84 |
| C01 (held-out case) | 0.674 | 0.977 | n/a | 1.00 | 0.98 |
| D01 | 0.801 | 0.824 | 0.80 | 0.91 | 0.95 |
| **mean** | **0.672** | **0.726** | | | |

## 4. What the baseline taught us

1. **Too slow for the tick.** A median non-quiet tick of 16–23 s, with 81 of 500 ticks over the 30 s budget on these
   four episodes. The model blocks the tick.
2. **Decode-bound.** Answers of ~160–190 tokens at ~14 tok/s are most of the time. Shorter answers are the lever.
3. **Unreliable format.** 71 unusable answers on these four episodes (free JSON broken or cut off at the cap); a
   quarter of citations pointed to facts that don't exist in A01.
4. **Not better than code alone.** On average the model's ranking (0.672) was below belief's (0.726): it helped on
   B01 and hurt on C01.
5. **The NPU is busy 100% of the time** while an episode runs (calls back to back), which is also why it heats up.

These five points are the requirements the multi-agent design was built to meet (`05`).
