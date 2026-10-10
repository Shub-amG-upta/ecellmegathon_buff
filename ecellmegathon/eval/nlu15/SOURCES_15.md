# Sources: 15-field set

The 65 original cases keep their sources (`eval/nlu/SOURCES.md`, S00–S17 and H). The 15 new cases are
rewritten from **OSHA Severe Injury Reports** (SIR), the employer-reported records of amputations and
in-patient hospitalisations that OSHA publishes as a downloadable dataset (2015 to the latest release).
Each is a US federal government work and in the public domain.

Dataset: https://www.osha.gov/severe-injury-reports (CSV download; this set uses records from 2023 to 2025).

## How the cases were made

- Facts come from the record's narrative: what the person was doing, what happened, the equipment, the cause
  and the injury.
- The wording, the Indian workplace setting, place names and times are ours. Each case is a first-person
  spoken report to a safety desk.
- SIR narratives seldom say what happened after the injury. So the follow-up turns (actions taken, who was
  informed, whether the hazard is controlled) are **written by us** to exercise `a`, `h` and `n`. Treat them
  as plausible, not sourced.
- Where we changed a fact, the case's `notes` says what changed (for example a softened outcome or a
  changed setting).

## Cases

| code | OSHA SIR ID | what the record describes | case | split | type |
|---|---|---|---|---|---|
| S18 | 2023054383 | cleaning below a planer, hand caught in the blade, fingers amputated, machine not locked out | pl-01 | dev | caught_in |
| S19 | 2024021859 | adjusting dams between roll-coater rollers, slipped while kneeling, arm drawn into the nip point | rc-01 | dev | caught_in |
| S20 | 2023087453 | removing a seal from a chiller drum, safety guard snapped back, fingertip amputated | cg-01 | test | caught_in |
| S21 | 2025044053 | pipe bundle loaded by forklift slid off a trailer onto two workers behind it | pb-01 | dev | struck_by |
| S22 | 2024032363 | reversing forklift struck a worker scanning packages and ran over her legs | fr-01 | dev | struck_by |
| S23 | 20241110761 | pickup truck struck a worker marking damaged concrete on a parking deck | pg-01 | test | struck_by |
| S24 | 2025043122 | fell about 18 ft while moving a ladder to get onto a roof | rl-01 | dev | fall_height |
| S25 | 2024010658 | arc flash making secondary connections while restoring power after a storm | af-01 | dev | electrical |
| S26 | 2023010979 | shock removing wiring from fire-pump switchgear, co-worker pulled him free, hand lacerations | sg-01 | test | electrical |
| S27 | 2023064947 | flash fire from a lit cigarette while a gas line was venting after maintenance | gw-01 | dev | fire |
| S28 | 2025066036 | extinguisher horn came off and the hose struck an operator fighting a machine fire | fx-01 | dev | struck_by (fire accepted) |
| S29 | 2023064861 | inflating a tyre on a rim damaged by bearing failure, tyre, rim and hub blew off, about 1 AM | ty-01 | dev | struck_by |
| S30 | 20251110924 | HDPE pipe joint released at 140 PSI during a pressure test | pt-01 | test | struck_by |
| S31 | 2023010707 | slipped in a cooler holding a leaning milk container, about 6:30 PM | mk-01 | dev | slip_trip |
| S32 | 2024032675 | hot water gushed out while cutting out a valve on a frozen-isolated heating line, burns | hw-01 | test | other (water_leak accepted) |

Split: 10 dev, 5 test. The test cases were chosen before any model was run on them. They stay locked, the
same as the original test split.
