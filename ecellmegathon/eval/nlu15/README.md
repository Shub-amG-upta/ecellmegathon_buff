# Text eval set: 15 fields (dataset branch)

The problem statement's 10 fields plus 5 industry-standard context fields (`schema/incident_form_15.json`):
task `g`, immediate_cause `c`, property_damage `k`, hazard_status `h` and reported_to `n`. This is a separate
version. The 10-field set (`eval/nlu/`), its locked test split and every benchmark stay as they are.

```
eval/nlu15/build_cases15.py   imports the 65 dialogs from eval/nlu/build_cases.py, adds labels for the 5 fields,
                              adds 15 OSHA-based dialogs, checks everything, writes cases.jsonl
eval/nlu15/cases.jsonl        generated, same line format as eval/nlu/cases.jsonl
eval/nlu15/LABELING_15.md     rules for g, c, k, h, n (the 10 fields follow eval/nlu/LABELING.md)
eval/nlu15/SOURCES_15.md      OSHA Severe Injury Report IDs for the 15 new dialogs
```

```
.venv/Scripts/python eval/nlu15/build_cases15.py
```

## What's in it

| | |
|---|---|
| cases / turns | 80 / 323 (65 / 250 original + 15 / 73 new) |
| splits | demo 1 · dev 53 · test 26 (test is locked) |
| field coverage (turns) | t 79 · s 80 · l 81 · d 78 · p 42 · e 45 · a 49 · w 9 · **g 28 · c 32 · k 5 · h 33 · n 17** |
| new dialogs | 15 rewrites of OSHA Severe Injury Reports 2023–2025, set in Indian workplaces |

The builder refuses to write if any of these fail:
- a new value isn't copied from that turn's words;
- an enum value is wrong, or a length or item limit is broken;
- any original turn's 10-field gold changed;
- any tool changed other than the 2 documented `no_op` → `update_report` turns.

## Not wired in yet

The app, the prompt and `eval/nlu/run_eval.py` still use the 10-field schema. Scoring the model on this set
needs Track A changes: the schema path, the grammar and prompt for the new keys, and its decode cost (project rule:
measure before adding output fields). Whether to adopt it is Track A's call.
