# Text eval set v1 (NLU)

The extraction model's eval set: 65 scripted dialogs, 250 user turns, labelled for the problem statement's
**10-field form** (`schema/incident_form.json`), with a gold tool call for every turn. 52 of the 65 cases are
rewrites of real, publicly reported incidents (OSHA accident summaries and Indian news reports); every case links
to its source. The other 13 are hand-written (source `H`) to cover gaps, such as witnesses.

```
eval/nlu/build_cases.py        source of truth; edit cases here, then run it to regenerate cases.jsonl
eval/nlu/cases.jsonl           one dialog per line (generated; regenerating is byte-identical)
eval/nlu/validate_cases.py     schema + copy-from-transcript checks, coverage, run-time estimate
eval/nlu/LABELING.md           the rules behind every gold label
eval/nlu/SOURCES.md            the source for every case
```

```
.venv/bin/python eval/nlu/build_cases.py && .venv/bin/python eval/nlu/validate_cases.py
```

## What's in it

| | |
|---|---|
| splits | demo 1 · dev 43 · test 21 (test is locked) |
| turn tools | update_report 177 · submit_report 64 · no_op 7 · cancel_report 2 |
| incident types | all 15; fire 7, water_leak 7, slip_trip 7, chemical 6, structural 5, … |
| field coverage (turns) | t 64 · s 65 · l 66 · d 63 · p 27 · e 31 · a 39 · w 9 |
| traps | negation, corrections, severity changes and negations, two incidents in one turn, premature submit, questions and noise that must be `no_op`, "nobody else saw it" |

## Changes from v0 (17 fields)

- The 9 fields outside the problem statement's 10 were removed from every label (injured count, injury, body
  part, medical care, activity, cause, notified, hazard status, damage). That information still reaches the
  report through the description.
- One rule for actions (LABELING.md): a response, a notification, or sending someone for care is an action
  taken; a responder only arriving is accepted but not required. Fixes `lf-03` (its only action was lost),
  `ch-03` / `sec-03` (informing someone was labelled two ways), `gs-03`.
- Turns left with no 10-field content became `no_op` ("she's in hospital now", "the forklift mast is bent",
  "they're out now"), except "sent to the government hospital", which is an action.
- `md-01`: the colleagues who gave CPR are persons involved, not witnesses.
- 9 hand-written witness dialogs (`wit-01..09`): witness turns went from 2 to 9.

## One case line

```json
{"id": "ps-01", "source": "S00", "split": "demo", "tags": [...], "ref_time": "2026-10-10T15:00:00+05:30",
 "turns": [{"i": 0, "asked": null, "user": "There was a water leak near ...", "form_before": {},
            "gold": {"tool": "update_report", "args": {"t": "water_leak", "l": "near Conference Room 2", ...}},
            "gold_time": "2026-10-10T10:00:00+05:30"}, ...],
 "gold_final": {...}, "final_tool": "submit_report", "accept": {"l": ["Conference Room 2"], ...}}
```

## Running it (`eval/run_eval.py`)

1. **Teacher-forced** (per turn): set the form to `form_before`, send `Asked:` + `User:`, compare the tool call
   to `gold`. Measures extraction (E4) and mapping (E5) without errors cascading.
2. **Free-running** (per dialog): start from an empty form, feed the scripted user lines through the real
   dialog loop, compare the final form to `gold_final`. Measures the conversation (E2).
3. Score with the rules in `LABELING.md`: enums exact (or in `accept`), strings by normalised containment
   against gold or `accept`, lists per item, plus extra values (not in gold or `accept`) after the gate.

Run-time and answer-length estimates: `validate_cases.py` prints them (ESTIMATE, from BOARD screening rates; the
longest gold answer is about 72 tokens).
