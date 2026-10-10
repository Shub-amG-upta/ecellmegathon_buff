# Labeling rules

These rules decide every gold label in `cases.jsonl`. Anyone adding cases follows them; `validate_cases.py`
enforces the mechanical ones.

## The form: the problem statement's 10 fields

| key | field | filled by |
|---|---|---|
| `t` | incident type | LLM |
| `s` | severity | LLM |
| `l` | location | LLM |
| `d` | date & time | LLM (the phrase); code resolves it |
| `p` | persons involved | LLM |
| `e` | equipment / asset involved | LLM |
| `a` | immediate actions taken | LLM |
| `w` | witnesses | LLM |
| — | description | code, from the user's own words |
| — | recommended follow-up | code, from `data/followup_catalogue.json` by type |

Anything else the user says (how many were hurt, the injury, the cause, damage, whether the hazard is still
there) is not a field. It still reaches the report through the description, which is the user's own words.

## What a turn's gold contains

- Only what the user said **in that turn**. Earlier turns are already in `form_before`.
- `update_report` when the turn adds or changes at least one of the 8 LLM fields. `no_op` for questions to the
  assistant, noise, fillers, off-topic talk, and statements that touch none of the 8 fields ("she's in hospital
  now", "the forklift mast is bent"). `submit_report` / `cancel_report` when the user asks for it, even if the
  form is incomplete (the code refuses an incomplete submit; the model's job is only to recognise the intent).
- Description and follow-up are never in gold. Code builds them.

## Values

- **Free-text fields and list items are copied from the user's words** (an exact substring, any case). The
  validator rejects anything else. Other acceptable spans go in the case's `accept` list.
- **A later statement overwrites** a scalar field (corrections: "actually make it critical", "it's the UPS room,
  not the server room"). List fields append.
- **Severity** is mapped from explicit words only: minor / low / not serious → low; medium / moderate → medium;
  high / serious / bad → high; critical / very serious / emergency → critical. "not high, medium" → medium.
  Otherwise leave it empty; never infer it from injuries.

## Actions taken `a`

- **Anything done in response is an action**, whoever did it: "isolated the area", "called the ambulance",
  "Security is trying to open it", "Facilities is checking the pipeline" → `a`.
- **Telling or calling someone is an action**: "EHS has been informed", "I have informed security" → `a`.
- **Sending someone for care is an action**: "sent to the government hospital" → `a`.
- **A responder only arriving or transporting** ("the fire brigade is on the way", "the ambulance took them")
  is not required, but it goes in `accept.a` so a model that lists it is not marked wrong.
- **A status or outcome is not an action**: "she's in hospital now", "they're out now" → nothing (`no_op` if the
  turn holds nothing else).

## People: `p` and `w`

- `p` is everyone who took part: the people hurt or nearly hurt, and the people who helped on the spot
  ("Two colleagues gave CPR" → `p`).
- `w` is only people said to have **seen** it ("Anil and I both saw it" → `Anil`; "the receptionist Meena saw it
  fall"). The speaker is never a witness entry. "Nobody else saw it" leaves `w` empty.
- Roles are fine as values ("one housekeeping staff", "the guard Mahesh"); copy them as said.

## Incident type `t`

- If someone was hurt (or nearly hurt), the **injury mechanism** decides: `slip_trip`, `fall_height`,
  `struck_by`, `caught_in`. A ladder fall while fixing a leak is `fall_height`.
- `fire` whenever there were flames or smoke, even if a gas leak started it. `gas_leak` only without ignition.
- `chemical` covers spills, splashes and fumes (oil included). `structural` covers ceilings, walls, glass and
  scaffolding that crack or collapse. `equipment_failure` is a breakdown with nobody hurt. `security` covers theft
  and intrusion. `medical` is someone taken ill with no other cause.
- Two incidents in one turn: label the first one; the second does not change `t`.
- When the type really is ambiguous, the gold has one value and `accept.t` lists the others.

## Location and time

- `l` is the most specific place said ("meter room on the ground floor", not "ground floor").
- `d` is the time phrase as said ("around 3, no wait, closer to 3:30" → "closer to 3:30"). `gold_time` holds the
  resolved timestamp for `timeparse.py` relative to the case's `ref_time`; it is absent when the phrase is vague
  ("this morning", "yesterday night"). A clock time without AM/PM means its most recent past occurrence
  ("at 2:15" said at 3 PM → 14:15).

## `asked`

Generated, not hand-written: the first missing mandatory field in the order t → l → d → s, or null for the first
turn and once the form is complete. In free-running mode the system may ask something else; the scripted user
still says the scripted line, and the dialog is scored on its final form.

## Splits

`demo` (the problem-statement example, used for rehearsals) · `dev` (tune on these) · `test` (locked: don't read
failures from it until the M4 decision rules are written down).
