# Multi-agent accuracy recovery: progress

Updated and committed after every step. No accuracy claim until the final gate passes.

| step | what | state | check |
|---|---|---|---|
| pre-reg | answers a-c, human decisions 1-6, measurement sets, metric definitions | done | committed before any measurement |
| 0 | prompts and replies saved on board runs; replay tool; belief-alone on full dev; current multi on the quick set | **partial: board run not made (no QIDK attached)** | belief-alone full dev: library 0.502, held-out 0.709; replay faithful (0 differences on 5,850 mock ticks; outputs runs differ only on 5 unresolvable ties); 7 tests, 7 mutations caught; single unchanged |
| 1 | merge rule (decision 1): tiebreak primary, nudge secondary (amendment 1) | **built, mock-verified; board measurement waits for the session** | 3 rules; replay of each rule = its run, 0 differences on 5,850 mock ticks; 19 tests, 11 mutations caught; board_session.sh dry-run on mock |
| 2 | remove `g` | pending | |
| 3 | remove `x`, `n` | pending | |
| 4 | remove worked examples; shuffled-order echo run. STOP: full dev | pending | |
| 5 | case lines (13 lines shown before running) | pending | |
| 6 | case slots | pending | |
| 7 | reuse rule (N proposed, human decides) | pending | |
| 8 | verifier off, text reader off with raw notes. STOP: final gate | pending | |

## Stops hit
1. **Step 0 (planned STOP).** Baselines 1 and 2 done; baseline 3 needs the board (not attached here). Decision 1
   (option A or B) and the single-agent reference schedule are open.
2. **Step 1 (by instruction: Step 2 not started).** Rules built and verified on the mock; baseline 3, the A-vs-B check
   and the step-1 board run need `bench/board_session.sh` on the board laptop.
3. **Pre-board risks closed.** Replay claim proven from code and by a cross-rule prompt-identity test (verifier is
   the documented exception); session resume shown independent of the campaign's and tested with SIGKILL mid-episode;
   preflight and per-run model/lane checks added. Board commands are in the report.
