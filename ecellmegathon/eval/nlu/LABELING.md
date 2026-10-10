# Labeling rules

These rules decide every gold label in `cases.jsonl`. Anyone adding cases follows them; `validate_cases.py`
enforces the mechanical ones.

## What a turn's gold contains

- Only what the user said **in that turn**. Earlier turns are already in `form_before`.
- `update_report` when the turn adds or changes at least one field. `no_op` for questions to the assistant,
  noise, fillers and off-topic talk. `submit_report` / `cancel_report` when the user asks for it, even if the
  form is incomplete (the code refuses an incomplete submit; the model's job is only to recognise the intent).
- `x` (description) is never in gold. Code builds it from the user's words.

## Values

- **Free-text fields and list items are copied from the user's words** (an exact substring, any case). The
  validator rejects anything else. Other acceptable spans go in the case's `accept` list.
- **A later statement overwrites** a scalar field (corrections: "actually make it critical", "it's the UPS room,
  not the server room"). List fields append.
- **Enums** are mapped from explicit words only:

| field | words → value |
|---|---|
| `s` severity | minor / low / not serious → low; medium / moderate → medium; high / serious / bad → high; critical / very serious / emergency → critical. "not high, medium" → medium. Otherwise leave empty; never infer from injuries |
| `m` medical care | nobody/no one hurt → none; "gave first aid" → first_aid; doctor / clinic → doctor_or_clinic; emergency ward / ER / casualty → emergency_room; hospital / admitted / ICU / ambulance took them → hospitalised |
| `h` hazard status | still / is leaking / still loose → ongoing; isolated / cordoned / under control / contained → contained; out / fixed / cleared / they're out now → resolved |
| `n` injured count | only when a number is said or "nobody / no one hurt" (→ 0). Add parts when the speaker splits them ("one cook … nine others" → 10). A later subset ("five of them are in ICU", "three girls fainted") does **not** change `n` |

## Incident type `t`

- If someone was hurt (or nearly hurt), the **injury mechanism** decides: `slip_trip`, `fall_height`,
  `struck_by`, `caught_in`. A ladder fall while fixing a leak is `fall_height`.
- `fire` whenever there were flames or smoke, even if a gas leak started it. `gas_leak` only without ignition.
- `chemical` covers spills, splashes and fumes. `structural` covers ceilings, walls and scaffolding that collapse
  onto people. `equipment_failure` is a breakdown with nobody hurt. `security` covers theft and intrusion.
- Two incidents in one turn: label the first one; the second does not change `t`.
- When the type really is ambiguous, the gold has one value and `accept.t` lists the others.

## Location and time

- `l` is the most specific place said ("meter room on the ground floor", not "ground floor").
- `d` is the time phrase as said ("around 3, no wait, closer to 3:30" → "closer to 3:30"). `gold_time` holds the
  resolved timestamp for `timeparse.py` relative to the case's `ref_time`; it is absent when the phrase is vague
  ("this morning", "yesterday night").

## `asked`

Generated, not hand-written: the first missing mandatory field in the order t → l → d → s, or null for the first
turn and once the form is complete. In free-running mode the system may ask something else; the scripted user
still says the scripted line, and the dialog is scored on its final form.

## Splits

`demo` (the problem-statement example, used for prompt examples and rehearsals) · `dev` (tune on these) ·
`test` (locked: don't read failures from it until the M4 decision rules are written down).
