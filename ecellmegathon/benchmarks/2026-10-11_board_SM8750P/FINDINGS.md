# Findings from the board runs of 2026-10-11, and how to raise accuracy

For Shubham and Sarthak. Built from Sarthak's three board runs (QIDK SM8750P, Llama 3.2 3B pure Q4_0 on the NPU,
dev split, 157 turns, 44 dialogs). The full, generated reports are next to this file:

- `ANALYSIS_v1_run1.md`: v1 (no fast path): **every turn went to the LLM**, so this is the model's own accuracy.
- `ANALYSIS_v2_run1.md`: v2 (fast path on, context 2048): the system as it runs now.

Regenerate for any run: `.venv/bin/python benchmarks/analyze.py <run.json> [--compare <other.json>] [--log <monitor.log>] --out <file.md>`.

Labels: **BOARD** = counted from Sarthak's run files. **PROJECTION** = run 1's real model outputs with a proposed
code change applied offline, not yet run on the board. Nothing here is estimated otherwise.

---

## 1. The headline (BOARD)

| | v1 #1 | v2 #1 = v2 #2 |
|---|---|---|
| fields right (per turn) | 159/242 (66%) | 159/242 (66%) |
| dialogs ended right / exactly right | 27/44 / 2 | 29/44 / 2 |
| turns with an extra value | 71 | 51 |
| LLM calls / answered by code | 157 / 0 | 68 / 89 |
| LLM time per turn, average over all 157 turns | 1,141 ms | **716 ms (−37%)** |

**Speed is good; accuracy is the problem.** v2's fast path removed 37% of LLM time with no change in accuracy,
because the LLM was already right on those easy turns.

## 2. Patterns (BOARD, from `ANALYSIS_v1_run1.md`)

**P1. The model is very good on short answers and poor on long narrations.**

| turns | fields right | turns with an extra value |
|---|---|---|
| ≤ 5 words (101 turns) | **100%** | 23% |
| 6–15 words | 57% | 71% |
| 16–30 words | 61% | 94% |
| > 30 words | **51%** | 100% |
| first turn of a dialog (44) | **57%** | 86% |
| later turns (113) | 90% | 29% |

Almost every error is in the **first, long narration**. The more fields a turn holds, the more the model drops or
misplaces: 91% right with 0–1 fields, 57% with 4–5, 49% with 6+.

**P2. On long turns it leaves out mandatory fields it was told.** Time left out 11 times ("Last night around
8 PM…", "about twenty minutes ago"); severity left out 6 times even when said outright ("This is critical",
"Medium severity", "I'd say severity high"). This is also why **17 dialogs never finished**: the app asks for the
missing field, and the scripted user's next line doesn't answer it.

**P3. The list fields are weak:** persons 39%, equipment 30%, actions 44%, witnesses 17%. The kinds:

| field | main errors |
|---|---|
| equipment | items missing 5, reworded/extra 6; places put here ("meter room") |
| persons | services put here ("ambulance", "hospital"); witnesses put here (3 of 6 witnesses) |
| actions | left out 6; events put here instead of actions ("tripped over the metal strip") |
| witnesses | 3 of 5 errors are "put in persons" |

**P4. Locations get cut short** (6): "ground floor" instead of "meter room on the ground floor", "6th floor"
instead of "pantry on 6th floor". The model keeps the floor and drops the room.

**P5. Incident type confusions** (13 of 43 wrong): slip/trip → fall from height (2), struck-by → fall from
height (2), theft → "lift" / "equipment failure" (2), electrical / chemical / fire confused with neighbours.

**P6. Extra values: 143, of three very different kinds.**

| kind | count | what it means |
|---|---|---|
| said, but not labelled | 75 | every word was said. **Some are model over-extraction** ("ambulance" as a person, "meter room" as equipment), **some may be gaps in our labels** ("metal cabinet" he hit, the "rack" that fell, "coworkers" who helped: by LABELING.md helpers *are* persons involved) |
| repeats the form | 44 | the same value again: harmless, but counted |
| wrong field / partly made up / type not stated | 24 | real errors |

**P7. Tool errors are rare (4/157)**: all are "no_op" turns where the model still wrote something ("she's in
hospital now" → persons).

**P8. Latency.** Writing the answer is **88% of LLM call time** at **48.3 ms per output token (≈ 20.7 tok/s)**.
Prompt caching works: a call prefills a median of only **36 new tokens**. First turns take **2.0 s** median
(long answers, median 17 / max 82 output tokens overall); later turns **0.73 s**.

**P9. Heat.** The NPU runs at **≥ 95 °C for most of every run** (peak ~106 °C). In the monitor log, calls at
≥ 95 °C wrote 20.6 tok/s vs 24.4 tok/s below 60 °C (observed, not controlled). One earlier attempt crashed the
NPU link (`dspqueue_read failed`) after 4 minutes.

**P10. Determinism.** v2 #1 and v2 #2 gave **identical** output on all 68 LLM turns; v1 vs v2 on the same turns
differ on 2/68 (context 4096 vs 2048). Context size made no measurable speed difference on those turns
(1,688 vs 1,654 ms mean).

---

## 3. Ideas to raise accuracy, ranked by what the data says they fix

| # | idea | targets | expected effect | cost |
|---|---|---|---|---|
| A1 | **Severity fallback in code:** when the model leaves severity out but the user stated it ("this is critical", "medium severity", "serious incident"), code fills it | P2 | **6 of 6** severity omissions recovered, **0 wrong** (PROJECTION) → most "stuck on severity" dialogs finish | none |
| A2 | **Time fallback in code:** when the model leaves time out, code takes the time phrase from the sentence ("Last night around 8 PM") | P2 | **7 of 11** recovered (PROJECTION); a first prototype wrongly took "now" from "she's in hospital now", so bare "now" must be excluded and re-checked to 0 wrong | none |
| A3 | **Prompt v2: precise field definitions with "never" rules**: equipment = objects/machines, never places; persons = people, never services or buildings; witnesses = people who *saw* it; actions = what someone *did* in response, never what happened; copy the **full place** | P3, P4, P6 | the biggest lever for the list fields and locations; measure on dev | a few prompt tokens, cached |
| A4 | **Prompt v2: type rules with examples** for the confused pairs (slip vs fall from height vs struck-by; theft = security) | P5 | up to 13 type errors | as A3 |
| A5 | **Two-step extraction for long narrations only** (> ~20 words): one call for type/location/time/severity, one for persons/equipment/actions/witnesses, each with a narrowed grammar | P1 | smaller tasks for a 3B model; costs one extra prefill on first turns only (~0.1 s, the prefix is cached) | 1 h; measure |
| A6 | **Label audit** (human, below): fix genuine gaps by LABELING.md's rules, record every change | P6 | the score reflects real errors; must be done by rule, not to make the model look right | 1 h for two people |
| A7 | **Drop repeated values in code** and count them separately in the scorer | P6 | removes 44 harmless "extras" from the metric (disclosed as a metric change) | 15 min |
| A8 | **Stricter gate** (every word of a value must be said) | P6 | small: drops 3 wrong values and 1 right one (PROJECTION) | low priority |
| A9 | **Another 3B-class model** (e.g. Qwen2.5 3B Instruct), only if A1–A5 leave accuracy low | all | unknown; needs a pure Q4_0 build and a decision rule first | friend's time |

Suggested order: **A1, A2, A7 now** (code, no accuracy risk), **A3 + A4** (prompt v2, dev split only), then
**run v3 on the board** with `run_bench.py --tag v3`, compare with `compare.py` and `analyze.py`. A5 only if
first-turn accuracy is still low. Decision rules go in `eval/LLM_DECISION.md` **before** v3 runs.

---

## 4. What we two should review (to find patterns the script can't judge)

Open `ANALYSIS_v1_run1.md` and expand the tables.

1. **The 75 "said, but not labelled" values (§3 of the report).** For each one, mark: **label gap** (by
   LABELING.md it should have been labelled) · **wrong field** · **not a person / object / action at all**.
   If label gaps are more than a handful, our labels are too narrow and A6 matters.
2. **The 13 type errors (§5).** For each pair, is the label right by LABELING.md's rules, or is the case really
   ambiguous (then add the other type to `accept.t`)?
3. **The 6 "cut short" locations.** Is the shorter place good enough for a real report? If yes, add it to
   `accept.l`; if not, A3 must fix it.
4. **The 17 unfinished dialogs (§6).** Confirm they're the first-turn omissions (P2) and not something else.
5. **Wording patterns behind time and severity omissions.** Do they come at the start of the sentence, after a
   long clause, in Indian English phrasing? This decides whether A1/A2 rules or the prompt is the better fix.
6. **Heat.** Can we put a fan on the board for the next runs? Every run sat at ~105 °C.

Rules for any label change: decide by LABELING.md, both agree, write the reason in the case's `notes`, and apply
the same rule to the test split **without looking at model output on it**.
