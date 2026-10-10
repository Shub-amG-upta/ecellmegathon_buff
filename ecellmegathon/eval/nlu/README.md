# Text eval set v0 (NLU)

> **Status:** labelled against `schema/incident_form_v2.json` (draft, 17 fields). The app still runs on the
> frozen v1 form (`schema/incident_form.json`, 8 fields). Adopting v2 is a contract change both tracks must agree
> on (BUILD_PLAN §3) before `app/` and `eval/run_eval.py` use this set.

A starter dataset for the extraction model: 56 scripted dialogs, 219 user turns, a 17-field form, and a gold
tool call for every turn. 52 of the 56 cases are rewrites of real, publicly reported incidents (OSHA accident
summaries and Indian news reports); every case links to its source.

```
schema/incident_form_v2.json   the form: 17 LLM-filled fields + description (code), enums, mandatory, ask order
eval/nlu/build_cases.py        source of truth; edit cases here, then run it to regenerate cases.jsonl
eval/nlu/cases.jsonl           one dialog per line (generated)
eval/nlu/validate_cases.py     schema + copy-from-transcript checks, coverage, run-time estimate
eval/nlu/LABELING.md           the rules behind every gold label
eval/nlu/SOURCES.md            the source for every case, and for every form field
```

```
python eval/nlu/build_cases.py && python eval/nlu/validate_cases.py
```

## What's in it

| | |
|---|---|
| splits | demo 1 · dev 37 · test 18 (test is locked) |
| turn tools | update_report 158 · submit_report 55 · no_op 4 · cancel_report 2 |
| incident types | all 15; fire 6, water_leak 7, slip_trip 6, chemical 5, … other 1 |
| field coverage | every one of the 17 fields is exercised; weakest are `w` witnesses (2) and `k` damage (6) |
| traps | negation (8), corrections (3), severity changes and negations, count traps, two incidents in one turn, premature submit, questions and noise that must be `no_op` |

## One case line

```json
{"id": "ps-01", "source": "S00", "split": "demo", "tags": [...], "ref_time": "2026-10-10T15:00:00+05:30",
 "turns": [{"i": 0, "asked": null, "user": "There was a water leak near ...", "form_before": {},
            "gold": {"tool": "update_report", "args": {"t": "water_leak", "l": "near Conference Room 2", ...}},
            "gold_time": "2026-10-10T10:00:00+05:30"}, ...],
 "gold_final": {...}, "final_tool": "submit_report", "accept": {"l": ["Conference Room 2"], ...}}
```

## Running it (what `run_eval.py` should do)

1. **Teacher-forced** (per turn): set the form to `form_before`, send `Asked:` + `User:`, compare the tool call
   to `gold`. Measures extraction (E4) and mapping (E5) without errors cascading.
2. **Free-running** (per dialog): start from an empty form, feed the scripted user lines through the real
   dialog loop, compare the final form to `gold_final`. Measures the conversation (E2).
3. Score with the rules in `LABELING.md`: enums exact (or in `accept`), strings by normalised containment
   against gold or `accept`, lists per item, plus hallucination (a value not in gold and not in the turn's
   words), before and after the gate.

**Time (ESTIMATE from BOARD screening rates, not yet measured on this task):** one pass of all 219 turns takes
about 6 min on Llama 3.2 3B with the system prompt cached, 10 min without. Teacher-forced + free-running, each run
twice for the temperature-0 drift, is **24–40 min**. Adding one teacher-forced pass on Qwen3 1.7B (4–7 min) for
the model comparison fills the 40–50 min slot. Replace the rates in `validate_cases.py` with the M0 measurement.

The longest gold output is about 106 tokens, so raise the LLM cap from ~80 to **150 tokens** (`n_predict`).
At ~50 ms per token on the 3B, a full first narration costs ~5 s of decode.
