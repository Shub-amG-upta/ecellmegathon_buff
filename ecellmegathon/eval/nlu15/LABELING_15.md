# Labelling rules: the 5 extra fields (15-field form)

The first 10 fields follow `eval/nlu/LABELING.md` unchanged, and every original label is checked to stay
the same. This file covers only the 5 fields that `schema/incident_form_15.json` adds. All 5 are optional.
As with the other free-text fields, a value is copied from the user's own words in that turn.

| key | field | kind | standard it comes from |
|---|---|---|---|
| `g` | task | text ≤60 | OSHA Form 301, item 14: what the employee was doing just before the incident |
| `c` | immediate_cause | text ≤60 | Factories Act 1948, Form 18 (cause of accident); immediate cause in incident investigation |
| `k` | property_damage | list, 3 × ≤60 | ISO 45001: an incident includes damage to property and equipment |
| `h` | hazard_status | `controlled` / `ongoing` | the "area made safe / hazard contained" box on incident forms |
| `n` | reported_to | list, 4 × ≤40 | Factories Act 1948 s.88 (notice of accidents); internal EHS escalation |

## Rules

**g, task.** This is what the person was doing *before* it happened ("installing panels", "scanning
packages"), not the event itself. The label is left empty when the speaker doesn't say what they were doing.
When the speaker describes a passer-by's movement ("going home", "standing behind the trailer"), that counts
as the task.

**c, immediate_cause.** This is the condition or act the speaker *names* as leading to the incident: "the
machine was not locked out", "wet floor", "LPG leak", "probably overloaded". Never infer a cause. If the
speaker gives only the event ("fell from the ladder"), `c` stays empty. A hedge ("probably", "looks like")
is copied along with the cause.

**k, property_damage.** Damage to property or equipment, as stated: "the forklift mast is bent", "Ceiling
fans are damaged". A stolen item is not damage. Damage done during a break-in is ("broke open the window").
An injury never goes here.

**h, hazard_status.**
- `controlled`: the speaker says the source is stopped, made safe or over. Examples: "isolated", "fire is
  out", "switched off", "they're out now", "line is depressurised", "blocked off that bay".
- `ongoing`: the speaker says it is still happening. Examples: "still leaking", "still loose", "smoke
  coming", "everyone is evacuating", "is stuck".
- Nothing stated means no label. A later turn can change it: `lw-01` is ongoing in turn 0 and controlled
  in turn 2.

**n, reported_to.** This is a person, team or service that has been informed or called: "the ambulance",
"108", "EHS", "the police", "the plant manager". The action itself still goes in `a` (immediate_actions) under
the 10-field rules, so "called the ambulance" gives `a` "called the ambulance" and `n` "the ambulance".
A responder who only arrives doesn't count.

## No_op turns that change

Two turns had nothing for the 10 fields and were `no_op`. They now carry a new field and become `update_report`:

- `fk-03` turn 3, "the forklift mast is bent" → `k`
- `lf-03` turn 2, "they're out now, nobody hurt" → `h` controlled

These are the only tool changes. `build_cases15.py` refuses any other change.
