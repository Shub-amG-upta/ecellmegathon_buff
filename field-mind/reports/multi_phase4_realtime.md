# Multi-agent Phase 4 (demonstration subset): real time, two models: report

## Status
DONE on the mock (laptop). Two models per lane, both diagnosticians on the NPU, `configs/fast.yaml`, and real-time
mode with the plan's full stale rule; lockstep unchanged (336 tests; four lockstep configurations 0 differences and
byte-identical prompts against Phase 3). Board run not made yet: procedure at the end of this report. Branch
`multi-agent-p4`, made from `multi-agent-p3` at `7f73989`.

## HUMAN DECISIONS (2026-10-05)
1. **Speed first for the demonstration.** The human said the goal is a fast system ("we want ai go fast"), not a
   strict paper comparison. The one-change-per-step measurement of Phases 2 and 3 is not required for this build;
   lockstep runs keep reproducing every earlier result, so those comparisons stay available.
2. **The heat diagnostician runs on the NPU, not the CPU** ("it will take too long"). Both side diagnosticians are
   on the NPU lane; on a tick where both sides changed they queue there (23 of 1,982 dev ticks).
3. **Two models, overruling the plan's design rule 3 ("same model on both lanes"):** Llama 3.2 3B
   (`Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf`) on the NPU lane for the two diagnosticians; Gemma 3 1B
   (`gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf`) on the CPU lane for the verifier and the text reader, which "run in
   the background in parallel on the CPU, not constrained by ticks". Consequence recorded: a lane now means a model,
   so a job cannot move between lanes without changing model; the Phase 5 placement study has to be designed around
   this.
4. **Build what Phase 4 needs for this to work**: real-time mode, in which ticks fire on the wall clock and model
   calls really run in the background on both lanes at once. Not in this build: the drift watcher, the
   shared-memory slowdown test, moving the agent onto the board.

## Design
- **Per-lane model** (`multi.lanes.<lane>.model_file`): each lane's server is started with its own file; every call
  records the lane's model file. No other code depends on the model.
- **`configs/fast.yaml`**, an overlay on `configs/base.yaml` (`run_demo.py --overlay`): every compact section on,
  split on, call only on change, fixed placement with both diagnosticians on the NPU and the verifier and text
  reader on the CPU, the two model files, verifier `conditional`.
- **Real-time mode** (`run_demo.py --mode realtime [--tick-s S]`, multi only, needs split): one worker thread per
  lane, each running one call at a time from its own queue (newer job replaces a waiting one of the same agent and
  side). The tick loop never waits for a model: each tick runs the hard path, folds whatever answers have arrived
  since the last tick, submits new jobs, and publishes its assessment. Folding rules are the existing ones:
  - side answers go through `fold_sides` with the cache (commit 3). The stale rule is the plan's (p.10): an answer is
    accepted if it is at most one tick old, OR if its side's evidence has not changed since its job was built
    (fingerprint equal); otherwise it is dropped and counted. (The first version built only the one-tick half; the
    phase review caught it, below.) In real time the board keeps 40 ticks of facts (`multi.realtime_fact_ticks`), so
    an older answer's citations can still be checked against its own evidence tick. An accepted answer is cached and
    re-used while its side's evidence is unchanged; no new job for a side whose evidence is unchanged or whose job
    with the same evidence is already queued or running;
  - a verifier job is submitted when a new side answer was merged and the verifier's trigger fires; its verdict is
    applied to the tick it arrives in through `apply_verdict` (stale after one tick, counted);
  - text-reader jobs are submitted when a note arrives; checked note-facts are written when they come back (they
    reach a diagnosis prompt built after that).
- `--tick-s` sets the wall-clock tick for a run (default the agent's 30 s); a shorter tick is for tests and quick
  demonstrations and is recorded in the run file.
- Model agents own no board section, so their calls run off the main thread. A step taken on a worker thread is
  not audited, and any board write from a worker thread raises (`WriterError`, enforced in code and tested).

## Pass rules (written before any code)
| check | rule |
|---|---|
| lockstep unchanged | full suite green; the Phase 3 identity checks (single, Phase 2 all-on, split, on change) give 0 differences against the commit 5 runs |
| tick never waits for a model | real-time run on the mock with lanes made slow on purpose (each call sleeps longer than a tick): every tick's hard path finishes in < 200 ms and every tick starts on schedule |
| background work | the NPU and CPU lanes run calls at overlapping wall-clock times in that run |
| stale rule | answers older than one tick are dropped and counted, never merged |
| per-lane model | every call in a two-model run records its lane's model file |
| no duplicate work | never two queued or running diagnosis jobs for the same side with the same evidence |
| tests | every new rule tested and mutation-checked (`PYTHONDONTWRITEBYTECODE=1`) |

Real-time decisions are not compared with lockstep: answers arrive later by design, so the published ranking on a
tick can differ. Real timings on the laptop are not board timings; the board run measures them.

---

# Results (mock, laptop)

## Built
- **Two models**: `multi.lanes.<lane>.model_file`; `make_lanes` gives each lane's llama-server backend its own
  model file, recorded on every call. `bench.board start npu <file>` / `start cpu <file>` (on `main`) already take a
  file per lane, so the board side needs no code change.
- **`configs/fast.yaml`**, applied with `run_demo.py --overlay configs/fast.yaml` (deep merge over `base.yaml`).
- **Real-time mode**: `fieldmind/multi/realtime.py` (`RealtimeRunner`: one worker thread per lane, fixed placement,
  newer job replaces a waiting one); `MultiOrchestrator.tick_realtime` (never waits; drains, folds with the stale
  rule and the cache, applies verdicts, submits changed sides, verifier only after a fresh merge, one text-reader
  job per note); `bench/harness.py` paces ticks by the wall clock (`--tick-s`), waits for the lanes to go idle after
  the last tick and records lags, lane spans and both-lanes-busy time; `run_demo.py --mode realtime --tick-s`.
- Thread safety for the two lanes: the scheduler's current job is per thread (`LaneBackend` routes each call by
  its own thread's job); a blackboard step on a worker thread is not audited (model agents write nothing); the
  gate's cache re-use ignores a side answer dropped as stale.
- `bench/realtime_summary.py`: per-episode lanes, slowest call per agent (flags any call slower than the tick),
  stale drops, tick lag, worst tick time, group top-1.

## Checks (pre-registered rules)
| check | measured | result |
|---|---|---|
| lockstep unchanged | full suite 336 passed (re-run after the review fixes); single, Phase 2 all-on, split, split + on change lockstep dev runs: 0 differences and byte-identical prompt logs against the Phase 3 commit 5 runs | PASS |
| tick never waits for a model | real-time run on the mock with lanes slowed on purpose (diagnosis 0.15 s, verifier and text reader 0.3 s, tick 0.2 s): every tick on time (max lag < one tick, never early), every whole tick < 200 ms; a second run with diagnoses of 0.45 s (over two ticks): ticks still on time | PASS |
| background work | both lanes busy at the same time (both-lanes-busy time > 0); NPU ran only `diag_water` / `diag_heat`, CPU only `verifier` / `text_reader` | PASS |
| stale rule | fast diagnoses (within a tick) merged; every stale drop is more than one tick old; slow verifier calls produced stale drops; with diagnoses slower than two ticks, late answers on unchanged evidence ARE merged (`accepted_late_unchanged_evidence` > 0) | PASS |
| per-lane model | each lane's backend carries its own model file and URL (Llama 3B :8080, Gemma 1B :8081) | PASS |
| no duplicate work | with diagnoses slower than two ticks, the same side's evidence is never sent again while its job runs, and there are fewer diagnosis jobs than model ticks | PASS |
| board writes off the tick thread | refused (`WriterError`) | PASS |
| tests | `tests/test_realtime.py` 15 passed; 16 / 16 mutations caught (`PYTHONDONTWRITEBYTECODE=1`) | PASS |

## What real time needs on the board (recorded)
- **An answer is used if it comes back within about one tick, or later while its side's evidence is unchanged.**
  Measured Llama-3B NPU calls so far are single calls, not a benchmark: 3.89 s for a 700-token prompt and a 60-token
  answer, 1.85 s for 350 / 30 (`reports/session2_setup_progress.md`); the model-choice projection is 6.5 to 20.6 s per
  call from planning rates (`reports/phase0b_lanes_model_choice.md`). With both side diagnosticians on the NPU, heat
  waits behind water on a tick where both changed. With a shortened `--tick-s` for a demo, keep the tick longer than
  the slowest submit-to-answer time (`bench/realtime_summary.py` flags it, queue wait included). The CPU lane's speed
  for Gemma 1B with llama.cpp has never been measured; measure it first (`bench.board speed http://localhost:8081`).
- The llama-server timeout is 120 s (`llm.llamaserver.timeout_s`). The tick never waits for it, but a hung call
  holds ITS lane: with both diagnosticians on the NPU, a hung diagnosis (plus its one repair call) blocks both sides
  for up to about 240 s. The verifier and text reader on the CPU are unaffected.
- Lockstep note-reading order does not hold in real time: a note is read in the background, so its note-fact
  reaches the first diagnosis prompt built after it comes back.
- Laptop timings here are not board timings.

## Phase-reviewer findings and what was done
| finding | done |
|---|---|
| 1. **The stale rule was only half the plan's**: the plan (p.10) also accepts a late answer "if that side's evidence hasn't changed since". With slow lanes (0.45 s calls, 0.2 s tick) 0 of 21 NPU answers were used | **fixed**: the full rule, with 40 ticks of facts held in real time; the slow-lane test now asserts late answers are merged (mutation caught). The report's earlier wording ("the plan's rule, one tick") was wrong and is corrected |
| 2. **Single writer not enforced for worker threads** (an off-thread `write`/`mutable` passed, judged against the tick thread's active step) | **fixed**: `_check` raises `WriterError` off the tick thread; tested, mutation caught |
| 3. The "never waits" test had little margin; a computed count (`pairs`) was never asserted; no test that slow answers are ever merged | **fixed**: the slow-diagnosis run (0.45 s calls) asserts every tick < 200 ms and on time, fewer jobs than model ticks, late answers merged |
| the report's "~4.5 s Llama-3B" had no source | **fixed**: replaced by the measured single calls and the projection, with sources |
| `realtime_summary` judged a call by its run time, missing queue wait (heat behind water) | **fixed**: it flags submit-to-answer time |
| a worker that died (an exception outside the try) would leave its lane silently dead | **fixed**: the lane start is inside the try; `workers_alive` is reported |
| a call outliving the end-of-episode wait keeps running into the next episode of an `--all` run (`close` joins with a 5 s timeout) | **recorded**: leave `--cooldown-s` between episodes on the board |
| with the base placement (`diag_heat: cpu`) two threads would update the diagnostician's counters | **recorded**: the fast setup puts both sides on one lane; counters are telemetry only |
| the prompt recorder's tick is set by the tick thread and read by workers, so recorded prompts may carry a later tick in real time | **recorded**: do not use `--record-prompts` for real-time timing studies |
| in real time `prompt_tokens` / `predicted_finish_s` in job telemetry are empty, and `jobs_dispatched` / `jobs_replaced` are 0 (the real counts are under `realtime`) | **recorded**; the server's own token counts are in each call record |
| the per-lane model was checked on backend attributes, not on recorded calls; nothing checks the server's reported model | **recorded**: check the startup logs at `/board-up` |
| the non-compact verifier path would apply `revised_confidence` to whatever is rank 1 a tick later | **recorded**: latent; the fast setup uses the compact verifier, which matches by cause |

---

# Setting up and running the demonstration on the QIDK laptop
Written for the teammate whose Fedora laptop drives the QIDK (repo at `~/fieldmind`, branch `main`, the board
campaign, the four candidate GGUFs already on the board and sha256-checked).

## 0. On the Mac first: push the branches
    git push origin multi-agent multi-agent-p3 multi-agent-p4

## 1. On the QIDK laptop: keep the campaign work safe
    cd ~/fieldmind
    git status                               # must be clean; commit campaign results first
    git log --oneline origin/main..main      # the campaign and fix commits only this laptop has
    git push origin main                     # so they are not only on one laptop

## 2. Get the demonstration branch and merge `main` into it
`main` brings `--cache-ram 0` (the memory fix), the model-file sha256 check and the campaign runner.

    git fetch origin
    git switch -c multi-agent-p4 origin/multi-agent-p4
    git merge main

Conflicts are expected in `bench/harness.py` and `.gitignore`, and possibly `run_demo.py`, `CLAUDE.md`,
`configs/base.yaml`. Keep BOTH sides:
- from `main`: everything in `bench/board.py`, `bench/campaign.py`, the `on_tick` hook in `bench/harness.py`, the
  board results in `.gitignore`;
- from `multi-agent-p4`: `fieldmind/multi/`, `run_demo.py`'s `--arch --mode --compact --split --on-change --overlay
  --tick-s` flags, `bench/harness.py`'s `wrap_backend`, `mode` and real-time loop, the `multi:` block of
  `configs/base.yaml`, `configs/fast.yaml`.
Do not edit `fieldmind/agent/`. Then `git add <files>` and `git commit`.

## 3. Check the merge on the mock (no board needed)
    .venv/bin/pip install -r requirements.txt
    ls data/episodes_dev | wc -l             # 36 (else: .venv/bin/python -m data.generator.episode_build --set dev)
    .venv/bin/python -m pytest -q            # everything must pass
    .venv/bin/python -c "from bench import board; print(board.lane_command('npu', 'x'))"
                                             # must contain --cache-ram 0, -c 4096 -np 1, -fit off
    .venv/bin/python run_demo.py --episode dev_B02_tube_leak_fast --episodes-dir data/episodes_dev \
        --backend mock --arch multi --overlay configs/fast.yaml --mode realtime --tick-s 1 \
        --tag rt_mock --out results/rt_mock
    .venv/bin/python bench/realtime_summary.py results/rt_mock/runs_mock_rt_mock.json

## 4. Board up: two lanes, two models
    adb devices                              # exactly one device (ab8f6592)
    .venv/bin/python -m bench.board stop
    .venv/bin/python -m bench.board temp
    .venv/bin/python -m bench.board start npu Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf
    .venv/bin/python -m bench.board start cpu gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf
    .venv/bin/python -m bench.board log npu | grep -E "offloaded|HTP0 model buffer|n_ctx"
    .venv/bin/python -m bench.board log cpu | grep -c "HTP0 model buffer"     # must print 0
    .venv/bin/python -m bench.board speed http://localhost:8080
    .venv/bin/python -m bench.board speed http://localhost:8081
NPU: "offloaded N/N layers" with both numbers equal and a nonzero HTP0 buffer. Note both lanes' prefill and decode
tok/s: the CPU lane (Gemma 1B) has never been measured.

## 5. Smoke test: one episode, real time
    systemd-inhibit --what=sleep:idle .venv/bin/python run_demo.py --backend llamaserver --arch multi \
        --overlay configs/fast.yaml --mode realtime --tick-s 30 \
        --episodes-dir data/episodes_dev --episode dev_B02_tube_leak_fast \
        --tag board_rt_smoke --out results/board_multi
    .venv/bin/python bench/realtime_summary.py results/board_multi/runs_llamaserver_board_rt_smoke.json
At 30 s per tick this episode takes about 45 minutes. Check in the summary: `workers_alive` all true,
`calls_slower_than_a_tick` empty, `parse_failure_rate` low, `tick_ms_max` < 200, `stale_dropped` small. For a
shorter demonstration use `--tick-s 15` (or 10) ONLY if every `slowest_submit_to_answer_s` from the smoke test is
well under that tick.

## 6. The demonstration episodes
    systemd-inhibit --what=sleep:idle .venv/bin/python run_demo.py --backend llamaserver --arch multi \
        --overlay configs/fast.yaml --mode realtime --tick-s 30 --cooldown-s 120 \
        --episodes-dir data/episodes_dev --episodes <id1>,<id2>,<id3>,<id4>,<id5>,<id6> \
        --tag board_rt_demo --out results/board_multi
    .venv/bin/python bench/realtime_summary.py results/board_multi/runs_llamaserver_board_rt_demo.json
Run it inside `tmux` so a closed terminal does not stop it. Log `bench.board temp` before and after.
Optional, does the Gemma-1B verifier catch a known-wrong diagnosis (lockstep, both lanes up):

    .venv/bin/python bench/verifier_wrong_answer.py --backend llamaserver --overlay configs/fast.yaml \
        --episodes dev_A03_bfp_suction,dev_B01_tube_leak,dev_C05_low_cv_coal \
        --out results/board_multi/verifier_wrong.json
