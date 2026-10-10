# Handoff: run the multi-agent FieldMind on the QIDK, with the UI

*Written 5 October 2026 on the Mac (branch `multi-agent-p4`) for Claude Code on the Fedora laptop that drives the
QIDK. The person running you also has UI changes of their own on this laptop. **The repo is the source of truth:**
where this file and the repo disagree, trust the repo and say what differs.*

---

## 1. What you are getting

`multi-agent-p4` holds everything built on the Mac since 3 October, on top of the old `multi-agent` branch:

| part | what it is | where |
|---|---|---|
| Phase 2 | shrunk prompts (diagnostician ~750 tokens instead of ~1,900; verifier ~420 instead of ~1,540), line-number answers, the text reader for engineer notes | `fieldmind/multi/compact.py`, `agents/diagnostician.py`, `agents/verifier.py`, `agents/text_reader.py`; `reports/multi_phase2_prompt_shrink.md` |
| Phase 3 | the diagnostician split into a **water side** and a **heat side**; a side is asked again only when its evidence changed (about half the calls); a known-wrong-answer verifier tool | `fieldmind/multi/sides.py`, `agents/gate.py` (`fold_sides`); `bench/verifier_wrong_answer.py`; `reports/multi_phase3_split.md` |
| Phase 4 (demo subset) | **real-time mode**: ticks on the wall clock, model calls in the background on two lanes at once, the tick never waits; **two models**; `configs/fast.yaml`; `bench/realtime_summary.py`; a live `on_assessment` hook for a UI | `fieldmind/multi/realtime.py`, `orchestrator.py` (`tick_realtime`), `bench/harness.py`; `reports/multi_phase4_realtime.md` |

The demonstration setup (`configs/fast.yaml`, human decisions of 5 October):

| lane | port | model file | agents |
|---|---|---|---|
| NPU (HTP0) | 8080 | `Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf` | water diagnostician, heat diagnostician |
| CPU | 8081 | `gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf` | verifier, text reader (in the background) |

Both files are already on the board (`/data/local/tmp/llm/`), sha256-checked on 3 October. Server flags stay as
decided: `-c 4096 -np 1 -fit off -lv 4 --cache-ram 0` on both lanes.

**Everything on the Mac ran on the mock backend.** The mock does no reasoning: its numbers say nothing about
accuracy, and laptop timings are not board timings. Real time and the two-model setup have never run on the board.
That is what you are here to do.

Checks already done on the Mac (mock): 337 tests pass; the single agent and every earlier configuration give 0
decision differences and byte-identical prompts against their earlier runs; real-time tests with lanes slower than a
tick show the tick never waits, both lanes work at once, late answers follow the plan's stale rule, and no side is
asked twice for the same evidence.

---

## 2. Rules (from `CLAUDE.md`; they still hold)

1. Do not edit `fieldmind/agent/` (the single-agent baseline) or `bench/model_choice.py`.
2. Always `.venv/bin/python`; board scripts run with `PYTHONPATH` unset (`bench.board` does it itself).
3. Never `git push`, never `--force`; the person pushes.
4. Do not change thresholds, case data or band edges to improve a number. Do not run the 30 reporting episodes to
   decide anything.
5. `LD_LIBRARY_PATH` and `ADSP_LIBRARY_PATH` on every `adb shell` that starts a binary (`bench.board` does this).
   After any `adb push`, compare `sha256sum` on the board with the laptop. Log chip temperature before and after
   runs; leave cooling gaps.
6. Mock numbers are never reported as model accuracy; laptop timings never as board timings.
7. Speed is the goal for this demonstration (human decision): a strict one-change-at-a-time comparison is not
   required, but every number you report must come from a real run.

---

## 3. Step 1: check the state, change nothing

    cd ~/fieldmind
    git status                               # what is uncommitted? (UI changes, campaign results)
    git branch --show-current
    git log --oneline -10
    git log --oneline origin/main..main      # commits only this laptop has
    git fetch origin
    git log --oneline -3 origin/multi-agent-p4   # must include 2b02bde (Phase 4) or later

Report: which branch the UI changes are on, whether they are committed, and which files they touch
(`git diff --stat origin/main` and `git status`). **Stop and ask before the merge if anything is uncommitted** that
you did not create; ask the person to commit it (or commit it with a message saying it is theirs).

---

## 4. Step 2: one branch with everything

Commit the UI changes first (on the branch they are on), and push `main` if this laptop has commits GitHub lacks
(the person runs `git push`). Then:

    git switch -c demo origin/multi-agent-p4     # a new branch for the demonstration
    git merge main                               # brings --cache-ram 0, the board runner, the CANDIDATES check
    # and, if the UI is on a branch other than main:
    git merge <ui-branch>

**Expected conflicts and how to resolve them (keep both sides):**

| file | keep from `main` / the UI | keep from `multi-agent-p4` |
|---|---|---|
| `bench/harness.py` | the `on_tick(k)` hook (campaign: called before each tick) | `record_prompts`, `wrap_backend`, `mode`, `tick_s`, `on_assessment` parameters; the real-time loop; `belief_supports`; the `multi` telemetry. `run_episode`'s signature must carry ALL of these parameters |
| `run_demo.py` | `--url`, `--lane`, `--cooldown-s`, chip temperature, anything the UI added | `--arch --mode --compact --split --on-change --overlay --tick-s --placement --case-order --record-prompts --episodes-dir --tag`, `load_config(..., overlay)` |
| `configs/base.yaml` | anything `main` or the UI changed | the whole `multi:` block |
| `bench/board.py` | **all of it** (`--cache-ram 0`, the CANDIDATES sha256 check, the memory guard) | nothing |
| `.gitignore`, `CLAUDE.md`, `.claude/` | the board / campaign lines | the multi-agent lines |

Then check the merge on the mock:

    .venv/bin/pip install -r requirements.txt
    ls data/episodes_dev | wc -l                 # 36; else .venv/bin/python -m data.generator.episode_build --set dev
    .venv/bin/python -m pytest -q                # everything must pass; report the count
    .venv/bin/python -c "from bench import board; print(board.lane_command('npu', 'x'))"
                                                 # must show --cache-ram 0, -c 4096 -np 1, -fit off
    .venv/bin/python run_demo.py --episode dev_B02_tube_leak_fast --episodes-dir data/episodes_dev \
        --backend mock --arch multi --overlay configs/fast.yaml --mode realtime --tick-s 1 \
        --tag rt_mock --out results/rt_mock
    .venv/bin/python bench/realtime_summary.py results/rt_mock/runs_mock_rt_mock.json

Commit the merge. If tests fail and the fix is not an obvious merge slip, stop and report.

---

## 5. Step 3: connect the UI

Look at the UI code first and say how it gets its data today. The multi-agent system offers three ways in:

1. **Live, per tick (recommended for a demonstration):** `bench.harness.run_episode(..., on_assessment=callback)`.
   `callback(d)` is called right after every tick with that tick's published assessment, in lockstep and in real
   time. It is telemetry only: it must not change the agent, and it runs on the tick thread, so keep it fast (put
   the dict on a queue for the UI; do not render or do network I/O inside it, or the tick slows down).
2. **After a run:** `run_demo.py ... --out <dir> --tag <tag>` writes `<dir>/runs_<backend>_<tag>.json` (every tick
   of every episode) and `summary_<backend>_<tag>.json` at the end of the run.
3. **Lane health and speed:** `bench/realtime_summary.py <runs file>` and, while running,
   `.venv/bin/python -m bench.board speed http://localhost:8080` / `:8081`.

What an assessment dict holds (keys the UI can show):

| key | meaning |
|---|---|
| `tick`, `timestamp` | tick number and simulated plant time |
| `state` | `NORMAL`, `DEVIATION`, `ALARM`, `TRIP_IMMINENT`, or `DEGRADED` (a model failure that tick); derived from facts by code |
| `triage` | `QUIET`, `WATCH`, `INVESTIGATE`, `URGENT` |
| `headline` | one code-written sentence for the engineer |
| `facts` | the facts L1 emitted (`id`, `check`, `severity`, `tags`, `detail`) |
| `hypotheses` | ranked causes: `case_ref` (e.g. `RCA-01`), `cause`, `confidence`, `confidence_shown` (display this one), `supports` (fact ids, e.g. `F1` or `t83.F1` for an answer from an earlier tick), `discriminator` (what to check), flags `carried` / `model_only` / `verifier` |
| `actions` | approved actions from the catalogue (`id`, `text`, `urgency`) |
| `escalate` | True when the engineer must be alerted |
| `unexplained` | facts no ranked cause explains, verifier contradictions |
| `confidence` | display confidence of rank 1 |
| `llm_invoked` | a model was called this tick |
| `multi.p0_ms` | how long the tick took on the tick thread (must stay < 200 ms) |
| `multi.submitted` | model jobs sent this tick (agent, side, lane) |
| `multi.results` | model answers that arrived this tick (agent, lane, `queue_wait_ms`, start, finish, `stale`) |
| `multi.compact` | per answer: which side, which cases and facts it saw, what it answered; a `merge` record with the side order and which answers were re-used |

In real time, `on_assessment` fires every `--tick-s` seconds; model answers appear in later ticks as they arrive
(the ranking starts as code-only belief and is refined when an answer lands). Never feed a model answer into the UI
as if it were checked before it reaches the assessment: only the assessment is checked by the gate.

If the UI starts the agent itself, build it the way `run_demo.py` does (`load_config(path, backend, overlay)`, then
`run_episode(..., arch="multi", mode="realtime", tick_s=..., on_assessment=...)`). Do not copy agent code into the
UI.

---

## 6. Step 4: bring the board up (two lanes, two models)

    adb devices                                  # exactly one device (ab8f6592)
    .venv/bin/python -m bench.board stop
    .venv/bin/python -m bench.board temp
    .venv/bin/python -m bench.board start npu Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf
    .venv/bin/python -m bench.board start cpu gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf
    .venv/bin/python -m bench.board log npu | grep -E "offloaded|HTP0 model buffer|n_ctx"
    .venv/bin/python -m bench.board log cpu | grep -c "HTP0 model buffer"     # must print 0
    .venv/bin/python -m bench.board speed http://localhost:8080
    .venv/bin/python -m bench.board speed http://localhost:8081

NPU: "offloaded N/N layers" with equal numbers and a nonzero HTP0 buffer. Report both lanes' prefill and decode
tok/s: **the CPU lane with Gemma 1B has never been measured.**

---

## 7. Step 5: smoke test, then the demonstration

Smoke test, one episode, real 30 s ticks (about 45 minutes):

    systemd-inhibit --what=sleep:idle .venv/bin/python run_demo.py --backend llamaserver --arch multi \
        --overlay configs/fast.yaml --mode realtime --tick-s 30 \
        --episodes-dir data/episodes_dev --episode dev_B02_tube_leak_fast \
        --tag board_rt_smoke --out results/board_multi
    .venv/bin/python bench/realtime_summary.py results/board_multi/runs_llamaserver_board_rt_smoke.json

Check in the summary: `workers_alive` all true; `calls_slower_than_a_tick` empty; `tick_ms_max` < 200;
`parse_failure_rate` low; `stale_dropped` small; `slowest_submit_to_answer_s` per agent. A shorter tick for the
demonstration (`--tick-s 15` or `10`) is fine ONLY if every `slowest_submit_to_answer_s` is well under it: an answer
is used if it arrives within about one tick, or later while its side's evidence is unchanged; otherwise it is
dropped as stale.

The demonstration (run in `tmux`; log `bench.board temp` before and after):

    systemd-inhibit --what=sleep:idle .venv/bin/python run_demo.py --backend llamaserver --arch multi \
        --overlay configs/fast.yaml --mode realtime --tick-s 30 --cooldown-s 120 \
        --episodes-dir data/episodes_dev --episodes <id1>,<id2>,... \
        --tag board_rt_demo --out results/board_multi

(or through the UI, with the same settings). For comparison with the single agent on the same episodes, the single
agent's board results from the campaign or from the person's own runs are the reference.

---

## 8. What can go wrong on the board (known, not yet seen)

- **The 1B CPU lane is slow.** If verifier or text-reader answers take longer than a tick, verdicts arrive stale and
  are dropped; the diagnosis itself is unaffected (it is on the NPU). Fixes, in order: set `agent.verifier: never`
  in a copy of `fast.yaml`, or a longer `--tick-s`.
- **Heat waits behind water** on the NPU on a tick where both sides changed (rare: 23 of 1,982 dev ticks).
- **A hung call** holds its lane up to `llm.llamaserver.timeout_s` (120 s, twice with the repair call); the tick
  carries on with belief's ranking meanwhile.
- **Memory:** `--cache-ram 0` must be on both lanes (it comes from `main`); without it Llama 3B filled the board's
  memory after ~31 to 37 calls on 3 October.
- **Run-to-run variation:** two identical runs differed on 10 of 37 replies on 3 October (flags differed then);
  small differences between runs are not evidence of anything.
- Real-time answers can carry fact ids of an earlier tick (`t83.F2`); the evaluator handles them.

---

## 9. Open items you should not decide (ask the person)

- Phase 2 gate G1a: one predicted count was 19, measured 20 (the code follows the rule on all 3,170 cases); the
  30-episode mock reporting run waits on the person's decision.
- Advisor questions A1 and A2 (`reports/multi_phase3_split.md`): cross-side faults are rare in the data (23 of 1,982
  ticks); fuel-cap cases shown to both sides.
- Placement: both diagnosticians on the NPU is the human decision for the demonstration; "earliest finish" and the
  Phase 5 study come later.

---

## 10. How to report back

After each step: what you ran (exact commands), the full output of anything that failed, the test count, and for
board runs the `realtime_summary` output plus chip temperatures. Say plainly what you could not check. The person
pastes your reports into a separate chat for review before you continue.
