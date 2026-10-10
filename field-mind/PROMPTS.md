# FieldMind multi-agent build: one Claude Code session per step

Run each session from the repo root with the venv active:

```sh
source .venv/bin/activate
```

Paste the prompt at the `>` line. Do not start the next session until the current one's check passes and is committed.
After each build session, ask in the same session: "Use the phase-reviewer subagent on this phase." Fix what it finds,
then commit.

---

## Session 0 - freeze the baseline (on main)

```zsh
claude -n ma-0-baseline
```

> Read CLAUDE.md and docs/multi_agent_plan.pdf. Change no code in this session. Run /bench --arch single --backend mock --mode lockstep
> (the --arch and --mode flags don't exist yet, so run the existing benchmark exactly as it runs today). Save the
> results as results/baseline_single_v1.json together with the commit hash. Then list, from the code as it stands, which of
> the "Known bugs" in CLAUDE.md are still present, with file and line for each. Confirm whether records.json now reaches the
> agent and whether query.txt is read. Report only; do not fix anything.

Then, yourself: `git tag single-agent-v1`

---

## Session 1 - phase 0a: correctness fixes in the single agent (on main)

```zsh
claude --permission-mode plan -n ma-0a-fixes
```

> Phase 0 of docs/multi_agent_plan.pdf, correctness part only. On main, in fieldmind/agent/ and bench/:
> 1. update_hypotheses must compare (tag, direction, band) triples, the same way CaseLibrary.match does. Add a test: at
>    ep_A01 tick 76 the RCA-01 hypothesis must not be charged for a flat bed temperature.
> 2. Retire hypotheses that have not been retrieved for N consecutive ticks (N in configs/base.yaml), and log each retirement
>    as an Event. Add a test using the RCA-09 / RCA-10 tie.
> 3. Add a group-level metric to bench/evaluator.py: cases whose six-tag signatures are identical or nearly identical form a
>    group (compute the groups from case_library.json and write them to data/kb/case_groups.json so they can be reviewed).
>    Score "correct group" and "named the separating case" alongside the existing top-1 and top-3.
> 4. Make the LLM answer cap a config value and set it to 256 for now.
> Propose the plan first. After building, run the tests, then /bench with the mock backend, and compare against
> results/baseline_single_v1.json. Explain every metric change.

---

## Session 2 - phase 0b: persistent model servers and model choice (on main)

Needs the QIDK on USB.

```zsh
claude --permission-mode plan -n ma-0b-lanes
```

> Phase 0 of docs/multi_agent_plan.pdf, runtime part.
> 1. Add a LlamaServerBackend in fieldmind/runtime/llm_backend.py that talks HTTP to a llama-server at a configured URL
>    (OpenAI-style chat endpoint), fills every LLMReply field (latency_ms, backend, model, prefill and decode tokens,
>    ttft) from the server's own timing fields, and registers itself with register_backend(). Nothing else in the agent changes.
>    Config gets llm.backend: llamaserver and llm.url.
> 2. Then run /board-up with the Q4_0 model we already have on the board, and confirm both lanes.
> 3. Model choice: with each candidate model that is on the board (list them first), run 3 fault episodes (A01, B01, one from
>    family C) on the NPU lane through the single agent. Report top-1, group-level accuracy, citation faithfulness, broken-JSON
>    rate, and per-lane prefill and decode tok/s measured from /board-up's test prompt. Do not pick a model; give me the table.
> 4. Write the measured NPU and CPU rates into configs/base.yaml as the scheduler's starting estimates.

After the table: choose the model yourself, put it in configs/base.yaml, and commit.

---

## Branch for the multi-agent work

```zsh
git switch -c multi-agent
```

---

## Session 3 - phase 1: plumbing only

```zsh
claude --permission-mode plan -n ma-1-plumbing
```

> Phase 1 of docs/multi_agent_plan.pdf ("Plumbing only"). Build fieldmind/multi/ as laid out in CLAUDE.md, but the only
> model agent is the existing single diagnostician, run as one job. Include: tick-stamped fact IDs; blackboard sections with
> single-writer enforcement; Job and Result envelopes with evidence_tick, lane and queue_wait_ms; two lanes (NPU 8080, CPU 8081)
> with one call in flight each; a scheduler with fixed placement; the gate rejecting stale answers (older than one tick unless
> that side's evidence is unchanged). Add --arch single|multi to the harness; lockstep mode only for now (the harness waits for
> all jobs of a tick before moving on).
> Tests: single-writer violation raises; a stale answer is dropped and logged; a hard-path tick never waits on a lane (use a
> fake lane that sleeps 60 s).
> Check: /bench --arch multi --backend mock --mode lockstep must match the phase 0 results on accuracy, because only plumbing
> changed. Any difference is a bug; find it before going on.

---

## Session 4 - phase 2: shrink the prompt

```zsh
claude --permission-mode plan -n ma-2-prompt
```

> Phase 2 of docs/multi_agent_plan.pdf ("Shrink the prompt"). Add the text-reader agent (note or record in, note-fact N1... out,
> with reliability and a link back to the raw note); diagnosis prompts get note-facts, never raw notes. Compact case lines (case ID,
> signature, discriminating check, nothing else). Switch the diagnostician to the ID-only answer format in the plan
> (g, r, sep, n, x) with a 60-token cap; the gate turns it into the engineer's sentences. Log prompt tokens per call.
> Before running: I set the allowed accuracy drop for this phase to [WRITE YOUR NUMBER HERE] on group-level accuracy.
> Check: every diagnosis prompt is under 1280 tokens on all 30 episodes (report max and p95), and group-level accuracy is within
> the allowed drop of phase 1. Run the check on mock first, then on the NPU lane for A01, B01 and one family C episode.

---

## Session 5 - phase 3: split by plant side

```zsh
claude --permission-mode plan -n ma-3-split
```

> Phase 3 of docs/multi_agent_plan.pdf ("Split by side"). Water and heat diagnosticians as two jobs that can run at the same time
> on different lanes; each prompt carries one line about the other side; belief merges both answers as separate evidence; the
> verifier runs after the merge on whichever lane frees first, and only in the uncertain band or at INVESTIGATE and above.
> Scheduler: request a side's diagnosis only when that side's signature, findings or note-facts changed; at most one queued job per
> (agent, side); a newer job replaces a waiting one.
> Check: group-level accuracy at least phase 2's, reported separately for faults that move both sides (family B) and faults that
> move one side; diagnosis latency mean and p95; model calls per tick. Also feed the verifier one episode where rank 1 is known to
> be wrong and show whether it disagrees.

---

## Session 6 - phase 4: real time on the board

```zsh
claude --permission-mode plan -n ma-4-realtime
```

> Phase 4 of docs/multi_agent_plan.pdf. Add --mode realtime to the harness: ticks fire every 30 s of wall-clock time and jobs really
> overlap; record per tick the hard-path time, every job's queue wait, lane, latency and whether it was dropped as stale. Add the drift
> watcher (cumulative offset on load-corrected bed temperature; threshold tuned on normal episodes only). Add the shared-memory test:
> decode speed of each lane alone versus both lanes decoding at once.
> Then propose, but do not build yet, how to move the agent code onto the board itself (Python on the device, talking to the lanes on
> localhost) so hard-path timings become board timings. List what blocks it.
> Check: run one episode per fault family plus two normal episodes in real-time mode, with chip temperature logged. Zero hard-path
> misses; report stale-drop rate and lane busy time.

---

## Phase 5 - the scheduling study

Plan it in its own session once phase 4's numbers are in: the four placement policies, the degradation ladder, and power measurement on
the board.
