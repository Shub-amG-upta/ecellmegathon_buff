# 09. Suggested slide outline (audience: Qualcomm engineers)

Emphasis: the Snapdragon platform, the Hexagon NPU, measured performance and scheduling. The boiler is context.
About 16 main slides plus backups. Every number referenced is in `08_key_numbers.md` or `data/`.

---

### 1. Title
FieldMind: offline multi-agent LLM inference on the Snapdragon Hexagon NPU. Team, course (Embedded Systems Workshop,
Lab 2, Project 4), advisor.

### 2. Why on-device (one slide on the use case)
- An industrial advisor that names the likely fault every 30 s from six sensors plus engineer notes, for a simulated
  67 TPH coal-fired boiler.
- Offline by design, data stays on site, 30 s cadence.
- **Visual:** sensor → facts → ranked cause → checked advice.

### 3. The research question
How to schedule heterogeneous LLM jobs across NPU and CPU under latency (30 s tick, 200 ms hard path), memory
(11.1 GiB) and thermal limits. The agent is the vehicle; scheduling is the contribution.

### 4. Platform and stack
- QIDK SM8650; llama.cpp Hexagon backend (`HTP0`), Hexagon SDK 6.6.0.0.
- Two persistent `llama-server` lanes: NPU on :8080, CPU on :8081.
- The agent on a laptop over adb.
- **Visual:** the architecture diagram in `05` §2. Source: `02`.

### 5. Putting models on the NPU, and proving it
- Pure Q4_0 + Q8_0 embedding recipe (a published "Q4_0" file wasn't pure).
- 29/29 layers and the final logits on HTP0; only the embedding lookup on the CPU.
- Buffers: 1,912 MiB model, 448 MiB KV, 64 MiB compute.
- Proof saved per episode.
- **Visual:** a snippet of the startup log, or a stacked bar of the memory buffers.
- Caveat: K-quant support exists in the backend source; untested here (`02` §4).

### 6. Model screening on the NPU
- Four models, prefill and decode tok/s, time per verified diagnosis.
- **Chart:** grouped bars from `data/npu_model_screening.csv` (prefill and decode on two axes, or two panels).
- Message: decode is the bottleneck on every model (21–53 tok/s against 986–2,960 tok/s prefill).

### 7. Engineering findings on the board
- **Host prompt cache memory exhaustion:** stall after 36 calls; fixed with `--cache-ram 0`. Show the soak-test
  table.
- **`-lv 4` logging:** costs about 0%.
- **Determinism:** 13–22% of replies vary at temperature 0.
- **Thermal:** the chip reaches about 65–70 °C under continuous inference; cooling gates between episodes.
- Source: `02` §6–7.

### 8. Choosing the model: speed isn't enough
- The automatic campaign: smaller models fast but 37–46% broken JSON; the 0.5B worse than no model.
- Llama 3.2 3B chosen by override.
- Lesson: short, structured answers.
- Source: `03` §3.

### 9. Single-agent baseline
- L0–L7 pipeline; one ~1,900-token prompt and a ~160-token answer per tick on the NPU.
- Results: 13–17 s per call, 81 of 500 ticks over budget, 82–89% of time decoding, 71 unusable answers.
- **Chart:** prefill against decode share of call time. Source: `04`, `lane_speeds_in_runs.csv`.

### 10. Multi-agent architecture
- Agents, blackboard (one writer per section), scheduler with per-lane queues and priorities.
- Water and heat diagnosticians on the NPU; verifier on the CPU.
- Lockstep and real-time modes; the tick never waits.
- **Visual:** `05` §2 diagram. Source: `05`.

### 11. Making the workload NPU-friendly
- ~750-token prompts; 60-token line-number answers; GBNF grammar built per request; ask only on change.
- **Chart:** median call 13.8 s → 3.1 s; prompt 1,931 → 749 tokens; answer 159 → 34 tokens.
- Source: `lane_speeds_in_runs.csv`.

### 12. Speed result: single against multi-agent
- 12× less wall time on four episodes (9,915 s → 821 s); 0 ticks over budget (against 81); 0 unusable answers
  (against 71); 6× fewer prompt tokens.
- **Chart:** per-episode wall time bars from `episode_metrics.csv`. Source: `08` §D.

### 13. Accuracy journey
- Grammar fixed the format, then accuracy collapsed (0.378): the model overrode a better code ranking.
- Merge rules (tiebreak 0.688, nudge 0.750) recovered it.
- A prompt shortcut was found (the group letter): removing it doubled the model's own accuracy.
- `hybrid` lets the model lead only where the code is unsure.
- **Chart:** group top-1 per configuration with belief's line at 0.726 (`08` §E). Source: `06`.

### 14. v3 results on ten episodes
- Group top-1 0.703 against 0.575 for belief; right group in the top 3 0.91; right before the trip 0.76 against
  0.41; first right 9.6 min against 14.9 min after onset.
- Honest: the three fuel-fault episodes lose (look-alike tie), and one run, reporting set.
- **Chart:** per-episode v3 against belief bars (`08` §F).

### 15. NPU utilisation and headroom
- Multi-agent model work is about 1.5–2 s per 30 s tick: the NPU is idle most of the time.
- Plan: spend the idle time on targeted questions where the code is unsure (`reports/multi_llm_impact_design.md`).
  Both lanes can run at once in real-time mode.

### 16. Lessons for on-device agentic LLMs
1. Verify placement on every launch; don't trust defaults.
2. Watch host-side memory in the server (prompt cache).
3. Decode dominates: shrink answers, constrain them with a grammar.
4. Small models need a reliability gate, not just speed.
5. Let deterministic code own state and alarms; let the model rank, under a merge rule.
6. Measure the model's own picks to find prompt shortcuts.

### 17. Limitations and next steps
- Energy not measured; agent code on the laptop; simulated plant.
- Reporting-set evaluation; dev validation and the tie rule pending.
- Next: measure energy, move the agent onto the board, use idle NPU time, run the earliest-finish scheduling study.

### Backup slides
- Full per-episode tables (`episode_metrics.csv`).
- Merge-rule definitions (`05` §6).
- Lane speeds by agent (`lane_speeds_in_runs.csv`).
- Quantization tensor counts (`02` §4).
- The soak test (`02` §6).
- Metric definitions (`07` §4).

---

## Do / don't

| do | don't |
|---|---|
| label every chart's source run and episode set | mix the 4-episode and 10-episode averages in one comparison |
| say "group top-1 on the episodes measured" | say "the system is 70% accurate" |
| show belief (code only) as the baseline line on accuracy charts | omit belief: the model's value is the gap above it |
| state "energy not measured" if power comes up | infer energy from temperature or time |
| say "model calls run on the NPU; agent code ran on a laptop" | call tick times "on-device latency" |
| present NPU-vs-CPU rows as "measured in our runs" | present them as a controlled benchmark |
