# FieldMind benchmark command: guide

`scripts/benchmark.sh` runs one or more episodes on the QIDK board, shows the live stage monitor in the same
terminal, and saves every result under `results/benchmarks/<test name>/`.

---

## 1. Before you start

- Run from the repo root (`~/fieldmind`) in a normal terminal window. tmux is fine and protects the run if the
  window closes.
- Exactly one board on adb: `adb devices`.
- Nothing else on the board. The command refuses to start if another board run is going (the campaign,
  `bench/board_session.sh`, `benchmark_multi_lockstep_all.sh`, or another benchmark).
- If the VPN blocks the board or GitHub, that is a network problem, not this command.
- You do **not** need to start the model servers yourself. The command starts them before every episode and stops
  them afterwards.

---

## 2. The command

```
scripts/benchmark.sh <test name> <episode> [<episode> ...] [options]
```

The test name is the results folder name. Use letters, digits, `.`, `_` and `-` only.

| episode prefix | where it comes from |
|---|---|
| `ep_*` | the reporting episodes (`data/episodes`) |
| `dev_*` | the dev episodes (`data/episodes_dev`) |

Example, the six presentation episodes:

```
scripts/benchmark.sh six_episodes ep_N01_normal ep_A01_fcv_seize ep_B01_tube_leak \
    ep_C01_wet_coal ep_D01_high_cv_coal ep_E01_fouling_drift
```

Every option is listed by `scripts/benchmark.sh --help`.

---

## 3. Options

| option | meaning |
|---|---|
| `--arch multi\|single` | `multi` (default): the multi-agent system. `single`: the single agent, for comparison. |
| `--overlay FILE` | Config overlay. Defaults: `configs/fast.yaml` for multi (Llama 3.2 3B on the NPU, Gemma 3 1B on the CPU, answer grammars on); no overlay for single. `configs/accuracy.yaml` puts Llama 3B on both lanes. |
| `--mode lockstep\|realtime` | `lockstep` (default): ticks run back to back, and each tick waits for its model answers. This is how the single-agent results were made, so use it for comparisons. `realtime`: one tick every `--tick-s` seconds, and a tick never waits for a model (multi-agent only). |
| `--tick-s N` | Realtime only: seconds per tick (default 30). |
| `--merge-rule R` | Multi only: `model`, `belief_only`, `tiebreak` or `nudge`. This is your teammate's accuracy-fix switch. Only `model` works in realtime mode. |
| `--again` | Run episodes this test already has once more. The new run is saved as `_r2`, `_r3`, ... |
| `--backend mock` | Laptop only, no board. Results go to `results/benchmarks_mock/`. This checks the plumbing and is **never** an agent result. |
| `--no-prompts` | Do not save prompts and raw model replies. They are saved by default and are needed by `bench/replay_multi.py`. |
| `--cool-margin C` | Before each episode, wait until the chip is within C degrees of the reading at the start (default 5) ... |
| `--cool-max S` | ... but wait at most S seconds (default 900). |
| `--plain` | Print one line per tick instead of the full-screen monitor (for logs or pipes). |

---

## 4. More examples

Single agent on the same episodes, under its own name:

```
scripts/benchmark.sh six_single ep_A01_fcv_seize ep_B01_tube_leak --arch single
```

Real time, a tick every 30 s (the demonstration):

```
scripts/benchmark.sh rt_demo ep_A01_fcv_seize --mode realtime --tick-s 30
```

Teammate's accuracy setup on dev episodes:

```
scripts/benchmark.sh tiebreak_quick dev_A01_fcv_seize dev_B01_tube_leak \
    --overlay configs/accuracy.yaml --merge-rule tiebreak
```

Repeat an episode a test already has:

```
scripts/benchmark.sh six_episodes ep_A01_fcv_seize --again
```

Try the command on the laptop without the board:

```
scripts/benchmark.sh try1 dev_C01_wet_coal --backend mock
```

---

## 5. What happens when you run it

1. Checks the name, the episodes and the options, the board, and that nothing else is using the board.
2. Stops any running model servers and reads the chip temperature. That reading is the cooling target for the whole
   batch.
3. For each episode:
   1. Waits for the chip to cool, if needed.
   2. Starts the model servers fresh with the fixed flags and checks the model file checksums.
   3. Confirms from the startup logs that the NPU lane is fully on the NPU and that the CPU lane is not. If not, it
      stops.
   4. Runs the episode while the monitor shows it live.
   5. Logs the chip temperature, stops the servers, and saves the results.
4. When all episodes are done, it leaves the monitor and prints the results table.

---

## 6. Reading the monitor

### Top lines (cyan)
The test name, the results folder, episode *i* of *n*, what the runner is doing (cooling, starting a lane, running,
saving), and one line per episode already saved in this session.

### Header
- Episode, elapsed time, mode, and the tick number of the last tick.
- Model calls answered, and tokens in / out so far.
- Per agent: number of calls, median call time, median input tokens, and how many answers could not be used.
- The green score line (see section 7).

### Units
- **CODE:** the agent's non-model work on the laptop: how long the last tick took, the worst so far, and the 200 ms
  limit. These are laptop times, not board times.
- **NPU / CPU:** what each board lane is doing now (live from the server), plus its last answer: run time, tokens
  in and out, and whether it was used.

### Tick table (one row per tick)

| column | meaning |
|---|---|
| `tick`, `plant` | tick number and simulated plant time |
| `state`, `triage` | worked out by code (models are called at WATCH or above) |
| `code` | the tick's non-model time |
| job columns | multi-agent: `water·NPU`, `heat·NPU`, `verify·CPU`, `notes·CPU`; single agent: `diagnose·NPU`, `verify·NPU` |
| `pub`, `bel`, `conf` | see section 7 |
| `top cause` | the published top cause |

### Job cells

| cell | meaning |
|---|---|
| `2.8s 712t✓` | the call took 2.8 s and read 712 input tokens (repair call included); the answer was used |
| `✓` | answer used |
| `∅` | well-formed, but it ranked no case it was shown |
| `✗` | wrong format, failed call, or rejected note reading |
| `~` | arrived too late, dropped (realtime only) |
| `▶3s` | running for 3 s (realtime only) |
| `answered` | done; it is used at the next tick (realtime only) |
| `reuse` | no new call; the earlier answer was kept because the evidence had not changed |
| `·` | not asked this tick |
| `—` | quiet tick, no models involved |

Row colours: yellow means a job is still running, red means an answer was not usable, grey means a quiet tick.

In lockstep, a row appears only when its whole tick is finished. While a tick runs, the lanes show busy or idle, but
the job is named only once the tick is written.

---

## 7. The metrics explained

### Two rankings exist on every tick

- **Belief, the code-only ranking.** The code turns the sensor facts into a score for each candidate cause from the
  case library and keeps updating those scores tick after tick. No model is involved.
- **The published ranking,** what the system actually shows the engineer. In the multi-agent system the model
  answers are merged into belief's ranking; with the current setting (`merge_rule: model`) the model's order wins.

The model is only worth running if the published ranking is right more often than belief alone. The new metrics
compare the two.

### Per-tick columns: `pub`, `bel`, `conf`

**`pub`**: is the published top cause right?
**`bel`**: would belief's own top cause have been right?

| mark | meaning |
|---|---|
| `✓` | it is the true cause (the exact case from the library) |
| `≈` | not the exact case, but one in the same look-alike group: cases the sensors cannot tell apart, e.g. a seized feed valve and a feed-pump problem both show as "feed water short". The groups are fixed in `data/kb/case_groups.json`. |
| `✗` | wrong group |
| `pre` | the fault has not started yet, so the tick is not scored |
| blank | quiet tick, or a no-fault episode (nothing to be right about) |

How to read the pair:

| `pub` | `bel` | meaning |
|---|---|---|
| ✓ or ≈ | ✗ | the model fixed belief's mistake |
| ✗ | ✓ or ≈ | the model made it worse |
| same | same | the model changed nothing that matters |

**`conf`**: the confidence shown for the published top cause, from 0 to 1. It is capped by how far that cause is
ahead of its nearest rival, so two tied causes show 0.50 even if each looks likely on its own. It is display only;
nothing in the agent decides on it.

### The score line (green, in the header)

This is computed by the same scoring code that writes the final summary (`bench/evaluator.py`), so the live numbers
become the final ones.

| metric | meaning |
|---|---|
| true cause | the episode's real cause, e.g. `RCA-01` |
| scored ticks | ticks counted so far: from fault onset onward, with a ranking published |
| published group top-1 | share of scored ticks where `pub` was ✓ or ≈. **The headline accuracy.** |
| belief alone | the same share for `bel`; the bar the model has to beat |
| exact top-1 | share of scored ticks where `pub` was ✓ only (the exact case) |
| mean shown confidence | average `conf` over scored ticks |

Some episodes are scored differently:
- **Held-out cause** (e.g. C01, wet coal, RCA-06): the true case was deliberately left out of the library to test
  generalisation. The system can never name it exactly, so exact top-1 is not shown and only group credit counts.
- **No-fault episode** (N01): there is no cause to find. The line shows the false alarms instead: ticks where the
  state was not NORMAL, per hour, with a target of 2 per hour or fewer.

### Token and call metrics

| where | metric | meaning |
|---|---|---|
| job cell | `2.8s 712t✓` | the call took 2.8 s and the model read 712 input tokens (prompt tokens as the server counted them, repair call included) |
| header | tokens in / out | total prompt tokens read and answer tokens written so far |
| per-agent line | median call time, median tokens in | the typical call for that agent |
| per-agent line | not usable | answers that could not be used: wrong format, failed call, rejected note reading, or too late |
| lane line | last answer: … tokens in / out | the most recent call on that lane |

Input tokens matter for speed. On the NPU, reading the prompt runs at roughly 650–900 tokens/s and writing the
answer at about 14–20 tokens/s, so prompt size drives part of every call's time.

### Metrics in RESULTS.md

The same numbers per finished episode (group top-1, belief group top-1, exact top-1, tokens in, unusable answers),
next to the older evaluator metrics:

| metric | meaning |
|---|---|
| faithfulness | share of cited fact IDs that really exist |
| actions precision / recall | suggested actions against the correct ones |
| lead time | minutes between the first correct call and the trip |
| false alarms /h | no-fault episodes only: ticks not NORMAL, per hour |
| tick p95 | the slowest 5% of tick times (laptop time) |

---

## 8. The results folder

`results/benchmarks/<test name>/`

| file | contents |
|---|---|
| `test.json` | the test's settings and every run: start and finish time, git commit and whether there were uncommitted changes, chip temperature before and after, cooling wait, wall time, exit code, status (done / failed / interrupted) |
| `RESULTS.md` | one row per finished episode run (see section 7) |
| `temps.jsonl` | chip temperature before and after every episode |
| `<ep>_<arch>.summary.json` | evaluation, run facts (calls, tokens, unusable answers per agent) and settings |
| `<ep>_<arch>.run.json.gz` | every tick, every prompt and raw reply |
| `<ep>_<arch>.log` | the run's own output |
| `<ep>_<arch>.lanes.txt` | the startup lines that prove NPU / CPU placement |
| `<ep>_<arch>_r2.*` | a repeat made with `--again` |

`<arch>` is `multi` or `single`.

While an episode runs there is also `<ep>_<arch>.ticks.jsonl`, written every tick. It is deleted once the run file is
saved, because the run file holds the same ticks.

Git: `test.json`, `RESULTS.md`, temps, summaries, run files, logs and lane files are tracked. Live tick files,
stopped attempts and mock results are not.

---

## 9. Same name again, resuming, repeats

- **Same test name, new episodes:** they are added to the same folder.
- **Same test name, an episode it already has:** skipped, with a message. This is how a stopped test resumes; run the
  same command again.
- **`--again`:** the episode is run once more and saved as `_r2`, `_r3`, ... Nothing is ever overwritten.
- **Same test name with different settings** (architecture, mode, tick, overlay or its contents, merge rule, models,
  backend, prompt saving): refused, with the differences listed. Use a new name for a new setup.

---

## 10. Stopping

Press **Ctrl-C** once:
- the episode run is stopped and the model servers are shut down;
- the stopped episode's ticks, log and lane file are kept as `<stem>.interrupted-<time>.*` and marked
  "interrupted" in `test.json`;
- episodes already saved are kept;
- run the same command again to continue. The stopped episode starts over.

A second Ctrl-C leaves at once.

If an episode fails on its own (not Ctrl-C), the batch stops and the end of that episode's log is printed.

---

## 11. Troubleshooting

| message or symptom | what to do |
|---|---|
| "another board run is going" | Something else uses the board. Wait for it or stop it. |
| "need exactly one board on adb" | Check the cable and run `adb devices`. |
| "the NPU lane is not fully on the NPU" | The NPU server did not load on the NPU. Read `<stem>.lanes.txt`. Do not work around it. |
| "already exists with other settings" | You reused a test name with different options. Pick a new name. |
| "unknown episode(s)" | Check the spelling. Dev episodes start with `dev_`, reporting episodes with `ep_`. |
| the monitor looks frozen | In lockstep, a tick's row appears only when its model calls finish. If the NPU / CPU lines show "working", it is running. |
| screen garbled after a crash | Type `reset` and press Enter. |

---

## 12. What to keep in mind when reporting

- Mock results are plumbing only, never agent results.
- Tick and code times are laptop times. Only model calls run on the board.
- Energy is not measured; it is left empty, never estimated.
- All plant data is synthetic.
- The answer grammar (on in `configs/fast.yaml`) makes wrong-format answers impossible, so a zero wrong-format rate
  says nothing about the model.
- Decisions about thresholds or prompts are made on `dev_` episodes, not on the `ep_` reporting episodes.

---

## 13. Status of this command (7 October 2026)

Tested on the laptop with the mock backend: folders and files, resume, `--again`, refusal of other settings, token
counts, and per-tick marks that match the evaluator's score. **Not yet run on the board:** lane start-up, cooling,
the NPU placement check and Ctrl-C. Watch the first minute of the first board run.

Files:
- `scripts/benchmark.sh`: the entry point
- `bench/benchmark.py`: the runner
- `bench/stage_monitor_multi.py`: the monitor. It can also view a run on its own:
  `.venv/bin/python bench/stage_monitor_multi.py`
- `tests/test_benchmark.py`: the tests
