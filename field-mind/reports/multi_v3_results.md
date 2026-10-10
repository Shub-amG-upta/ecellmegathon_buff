# multi_v3 on the board: results, analysis and conclusions

*8 October 2026. For the FieldMind team and the advisor. Data: `results/benchmarks/multi_v3/` (12 reporting
episodes, QIDK board, lockstep), compared with belief alone (same run) and the earlier nudge runs
(`multi_nudge_6mark`, `multi_nudge_6.1mark`). All plant data is synthetic. Agent code ran on the laptop, model calls
on the board, so tick times are laptop times. Energy was not measured.*

---

## 0. Summary

| | v3 (hybrid) | belief alone | nudge (earlier prompt) |
|---|---|---|---|
| published group top-1, mean of 10 fault episodes | **0.703** | 0.575 | 0.644 |
| true group in the top 3 | **0.908** | 0.722 | 0.792 |
| ticks helped / harmed vs belief | **224 / 38** | – | 55 / 12 |
| minutes from fault onset to the first right top cause | **9.6** | 14.9 | 14.6 |
| right before the trip (A01, A02, A03, D01) | **0.76** | 0.41 | 0.48 |
| team's pass rule (`bench/beats_belief.py`) | **FAIL** (C01 −0.349) | – | PASS |

1. **Removing the group letters doubled the model's own accuracy:** its first pick was right on 0.33 of answers
   before, 0.65 now. The change is exactly where predicted: tube leaks (B) 0.04 → 0.70 and high-CV coal (D)
   0.02 → 0.70, while feed faults (A) are unchanged and fuel faults (C) fell from 0.60 to 0.49.
2. **`hybrid` turns that into published accuracy.** On ticks where belief was tied and the model led, the published
   top cause was right on 0.74 of ticks against belief's 0.44 (374 ticks). On weak-belief ticks it was 0.83 against
   0.21.
3. **The losses come from one artefact.** On all three fuel-fault episodes (C), the true answer is a look-alike pair
   (RCA-14, RCA-18) that belief always scores identically. `hybrid` reads that as a tie and lets the model lead, and
   the model then leads with a different cause. This alone makes v3 fail the pass rule.
4. **A group-aware tie test would fix C,** passing the rule on these answers at +0.156, but it would give back A03,
   where the tied look-alike pair belief put on top was the wrong one. Neither version dominates, so this must be
   decided on the dev set.
5. **Early warning is the biggest practical gain:** right before the trip 0.76 against 0.41, and the right cause
   first shown about 5 minutes sooner.
6. **Shown confidence got worse at telling right from wrong** (AUROC 0.60 against 0.69), and the top cause changes
   more often (8.2 against 5.5 per 100 ticks). Both follow from the model leading.
7. **Prompts got smaller with raw notes,** not bigger: median 731 against 748 tokens, max 873 against 914. Call time
   is unchanged (3.05 against 3.01 s).
8. **Injection resistance was not tested:** in both injection episodes the poisoned note was never selected into a
   diagnosis prompt.

---

## 1. What was run

| item | value |
|---|---|
| command | `scripts/benchmark.sh multi_v3 <12 episodes> --overlay configs/v3.yaml` |
| configuration | `configs/v3.yaml` = `configs/fast.yaml` + `merge_rule: hybrid` + `text_reader: false` + `note_source: raw` |
| what v3 changes from earlier runs | no group letters on case lines or in the answer; raw note text instead of the Gemma note reader; `hybrid` merge rule with the filler-case guard |
| models | Llama 3.2 3B on the NPU (both diagnosticians); Gemma 3 1B on the CPU (verifier) |
| episodes | 10 fault episodes (A01–A03, B01–B03, C01–C03, D01) plus N01 and E01; all reporting episodes |
| code | the same throughout. C02 and C03 record commit `c13a5f9` because the code was committed during the run; no code file changed after the run started (checked by modification time and `git diff`). |

### 1.1 Run conditions to know about

- **B01 needed two attempts.** The first stopped after 81 s on a dropped board connection
  (`RemoteDisconnected`); this copy lacks the teammate's fix that turns that into a failed call. During those 81 s
  the chip went from 38 °C to 80.6 °C (unexplained). The second attempt completed.
- **Cooling was defeated after the restart.** The restarted batch took its cooling target from the hot chip
  (80.6 °C), so no episode waited, and episodes from C01 on started at 55–61 °C instead of about 35 °C. Median
  diagnosis call time was unaffected (3.05 s against 3.01 s in the earlier runs), so throttling did not visibly
  slow the calls.
- **Belief is identical, tick for tick, to the earlier nudge runs** on all ten episodes. Belief takes no model
  input, so "belief alone" is the same reference throughout.
- **The model's replies are not fully repeatable.** 13–21% of replies differ between runs on identical prompts
  (NPU variation at temperature 0), so differences of a few hundredths are noise.

---

## 2. Results per episode

Published group top-1 (share of scored ticks, fault onset onward, whose top cause is in the true look-alike group):

| episode | true cause (group) | v3 | belief | v3 − belief | nudge (earlier) |
|---|---|---|---|---|---|
| A01 feed valve seizure | RCA-01 (with RCA-16) | **0.89** | 0.55 | +0.34 | 0.68 |
| A02 feed valve, fast | RCA-01 (with RCA-16) | **0.58** | 0.25 | +0.33 | 0.30 |
| A03 feed-pump suction | RCA-16 (with RCA-01) | **0.81** | 0.45 | +0.36 | 0.46 |
| B01 tube leak | RCA-11 | **1.00** | 0.55 | +0.45 | 0.50 |
| B02 tube leak, fast | RCA-11 | **1.00** | 0.86 | +0.14 | 0.88 |
| B03 tube leak, slow | RCA-11 | **0.60** | 0.29 | +0.31 | 0.30 |
| C01 wet coal (held-out) | RCA-06 → RCA-14 + RCA-18 | 0.63 | **0.98** | **−0.35** | 1.00 |
| C02 feeder trip | RCA-14 (with RCA-18) | 0.32 | **0.53** | **−0.21** | 0.55 |
| C03 wet coal, mild (held-out) | RCA-06 → RCA-14 + RCA-18 | 0.38 | **0.47** | −0.09 | 0.95 |
| D01 high-CV coal | RCA-07 | 0.83 | 0.82 | +0.01 | 0.82 |
| **mean** | | **0.703** | 0.575 | **+0.128** | 0.644 |

No-fault episodes: N01 had 0 false alarms and made no model calls; E01 made no model calls. Their state comes from
code and is unchanged.

---

## 3. All metrics (mean over the ten fault episodes)

| metric | v3 | belief | nudge (earlier) | reading |
|---|---|---|---|---|
| group top-1 | **0.703** | 0.575 | 0.644 | headline |
| exact top-1 (8 episodes; held-out excluded) | **0.600** | 0.450 | 0.467 | the exact case, not its look-alike |
| true group in top 3 | **0.908** | 0.722 | 0.792 | the right cause is on screen |
| MRR (top 3) | **0.798** | 0.636 | 0.706 | how high the right cause sits |
| ticks helped / harmed vs belief | 224 / 38 | – | 55 / 12 | net +186 against +43 |
| first right top cause, min after onset | **9.6** | 14.9 | 14.6 | faster recognition |
| settled (right for 10 ticks in a row), min | **9.8** | 15.4 | 15.1 | |
| right before the trip (A01, A02, A03, D01) | **0.76** | 0.41 | 0.48 | early warning |
| rank-1 changes per 100 ticks | 8.2 | 5.5 | **4.7** | v3 less stable |
| shown confidence, right / wrong | 0.54 / 0.41 | 0.65 / 0.56 | 0.65 / 0.50 | |
| confidence AUROC (right vs wrong) | 0.60 | 0.69 | **0.76** | v3 confidence less informative |
| faithfulness (cited facts that exist) | 0.974 | – | 0.975 | from the evaluator |
| unusable model answers | 0 | – | 0 | answer grammar |

Lead time (exact case first at rank 1, before the trip):

| episode | v3 | belief | nudge |
|---|---|---|---|
| A01 | **22.2 min** | −1.3 (after the trip) | 5.7 |
| D01 | **114.8 min** | 108.3 | 103.3 |
| A02 / A03 | −5.9 / −0.5 | never | never |

On A02 and A03 the exact case is almost never separated from its look-alike (exact top-1 0.06 and 0.11), so
"exact" lead time is late. Group-level they are right from 4 and 6 minutes after onset.

---

## 4. Finding 1: removing the group letters made the model itself far more accurate

The model's own first pick per diagnosis answer, read from the gate's answer records (independent of any merge
rule):

| family | old prompt (nudge runs): pick right | v3 prompt: pick right | change |
|---|---|---|---|
| A (feed faults, two-case group) | 0.69 | 0.66 | ≈ |
| B (tube leaks, one-case group) | **0.04** | **0.70** | +0.66 |
| C (fuel faults, two-case group) | 0.60 | 0.49 | −0.11 |
| D (high-CV coal, one-case group) | **0.02** | **0.70** | +0.68 |
| **all 500 answers** | **0.33** | **0.65** | ×2 |

| | old prompt | v3 prompt |
|---|---|---|
| answers whose first pick was a filler case (RCA-09/10/15) | 0.39 | **0.16** |
| answers where the true group was among the shown cases | 0.76 | 0.76 |

- **This is exactly the prediction made before the run** (reports, 7 October): with the letters, the model picked
  the most common letter in the case list (89% of answers). Faults whose true cause has a two-case group (A, C) got
  free hits; one-case groups (B, D) could never win. Removing the letters should lift B and D and could cost A and C
  some. It did both.
- **The cases shown didn't change** (0.76 both times), so the gain is in how the model chooses, not in what it sees.
- **Confound:** raw notes changed in the same run. Two things point to the letters as the main cause: the gain
  follows the letter pattern by family, and A02, whose prompts showed no notes at all, barely changed
  (0.65 → 0.63).

---

## 5. Finding 2: `hybrid` turns the better answers into published accuracy

Every scored tick carries its regime in the rule record:

| regime (belief's state) | ticks | belief right | published right | net ticks |
|---|---|---|---|---|
| clear leader → nudge | 511 | 0.79 | 0.84 | +23 |
| tie, the model led | 374 | 0.44 | **0.74** | **+112** |
| tie, no usable model pick (filler or none) → nudge | 104 | 0.02 | 0.13 | +12 |
| weak, the model led | 53 | 0.21 | **0.83** | +33 |
| weak, no usable model pick → nudge | 7 | 0.00 | 0.86 | +6 |

Most of the gain is in ties: where belief's top two are close, belief is right less than half the time and the
model, now without the letter shortcut, is right three times in four.

### 5.1 The same answers under other merge rules (offline replay)

The model's answers don't depend on the merge rule (proven for the diagnosticians), so other rules can be applied to
v3's answers exactly. Replaying `hybrid` reproduces what v3 published, with 0 ticks differing. Group top-1 here uses
the replay tool's per-tick definition (belief 0.573 against 0.575 in section 2).

| rule on v3's answers | mean | margin over belief | worst episode | pass rule |
|---|---|---|---|---|
| belief only | 0.573 | – | – | – |
| tiebreak | 0.588 | +0.014 | −0.018 | PASS |
| nudge | 0.622 | +0.049 | 0.000 | PASS |
| model (old merge: model order wins) | 0.653 | +0.079 | −0.349 | FAIL |
| **hybrid** (what ran) | **0.701** | **+0.127** | −0.349 | FAIL |
| hybrid, group-aware tie (section 6) | **0.729** | **+0.156** | −0.018 | **PASS** |

The old merge, which collapsed to 0.38 with the letters, now gives 0.653: the model's answers themselves are much
better. The guarded rules (`tiebreak`, `nudge`) pass but use little of that improvement.

---

## 6. Finding 3: the fuel-fault losses are a look-alike tie

All three C episodes have the same true group: **RCA-14 + RCA-18**, two cases with identical sensor signatures.
Belief therefore always gives them the same score, so its top two are always tied, and `hybrid` treats every C tick
as a tie and lets the model lead. On the ticks v3 got wrong (belief right), the model led with another cause:

| episode | harmed ticks, published cause | example |
|---|---|---|
| C01 | RCA-11 ×9, RCA-03 ×5, RCA-13 ×2 | tick 51: belief RCA-14 0.12 = RCA-18 0.12; the model ranked RCA-15, RCA-13, RCA-09 (filler skipped) |
| C02 | RCA-07 ×12, RCA-13 ×4 | tick 55: belief RCA-14 **4.0** = RCA-18 **4.0** (maximum certainty); the model ranked RCA-07 first |
| C03 | RCA-07 ×5 | tick 92: belief RCA-14 4.0 = RCA-18 4.0, RCA-07 at 0.35; the model ranked RCA-07 first |

In C02 and C03, belief was as certain as it can be, and both tied cases count as right. The "tie" was between two
equally right answers, and handing it to the model could only make things worse.

### 6.1 A group-aware tie, and its trade-off

The variant counts a tie only against the best rival **outside** the leader's look-alike group. Replayed:

| episode | belief | hybrid | group-aware hybrid |
|---|---|---|---|
| A03 | 0.441 | **0.796** | 0.452 |
| C01 | 0.977 | 0.628 | **0.977** |
| C02 | 0.526 | 0.316 | **0.526** |
| C03 | 0.473 | 0.382 | **0.455** |
| others | | unchanged | unchanged |
| mean, v3 answers | 0.573 | 0.701 (FAIL) | **0.729 (PASS)** |
| mean, earlier answers | 0.573 | **0.759 (PASS)** | 0.739 (PASS) |

The trade-off is A03, where belief wrongly ranked the RCA-14 + RCA-18 pair first. There the same look-alike tie
handed control to the model, which was right. So a look-alike tie at the top helps when belief has the wrong pair
on top (A03) and hurts when it has the right pair (C01–C03). Neither rule wins everywhere. Both were designed after
looking at these episodes, so the choice belongs to a dev run.

---

## 7. Other observations

**Stability.** The top cause changes 8.2 times per 100 ticks against 5.5 for belief, most on A02 (23.8) and A03
(16.7), the episodes where the model leads most. A hysteresis rule (the model must name the same new case on two
consecutive answers before it leads) would reduce this. It hasn't been tested.

**Shown confidence.** It measures belief's margin, so it is low exactly when the model leads correctly (A03: 0.42
when right against belief's 0.75), and high when belief is confidently wrong (C02: 0.58 when wrong). AUROC fell from
0.69 to 0.60. Two fixes: a group-level margin (so look-alike ties don't read 0.50) and a "model-led" marker.

**Raw notes.**
- Note lines did reach the prompts (e.g. 122 note lines across A01's prompts, 149 across B03's).
- No reader calls were made.
- Prompts got smaller overall, because the letters went and the reader's long tag lines were replaced by shorter
  raw lines.

The episodes where the ground truth lists notes as required (A01, A03, B01, B03, C01, C03) include both the biggest
gains (B01, B03, A03) and losses (C01, C03). This run can't separate the notes' effect from the letters'.

**Injection.** Both injection episodes (A02, C03) carry a note with instruction-like text. In neither run did
retrieval select it into a diagnosis prompt, so v3's resistance to injected notes is **untested**. The evaluator's
injection metric (T9) is identical in every run (C03: 73 ticks) because it counts ticks where the **code-computed**
state lags the truth, which the model never touches. It needs redefining to measure the model.

**Cost.**

| | v3 | nudge runs |
|---|---|---|
| model calls (10 fault episodes) | 609 | 647 |
| median diagnosis prompt | 731 tokens | 748 |
| median diagnosis call | 3.05 s | 3.01 s |
| total wall time | 1,927 s | 1,984 s |

Verifier calls varied by episode (0 to 29), because its trigger depends on the published confidence. The CPU lane
(Gemma) ran only the verifier, which never reorders.

---

## 8. Conclusions

1. **v3 is the best measured configuration on these episodes:** +0.128 over belief alone, +0.059 over nudge. It has
   much earlier recognition (right before the trip 0.76 against 0.41) and the right cause on screen 91% of the time.
   It is the first configuration where the model clearly adds information that belief lacks.
2. **Removing the group letters was the decisive change.** It removed a shortcut that made the model systematically
   wrong on one-case faults. Keep it in every configuration.
3. **`hybrid` is the right shape of rule:** let the model lead where belief can't separate the top causes. But its
   tie test must account for look-alike groups; as built, it fails the pass rule only because of the RCA-14/RCA-18
   pair. The group-aware variant passes on v3's answers but trades away A03, so the tie definition is the open
   design question.
4. **Nothing here is a claim yet.** These are reporting episodes, the rule and the variants were designed while
   looking at them, each episode ran once, and replies vary 13–21% between runs. The project's rule requires the
   decision on the dev set.

## 9. Recommended next steps

1. **Run v3 on the dev quick set,** and replay `nudge`, `hybrid` and the group-aware variant on those answers
   (group-aware first has to be built as a selectable rule). Choose by the pre-registered pass rule.
2. **Add the stability rule and the confidence fixes** (group-level margin, model-led marker), each tested on dev.
3. **Test injection properly:** force the poisoned note into the prompt (as `tests/test_raw_notes.py` already does
   on the mock), and redefine T9 to measure the published ranking, not the code state.
4. **Fix the cooling target** so a restarted batch can't adopt a hot reading (use the first batch's reading or a
   fixed target), and **merge the teammate's dropped-connection fix**.
5. **Separate the two prompt changes** (letters, raw notes) with one dev run each, if the team wants to know how
   much each contributes.

---

## Appendix: how the numbers were computed

- **Group top-1, top-3, MRR, help/harm, first/settled right, pre-trip, confidence:** every scored tick (fault onset
  onward, ranking published) of each run file, scored against `data/kb/case_groups.json` exactly as
  `bench/evaluator.t2_root_cause` does. Belief's shown confidence uses the gate's formula on belief's own ranking.
- **Model's own picks:** the first-ranked case of each diagnosis answer record (`multi.compact`, agent
  `diagnostician`), scored ticks only.
- **Regimes:** the `rule` record the gate writes every non-quiet tick (`regime`, `model_led`).
- **Rule replays:** `bench/replay_multi.py` on v3's run files. Faithful (0 differing ticks for `hybrid`). The
  group-aware variant was replayed by swapping the regime test; it is not in the code.
- **Faithfulness, actions, lead time, T9:** the evaluator's summaries in the test folder.
- The analysis scripts were one-off and are not in the repo.

---

## 10. Re-run of C01, C02, C03 (8 October, `_r2` files)

The three fuel-fault episodes were run again with `--again` (same code `c13a5f9`, same `configs/v3.yaml`; the `_r2`
files were later deleted at the team's request to keep the test folder to one run per episode). This
time they started with a cool chip (33–38 °C, against 58–60 °C in run 1), which tests whether the hot chip caused
the losses.

| episode | run 1 (hot chip) | run 2 (cool chip) | belief | identical diagnosis replies (identical prompts) | model's own first pick right, run 1 / run 2 |
|---|---|---|---|---|---|
| C01 | 0.63 | 0.67 | 0.98 | 21 of 24 | 14/24 / 15/24 |
| C02 | 0.32 | 0.32 | 0.53 | 38 of 47 | 21/46 / 21/47 |
| C03 | 0.38 | 0.36 | 0.47 | 17 of 26 | 12/26 / 11/26 |

- **The losses are reproducible and not caused by heat.** Every prompt was identical between the runs, 78% of the
  replies were identical (76 of 97, in line with the 13–21% run-to-run variation seen before), and the scores moved
  by at most 0.04.
- **The mechanism is the same in both runs:**
  - every scored C01 tick, and 46 of C02's, was a look-alike tie where the model led;
  - the harmed ticks were again the model leading with RCA-07 (high-CV coal), RCA-11 (tube leak), RCA-13 or RCA-03.
- **On these episodes the model's own first pick is a coin flip:** about half the time it names the right pair
  (RCA-14), otherwise another fuel or heat cause. A look-alike tie hands those coin flips to the published ranking.
- **Replayed on run 2's answers:** the group-aware tie gives belief's level on all three (C01 0.977, C02 0.526,
  C03 0.455), plain `hybrid` gives 0.674 / 0.316 / 0.364, and nudge gives 1.000 / 0.539 / 0.473.

So the conclusion of section 6 holds on a second, cool run. The C losses come from the tie definition, not from heat
or chance; the group-aware tie test (or nudge for look-alike ties) removes them, at the A03 cost described there.
