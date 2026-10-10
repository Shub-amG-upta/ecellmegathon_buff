# Multi-agent Phase 4b: answer grammars, board runners, live monitor: report

## Status
PARTIAL. Built on the QIDK laptop on 5 October 2026 (branch `demo`): strict answer grammars for every model job
(`multi.grammar`), a per-tick live save, two board runners (real time and back to back), a live multi-agent stage
monitor. The grammars removed every wrong-format answer on the three reporting episodes run with them. Not done:
the dev before/after (only "before" exists), any accuracy analysis, real-time mode with grammars on the board.

Agent code ran on the laptop; only model calls ran on the board. Laptop timings are not board timings. Energy was
not measured. All plant data is synthetic.

## HUMAN DECISIONS (5 October 2026)
1. **Answer grammars on** ("fix the json replies, enforce the strict limits on the server, do anything required"),
   taken after the first board runs showed most Gemma 1B answers and a quarter to a half of Llama 3B diagnoses were
   unusable. Consequence stated to the human before the build and accepted: with a grammar the wrong-format rate is
   zero by construction and no longer measures the model, and the comparison with the single agent (which runs
   without a grammar) is uneven on that point.
2. **Back-to-back (lockstep) benchmark runs** of the multi-agent system, "for benchmark purposes with the previous
   single agent data, since it was also done this way".
3. **Result files carry `_multi` / `_single`** before their extension.
4. The human asked for the reporting episodes A01, B01, C01 by name for these runs. They were run to report, not to
   decide: decision 1 was taken before the grammar run on them. The first (pre-grammar) runs on A01 and B01 are what
   showed the problem, which is a use of reporting episodes the dev rule exists to avoid; recorded here.

## What was built
- `fieldmind/multi/grammar.py`: one GBNF per job, built from what that prompt showed (number of fact, case and note
  lines, group letters, claims; the note vocabulary). No whitespace, ascending line lists, distinct ranked cases, one
  verdict per shown claim, vocabulary words only. Sent per request (`grammar` field of llama-server); the job carries
  it, so a repair call is held to it too. `multi.grammar: false` in `configs/base.yaml` (old behaviour, request body
  unchanged), `true` in `configs/fast.yaml`. `fieldmind/agent/` is untouched.
- `run_demo.py --live-ticks FILE [--live-print]`: every tick's assessment appended to a JSONL file as it is published.
- `scripts/presentation_multi_all.sh` (real time), `scripts/benchmark_multi_lockstep_all.sh` (back to back),
  `scripts/monitor_multi.sh` (one episode with the monitor, nothing saved), `bench/stage_monitor_multi.py` (viewer).

## Verification
| step | result |
|---|---|
| ran the module | grammars exercised on the laptop and against both lanes; three reporting episodes run back to back on the board with grammars on (table below) |
| self-tests | `tests/test_grammar.py` 12, `tests/test_live_ticks.py` 2, `tests/test_stage_monitor_multi.py` 6: all pass. Full suite: see "What I could not verify" |
| independent re-derivation | (a) Accept / reject of the diagnosis grammar by llama.cpp's own `test-gbnf-validator` on the board against this module's `accepts`: 1 valid and 3 invalid strings, 4 of 4 agree, and the validator marks the same offending character. (b) Unusable diagnoses in the pre-grammar runs counted two ways, envelope status against the gate's answer records: A01 26 and 26, B01 47 and 47 (difference 0) |
| mutation check | 8 corruptions, each caught by `tests/test_grammar.py`: line lists may repeat; a case ranked twice; last claim not judged; a subject outside the vocabulary; one fact line more than shown; grammar never reaches the lane; grammar handed to a backend that cannot take one; grammar dropped from the request. Also caught: no flush in the live writer, hook not passed, nothing printed (`test_live_ticks`); verdict read as ok without an answer, newest instead of oldest job, idle lane ignored, simulated instead of real call time, verifier calls not read (`test_stage_monitor_multi`, one of these only after the test was strengthened) |

## Direction checks
| claim | expected | measured | |
|---|---|---|---|
| grammar on, wrong-format answers | fall | A01 diagnosis 26 of 84 to 0 of 57; verifier 9 of 11 to 0 of 5; notes 10 of 10 to 0 of 10. B01 47 of 112 to 0 of 52; 15 of 22 to 0 of 37; 5 of 5 to 0 of 5. C01 (grammar only) 0 of 25, 0 of 5 | OK |
| grammar on, repair calls | fall | 0 repair calls in 196 jobs (pre-grammar: every failed diagnosis made one) | OK |
| grammar on, decode speed | equal or slower | one call per lane: Gemma 1B CPU 62.2 to 46.2 tok/s; Llama 3B NPU 20.2 to 20.2 tok/s | OK, n = 1 each |
| longest answer under its cap | under | on the board: diagnosis max 43 answer tokens (cap 60), verifier 26 (cap 30), text reader 21 (cap 50), over 134 / 42 / 20 server calls | OK |

## Constants introduced
| symbol | value | provenance | range | affects |
|---|---|---|---|---|
| `MAX_FACTS_PER_CASE` | 3 | DESIGN CHOICE, so the longest diagnosis fits the 60-token cap | 1 to the fact lines shown (at most 9) | how many facts a ranked case may cite |
| `MAX_NOTES_USED` | 2 | DESIGN CHOICE, same reason | 1 to the note and record lines shown (at most 8) | how many notes an answer may name |
| `MAX_UNEXPLAINED` | 2 | DESIGN CHOICE, same reason | 1 to the fact lines shown | how many facts an answer may flag as unexplained |
| `MAX_RANKED` | 3 | = `compact.MAX_RANKED` (Phase 2) | fixed by Phase 2 | ranked cases per answer |

Longest answers on the lanes' own tokenizers (`/tokenize`, 5 October): diagnosis 51 tokens on both; verification 23
(Llama) and 22 (Gemma); note 41 and 42. Caps 60 / 30 / 50.

## Numbers that changed
Back-to-back runs, reporting episodes, Llama 3.2 3B on the NPU lane and Gemma 3 1B on the CPU lane, unusable
answers out of answers given:

| episode | run | diagnosis | verifier | note reader | wall clock |
|---|---|---|---|---|---|
| A01 | before | 26 of 84 | 9 of 11 | 10 of 10 | 322.6 s |
| A01 | grammar | 0 of 57 | 0 of 5 | 0 of 10 | 194.0 s |
| B01 | before | 47 of 112 | 15 of 22 | 5 of 5 | 496.2 s |
| B01 | grammar | 0 of 52 | 0 of 37 | 0 of 5 | 234.5 s |
| C01 | grammar | 0 of 25 | none called | 0 of 5 | 84.0 s |

Fewer diagnosis calls with the grammar because a failed answer is not cached and its side was asked again on every
tick. The number of verifier calls moved too (11 to 5, 22 to 37); not investigated. Dev, pre-grammar,
`dev_A01_fcv_seize`: diagnosis 11 of 34 unusable, notes 5 of 5, verifier not called.

Why the free answers failed (raw replies, dev run): Llama 3B writes one bracket too many
(`"r":[[1,[1,4]]],[1,[1]]]`) and ranks a case twice; Gemma 1B wraps every reply in a code fence and uses words
outside the vocabulary (`swing`, `fcv`, `FIRE_EXT`); on a verifier prompt it also hit the 30-token cap mid-answer.

## Disagreements recorded, not resolved
- **The grammar turns rejected note readings into accepted wrong ones.** Before, a note about an ash slurry pump
  came back with a subject outside the vocabulary and the gate rejected it whole. Now the model must pick a listed
  subject, and on A01 Gemma 1B coded the ten notes as `drum_level DOWN`, `feed_water_flow DOWN`, `steam_flow DOWN`
  (three times), `SUPERHEATER UP`, `FEED_VALVE DOWN`, `COAL_FEEDER_1 DOWN` and two empty readings, every one with
  kind `OTHER`. Nine of those ten notes are distractors (ash slurry pump, soot blowing, fire extinguisher, coal
  unloading, DM plant), so plant statements that no note made can now reach a diagnosis prompt. The prompt's rule
  ("use `"s":[]` if the note names no listed subject") is not followed. Not fixed: any fix (drop the pairs of an
  `OTHER` note, a "none of these" subject, a larger model for notes) changes prompts and needs the dev set.
- The server reported 25 answer tokens for a verdict the grammar tests put at 22 to 23 tokens in the lane's own
  tokenizer. Unexplained (an end token, or a different split under the grammar); the longest answers seen on the
  board stay under the caps (table above).
- Verifier call counts differ between the before and grammar runs in both directions. Unexplained.

## Blocked / needs a decision
1. Note readings (first item above): keep, filter by kind, or move the text reader to another model?
2. Single agent against multi-agent is now uneven on answer format (no grammar on the single agent). Options: report
   them as separate conditions, or re-run the single agent with a grammar of its own.
3. The dev before/after for the grammar was stopped by the human after the "off" half; run the "on" half
   (`dev_A01_fcv_seize`, back to back) or accept the reporting-episode runs as the evidence.
4. `agent.verifier`: the grammar forces a verdict on every shown claim. Is a forced verdict wanted, or should "not
   judged" stay possible?

## What I could not verify
- **Accuracy.** Nothing here says an answer is right; only that it is well-formed. Group top-1, faithfulness and
  actions of the grammar runs have not been compared with the single agent or with the pre-grammar runs.
- **Real time with grammars** has not run on the board. The one real-time run (A01, pre-grammar) was deleted at the
  human's request; from it only these were noted before deletion: 67 NPU calls of about 2.5 s median, 0 stale, code
  path at most 92 ms on the laptop, chip at 38.6 C after 56 minutes.
- **D01, E01, N01** have no saved grammar runs.
- `scripts/presentation_multi_all.sh` and `scripts/benchmark_multi_lockstep_all.sh` ran on the board before the
  `_multi` rename; after it they are syntax-checked only. `scripts/monitor_multi.sh` ran once on the laptop mock, not
  on the board. The single-agent scripts' new rename step ran on dummy files only.
- The viewer's full-screen loop was checked as single frames, not interactively by me.
- Pre-grammar real-model failure counts come from one run each; two identical runs differed on 10 of 37 replies on
  3 October, so small differences between runs are not evidence.
