# PEOPLEFIX (persons and actions rules): projection and the board check, written before any board run

Written 2026-10-11. `app/postfix.py` "persons and actions", default on, `PEOPLEFIX=off` turns it off.

## Rules
1. A person or witness must be a person: cut at the first clause ("Three workers are badly injured" -> "Three
   workers"); drop it when only a pronoun, an injury or a body part is left ("he wasn't wearing goggles", "we gave
   him water", "fitter's legs"). A value with none of these signs is kept as it is.
2. First description, no person from the model: "our ..." persons ("Our lineman was ...", "one of our packers")
   and possessives ("the operator's eyes").
3. Actions run to the end of their clause ("flushed his eyes for fifteen minutes", "informed the police and our
   security team"), stopping at a new subject or verb, at till / until / when, and at Hinglish "aur".
4. No action from the model: what a person says was done ("we hit the emergency stop", "IT is shutting down the
   rack"), not what a machine did.

## Safety checks (LAPTOP, ORACLE: the gold answers through gate + fixes)
Every gold answer unchanged: eval/nlu all 250 turns, eval/nlu15 all 323 turns (both include the test split),
eval/hinglish 38 turns: field 1.0, 0 extra values. Tests keep every gold person and witness as it is.

## PROJECTION (saved BOARD answers replayed through this code; replay reproduced every saved score exactly)
| run | fields before → after | persons | actions | turns with an extra value |
|---|---|---|---|---|
| v7fix-router (eval/nlu dev, 44 dialogs) | 202 → **210**/242 | 9 → 13/18 | 20 → 24/27 | 30 → 25 |
| v9-form10-nlu15 (54 dialogs) | 250 → **266**/309 | 12 → 22/28 | 22 → 28/35 | 42 → 36 |

0 right→wrong in both. Caveat: the rules were written after reading the remaining errors of both runs,
including the 10 nlu15 dialogs that were unseen before; they are no longer a clean holdout.

## Board check (1 run, v9 + TYPE_MODEL, eval/nlu dev)
Keep PEOPLEFIX on if the run gives fields **≥ 208/242**, tool 157/157, ended right ≥ 42/44, and no field below
the v7fix-router run. The model's answers don't change (code only), so a miss means the replay was wrong: report it.
