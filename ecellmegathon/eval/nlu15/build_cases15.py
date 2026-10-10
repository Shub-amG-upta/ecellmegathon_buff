#!/usr/bin/env python3
"""The 15-field text eval set (schema/incident_form_15.json). Writes cases.jsonl next to this file.

  .venv/Scripts/python eval/nlu15/build_cases15.py

Built from the 10-field set, never by editing it:
  1. the 65 dialogs of eval/nlu/build_cases.py are imported as they are;
  2. EXTRA adds labels for the 5 new fields (g, c, k, h, n) to existing turns, by the rules in LABELING_15.md;
  3. NEW adds 15 dialogs rewritten from OSHA Severe Injury Reports (2023-2025, public domain; SOURCES_15.md);
  4. every case is expanded with the same logic as the 10-field builder (asked, form_before, gold_final),
     then checked: every new text value is copied from that turn's words, enums and limits hold, and each
     original turn's 10-field gold is unchanged apart from the documented no_op -> update_report turns.
Stdlib only.
"""
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
_spec = importlib.util.spec_from_file_location("build_cases10", HERE.parent / "nlu" / "build_cases.py")
B10 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B10)                      # fills B10.CASES; writes nothing (main is guarded)

SCHEMA = json.loads((ROOT / "schema" / "incident_form_15.json").read_text(encoding="utf-8"))
FIELDS = {f["key"]: f for f in SCHEMA["fields"] if f.get("key")}
NEW_KEYS = ("g", "c", "k", "h", "n")
LIST_FIELDS15 = {k for k, f in FIELDS.items() if f["kind"] == "text_list"}

# ---------------------------------------------------------------- 1. labels for the 5 new fields
# (case id, turn index) -> args added to that turn. Values are copied from the turn's words.
EXTRA = {
    ("ps-01", 0): {"h": "controlled"},
    ("ps-02", 0): {"h": "controlled"},
    ("el-01", 0): {"g": "installing panels", "c": "The cover was off and one live wire was hanging loose",
                   "n": ["the ambulance"]},
    ("el-03", 4): {"c": "the panel cover was open and a live wire was hanging"},
    ("sl-01", 0): {"g": "preparing medical equipment", "c": "the wet floor"},
    ("sl-02", 0): {"c": "wet floor"},
    ("sl-03", 0): {"c": "floor was wet after mopping"},
    ("na-01", 0): {"g": "showering a resident", "c": "the metal strip", "h": "ongoing"},
    ("na-02", 0): {"h": "controlled"},
    ("fk-01", 0): {"g": "got down to straighten it", "c": "hit the stacked material", "n": ["an ambulance"]},
    ("fk-02", 0): {"g": "reversing the forklift out of the warehouse", "c": "turn it around with the forks up"},
    ("fk-03", 3): {"k": ["the forklift mast is bent"]},
    ("lw-01", 0): {"g": "went up the extension ladder", "c": "the ladder slipped in the water", "h": "ongoing"},
    ("lw-01", 2): {"h": "controlled"},
    ("lw-02", 0): {"h": "ongoing"},
    ("lw-03", 0): {"g": "trying to shut the water valve"},
    ("ld-01", 0): {"g": "cut the tie wires", "c": "Someone moved the base while the top was tied",
                   "n": ["the ambulance", "the site supervisor"]},
    ("ch-01", 0): {"g": "checking the vapour cleaning",
                   "c": "pressurised the internal valve before closing the outer one"},
    ("ch-02", 0): {"g": "mixing the sanitiser"},
    ("ch-03", 0): {"c": "he wasn't wearing goggles"},
    ("ch-03", 3): {"n": ["EHS"]},
    ("cv-01", 0): {"g": "taking tyres off", "h": "controlled"},
    ("cv-03", 0): {"c": "The guard on the packing machine was removed"},
    ("cv-03", 4): {"h": "controlled", "n": ["108"]},
    ("fi-01", 0): {"c": "a short circuit"},
    ("fi-01", 2): {"h": "controlled"},
    ("fi-02", 1): {"h": "ongoing"},
    ("fi-03", 0): {"h": "ongoing"},
    ("fi-04", 0): {"c": "LPG leak", "n": ["fire brigade"]},
    ("rf-01", 4): {"c": "the LPG cylinder leaked"},
    ("lf-01", 0): {"c": "probably overloaded"},
    ("lf-02", 0): {"g": "going home"},
    ("lf-02", 3): {"n": ["the lift maintenance company"]},
    ("lf-03", 0): {"h": "ongoing"},
    ("lf-03", 2): {"h": "controlled"},
    ("gs-01", 0): {"c": "Gas leaked from the LPG cylinder", "h": "controlled"},
    ("gs-02", 0): {"c": "the LPG cylinder regulator is leaking", "h": "controlled"},
    ("cl-01", 0): {"k": ["Ceiling fans are damaged"]},
    ("cl-02", 0): {"h": "ongoing"},
    ("cl-03", 0): {"k": ["Light fittings broken"]},
    ("sc-01", 0): {"g": "standing on the wooden scaffolding", "h": "controlled"},
    ("sc-02", 3): {"h": "controlled"},
    ("lb-01", 0): {"n": ["the fire department"]},
    ("lb-02", 0): {"h": "controlled"},
    ("md-02", 0): {"n": ["the ambulance"]},
    ("wl-02", 0): {"h": "ongoing"},
    ("wl-03", 0): {"h": "ongoing"},
    ("sec-01", 0): {"k": ["broke open the window", "cut the CCTV cables"]},
    ("sec-01", 2): {"n": ["the police", "our security team"]},
    ("sec-02", 0): {"h": "controlled"},
    ("sec-03", 2): {"n": ["security"]},
    ("oth-01", 0): {"h": "ongoing"},
}
# Other acceptable spans for the new fields (not required, not counted as extra).
EXTRA_ACCEPT = {
    "na-01": {"c": ["the wet floor", "tripped over the metal strip"]},
    "lw-01": {"g": ["to shut it"]},
    "ch-01": {"c": ["He wasn't wearing goggles"]},
    "fi-01": {"n": ["the fire brigade"]},
    "rf-01": {"n": ["Fire brigade"]},
    "gs-01": {"n": ["The fire brigade"]},
    "md-01": {"n": ["the ambulance"]},
    "lf-01": {"c": ["It's an old lift"]},
    "el-01": {"n": ["ambulance"]},
}
# Turns that were no_op in the 10-field set and now carry one of the new fields.
TOOL_CHANGES = {("fk-03", 3), ("lf-03", 2)}

# ---------------------------------------------------------------- 2. new dialogs (OSHA Severe Injury Reports)
NEW = []


def C(cid, src, split, tags, ref, turns, accept=None, notes=""):
    NEW.append(dict(id=cid, source=src, split=split, tags=tags, ref_time=ref, turns=turns,
                    accept=accept or {}, notes=notes))


def at(ref, hh, mm, day_offset=0):
    d = datetime.fromisoformat(ref).replace(hour=hh, minute=mm, second=0) + timedelta(days=day_offset)
    return d.isoformat()


def ago(ref, minutes):
    return (datetime.fromisoformat(ref) - timedelta(minutes=minutes)).isoformat()


R = "2026-10-08T16:00:00+05:30"   # S18 OSHA SIR 2023054383: planer, fingers amputated, not locked out
C("pl-01", "S18", "dev", ["narration", "machine", "lockout", "context_fields"], R, [
    ("One of our machine operators was cleaning out the space below the planer when his right hand got caught "
     "in the planer blade. Three of his fingers are cut off. The machine was not locked out.",
     {"t": "caught_in", "p": ["One of our machine operators"], "e": ["the planer"],
      "g": "cleaning out the space below the planer", "c": "The machine was not locked out"}),
    ("in the wood shop, ground floor", {"l": "wood shop, ground floor"}),
    ("around 2:30 this afternoon", {"d": "around 2:30 this afternoon"}, at(R, 14, 30)),
    ("critical", {"s": "critical"}),
    ("we have switched off the planer and called the ambulance, the shop supervisor knows",
     {"a": ["switched off the planer", "called the ambulance"], "h": "controlled",
      "n": ["the ambulance", "the shop supervisor"]}),
    ("submit", "submit_report")],
  accept={"l": ["wood shop"], "n": ["ambulance"]})

R = "2026-10-09T11:00:00+05:30"   # S19 OSHA SIR 2024021859: roll coater nip point, slipped while kneeling
C("rc-01", "S19", "dev", ["narration", "machine", "nip_point", "context_fields"], R, [
    ("A worker on the coating line was adjusting the dams between the rollers of the roll coater. She slipped "
     "while kneeling and the rollers pulled her left hand into the nip point, right up to her shoulder.",
     {"t": "caught_in", "p": ["A worker on the coating line"], "e": ["the roll coater"],
      "g": "adjusting the dams between the rollers", "c": "She slipped while kneeling"}),
    ("coating line 1", {"l": "coating line 1"}),
    ("about 9:45 this morning", {"d": "about 9:45 this morning"}, at(R, 9, 45)),
    ("high", {"s": "high"}),
    ("we hit the emergency stop and reversed the rollers to free her, she has been admitted to hospital",
     {"a": ["hit the emergency stop", "reversed the rollers to free her"], "h": "controlled"}),
    ("submit", "submit_report")])

R = "2026-10-07T15:00:00+05:30"   # S20 OSHA SIR 2023087453: chiller guard snapped back, fingertip amputated
C("cg-01", "S20", "test", ["narration", "machine", "lockout", "context_fields"], R, [
    ("Fingertip injury in the cold room. A technician was removing a seal that had come off its track on the "
     "chiller drum, and when it came free the safety guard snapped back and cut off his right middle "
     "fingertip. The machine was locked out at the time.",
     {"t": "caught_in", "l": "cold room", "p": ["A technician"], "e": ["the chiller drum"],
      "g": "removing a seal that had come off its track", "c": "the safety guard snapped back"}),
    ("just before lunch, around 12:40", {"d": "around 12:40"}, at(R, 12, 40)),
    ("high", {"s": "high"}),
    ("we sent him to the hospital and informed EHS",
     {"a": ["sent him to the hospital", "informed EHS"], "n": ["EHS"]}),
    ("submit", "submit_report")],
  accept={"d": ["just before lunch, around 12:40"]})

R = "2026-10-09T17:30:00+05:30"   # S21 OSHA SIR 2025044053: pipe bundle fell off a trailer onto two workers
C("pb-01", "S21", "dev", ["narration", "dense", "count", "context_fields"], R, [
    ("Two of our loaders got hit in the yard. The forklift was loading bundles of pipe onto a trailer and one "
     "bundle, around 500 kilos, slipped off the other side and fell on them while they were standing behind "
     "the trailer. One has both legs broken, the other has a pelvis injury.",
     {"t": "struck_by", "l": "the yard", "p": ["Two of our loaders"], "e": ["The forklift", "a trailer"],
      "g": "standing behind the trailer", "c": "one bundle, around 500 kilos, slipped off the other side"}),
    ("this evening around 4:15", {"d": "this evening around 4:15"}, at(R, 16, 15)),
    ("critical", {"s": "critical"}),
    ("ambulance took both of them, we have stopped loading in the yard and the plant manager has been told",
     {"a": ["stopped loading in the yard"], "h": "controlled", "n": ["the plant manager"]}),
    ("submit", "submit_report")],
  accept={"a": ["ambulance took both of them"], "e": ["bundles of pipe"]},
  notes="Source: 1,200 lb bundle; one leg was later amputated. Rewritten as ~500 kg and fractures.")

R = "2026-10-10T13:00:00+05:30"   # S22 OSHA SIR 2024032363: reversing forklift ran over a worker's legs
C("fr-01", "S22", "dev", ["narration", "vehicle", "relative_time"], R, [
    ("A forklift reversed into one of our packers in the dispatch bay while she was scanning packages. She "
     "fell and the rear wheel went over her legs.",
     {"t": "struck_by", "l": "dispatch bay", "p": ["one of our packers"], "e": ["A forklift"],
      "g": "scanning packages", "c": "A forklift reversed"}),
    ("about an hour ago", {"d": "about an hour ago"}, ago(R, 60)),
    ("high, her legs are badly bruised and she's admitted", {"s": "high"}),
    ("submit", "submit_report")])

R = "2026-10-06T18:00:00+05:30"   # S23 OSHA SIR 20241110761: pickup truck struck a worker in a parking deck
C("pg-01", "S23", "test", ["vehicle", "late_detail", "context_fields"], R, [
    ("one of our site engineers got hit by a pickup in the basement parking",
     {"t": "struck_by", "l": "basement parking", "p": ["one of our site engineers"], "e": ["a pickup"]}),
    ("around 5 PM", {"d": "around 5 PM"}, at(R, 17, 0)),
    ("high, broken ribs and some teeth", {"s": "high"}),
    ("he was marking the damaged concrete spots on the deck when the pickup was driving out",
     {"g": "marking the damaged concrete spots on the deck", "c": "the pickup was driving out"}),
    ("submit", "submit_report")])

R = "2026-10-08T12:00:00+05:30"   # S24 OSHA SIR 2025043122: fell 18 ft while moving a ladder to reach a roof
C("rl-01", "S24", "dev", ["narration", "fall", "context_fields"], R, [
    ("Our plumber fell about 18 feet from the ladder at block D while getting onto the roof. He started "
     "moving the ladder and lost his balance. He has fractures in his back and ribs.",
     {"t": "fall_height", "l": "block D", "p": ["Our plumber"], "e": ["the ladder"],
      "g": "getting onto the roof", "c": "started moving the ladder and lost his balance"}),
    ("10:20 this morning", {"d": "10:20 this morning"}, at(R, 10, 20)),
    ("critical", {"s": "critical"}),
    ("we called 108 and kept him still, the site safety officer is with him",
     {"a": ["called 108", "kept him still"], "n": ["108"]}),
    ("submit", "submit_report")],
  accept={"l": ["the ladder at block D"]})

R = "2026-10-09T19:00:00+05:30"   # S25 OSHA SIR 2024010658: arc flash restoring power after a hurricane
C("af-01", "S25", "dev", ["narration", "electrical", "context_fields"], R, [
    ("Arc flash at the staff quarters while restoring power after the flooding. Our lineman was making the "
     "secondary connections to the old cables lying on the ground when it flashed. He has burns on his face.",
     {"t": "electrical", "l": "staff quarters", "p": ["Our lineman"], "e": ["the old cables"],
      "g": "making the secondary connections"}),
    ("around 5:30 in the evening", {"d": "around 5:30 in the evening"}, at(R, 17, 30)),
    ("high", {"s": "high"}),
    ("the supply to that line is switched off now and the electrical supervisor has been informed",
     {"a": ["the supply to that line is switched off"], "h": "controlled", "n": ["the electrical supervisor"]}),
    ("submit", "submit_report")],
  notes="Source: post-hurricane mobile-home park; rewritten as staff quarters after flooding.")

R = "2026-10-07T11:00:00+05:30"   # S26 OSHA SIR 2023010979: shock in switchgear, co-worker pulled him free
C("sg-01", "S26", "test", ["narration", "electrical", "rescue"], R, [
    ("Electric shock in the pump house panel room. Our electrician was removing old wiring from the old fire "
     "pump switchgear and got a shock, his co-worker pulled him off. He also cut his left hand and forearm. "
     "The breaker was locked out but he still got the shock.",
     {"t": "electrical", "l": "pump house panel room", "p": ["Our electrician", "his co-worker"],
      "e": ["the old fire pump switchgear"], "g": "removing old wiring", "a": ["his co-worker pulled him off"]}),
    ("about 9 this morning", {"d": "about 9 this morning"}, at(R, 9, 0)),
    ("high", {"s": "high"}),
    ("submit", "submit_report")])

R = "2026-10-06T16:00:00+05:30"   # S27 OSHA SIR 2023064947: flash fire from a lit cigarette while a gas line vented
C("gw-01", "S27", "dev", ["narration", "fire", "context_fields"], R, [
    ("Flash fire at the gas metering station. After maintenance, our technician was waiting for the line to "
     "vent and someone lit a cigarette. He has burns on his face, hands and head and he is coughing.",
     {"t": "fire", "l": "gas metering station", "p": ["our technician"], "g": "waiting for the line to vent",
      "c": "someone lit a cigarette"}),
    ("around 1:30", {"d": "around 1:30"}, at(R, 13, 30)),
    ("critical", {"s": "critical"}),
    ("the fire is out and the vent is closed. we called the ambulance and the station in-charge",
     {"h": "controlled", "a": ["called the ambulance and the station in-charge"],
      "n": ["the ambulance", "the station in-charge"]}),
    ("submit", "submit_report")],
  notes="Source: natural gas well site; rewritten as a gas metering station.")

R = "2026-10-08T21:00:00+05:30"   # S28 OSHA SIR 2025066036: extinguisher hose struck an operator fighting a machine fire
C("fx-01", "S28", "dev", ["narration", "fire", "type_priority", "context_fields"], R, [
    ("Fire in the extruder on line 3. Our operator was pulling burning material out of the machine and using "
     "the extinguisher when the horn came off the hose and the hose hit his neck and shoulder. He is having "
     "trouble swallowing.",
     {"t": "struck_by", "l": "line 3", "p": ["Our operator"], "e": ["the extruder", "the extinguisher"],
      "g": "pulling burning material out of the machine", "c": "the horn came off the hose"}),
    ("around 7:15 this evening", {"d": "around 7:15 this evening"}, at(R, 19, 15)),
    ("high", {"s": "high"}),
    ("fire is out now, the extruder is shut down and he's gone to hospital",
     {"h": "controlled", "a": ["the extruder is shut down"]}),
    ("submit", "submit_report")],
  accept={"t": ["fire"]},
  notes="Someone was hurt, so the injury mechanism (struck by the hose) decides the type; fire is accepted.")

R = "2026-10-07T03:00:00+05:30"   # S29 OSHA SIR 2023064861: tyre, rim and hub blew off while inflating, 1 AM
C("ty-01", "S29", "dev", ["narration", "night", "damage", "context_fields"], R, [
    ("Tyre blast in the truck bay at 1 AM. Our fitter was filling air in a trailer tyre, the rim was already "
     "damaged from a bearing failure and the whole tyre, rim and hub flew off the axle and hit his legs. "
     "Compound fracture in the left leg.",
     {"t": "struck_by", "l": "truck bay", "d": "at 1 AM", "p": ["Our fitter"], "e": ["a trailer tyre"],
      "g": "filling air in a trailer tyre", "c": "a bearing failure", "k": ["the rim was already damaged"]},
     at(R, 1, 0)),
    ("critical", {"s": "critical"}),
    ("ambulance has taken him, we have blocked off that bay", {"h": "controlled", "a": ["blocked off that bay"]}),
    ("submit", "submit_report")],
  accept={"a": ["ambulance has taken him"]})

R = "2026-10-10T16:30:00+05:30"   # S30 OSHA SIR 20251110924: pipe joint released at 140 PSI during a pressure test
C("pt-01", "S30", "test", ["narration", "pressure", "context_fields"], R, [
    ("Pressure test accident at the new water line. Our fitter was pressure testing a joint on the HDPE pipe "
     "before backfilling, it was at 140 PSI when the joint came loose near the end cap and hit him. His jaw "
     "and right leg are fractured.",
     {"t": "struck_by", "l": "the new water line", "p": ["Our fitter"], "e": ["the HDPE pipe"],
      "g": "pressure testing a joint on the HDPE pipe", "c": "the joint came loose near the end cap"}),
    ("around 2 PM", {"d": "around 2 PM"}, at(R, 14, 0)),
    ("critical", {"s": "critical"}),
    ("the line is depressurised now", {"h": "controlled"}),
    ("submit", "submit_report")])

R = "2026-10-09T20:00:00+05:30"   # S31 OSHA SIR 2023010707: slipped catching a leaning milk container, 6:30 PM
C("mk-01", "S31", "dev", ["narration", "slip", "context_fields"], R, [
    ("Store staff slipped in the backroom cooler while pushing a container of milk cartons out to the truck. "
     "The container started leaning and when she tried to hold it her legs slipped. Torn ligament in the "
     "right leg.",
     {"t": "slip_trip", "l": "backroom cooler", "p": ["Store staff"], "e": ["a container of milk cartons"],
      "g": "pushing a container of milk cartons out to the truck", "c": "The container started leaning"}),
    ("6:30 this evening", {"d": "6:30 this evening"}, at(R, 18, 30)),
    ("medium", {"s": "medium"}),
    ("submit", "submit_report")])

R = "2026-10-08T14:00:00+05:30"   # S32 OSHA SIR 2024032675: 180 F water gushed out while replacing a heating valve
C("hw-01", "S32", "test", ["narration", "burn", "context_fields"], R, [
    ("Our maintenance tech got scalded in the boiler room. He was replacing a ball valve on the heating line, "
     "froze the pipe to isolate it, but when he cut the old valve out the hot water gushed out. Burns on his "
     "finger and knee.",
     {"t": "other", "l": "boiler room", "p": ["Our maintenance tech"], "e": ["the heating line"],
      "g": "replacing a ball valve on the heating line", "c": "the hot water gushed out"}),
    ("around noon", {"d": "around noon"}),
    ("high", {"s": "high"}),
    ("the valve is fitted now and the line is back to normal", {"h": "controlled"}),
    ("submit", "submit_report")],
  accept={"t": ["water_leak"]},
  notes="A burn from hot water fits none of the injury mechanisms; 'other', with water_leak accepted.")


# ---------------------------------------------------------------- 3. build, check, write
def with_extra(case: dict) -> dict:
    c = dict(case, turns=[], accept={k: list(v) for k, v in case["accept"].items()})
    for i, t in enumerate(case["turns"]):
        text, gold = t[0], t[1]
        add = EXTRA.get((case["id"], i))
        if add:
            if isinstance(gold, str):
                if gold != "no_op" or (case["id"], i) not in TOOL_CHANGES:
                    raise SystemExit(f"{case['id']} turn {i}: new fields on a {gold} turn")
                gold = dict(add)
            else:
                clash = set(gold) & set(add)
                if clash:
                    raise SystemExit(f"{case['id']} turn {i}: {clash} already labelled")
                gold = {**gold, **add}
        c["turns"].append((text, gold) + tuple(t[2:]))
    for k, spans in EXTRA_ACCEPT.get(case["id"], {}).items():
        c["accept"].setdefault(k, []).extend(spans)
    return c


def check(cases: list[dict], originals: dict) -> list[str]:
    problems = []
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        problems.append("duplicate case ids")
    for c in cases:
        for t in c["turns"]:
            where = f"{c['id']} turn {t['i']}"
            args, low = t["gold"]["args"], t["user"].lower()
            for k, v in args.items():
                f = FIELDS.get(k)
                if f is None:
                    problems.append(f"{where}: unknown key {k}")
                    continue
                if f["kind"] == "enum":
                    if v not in f["values"]:
                        problems.append(f"{where}: {k}={v!r} not in {f['values']}")
                    continue
                items = v if f["kind"] == "text_list" else [v]
                if f["kind"] == "text_list" and len(items) > f["max_items"]:
                    problems.append(f"{where}: {k} has {len(items)} items > {f['max_items']}")
                for item in items:
                    if len(item) > f["max_chars"]:
                        problems.append(f"{where}: {k}={item!r} longer than {f['max_chars']}")
                    if item.lower() not in low:
                        problems.append(f"{where}: {k}={item!r} is not in the user's words")
            orig = originals.get((c["id"], t["i"]))
            if orig is not None:
                ten = {k: v for k, v in args.items() if k not in NEW_KEYS}
                if ten != orig["args"]:
                    problems.append(f"{where}: 10-field gold changed {orig['args']} -> {ten}")
                tool_ok = t["gold"]["tool"] == orig["tool"] or (
                    (c["id"], t["i"]) in TOOL_CHANGES and t["gold"]["tool"] == "update_report")
                if not tool_ok:
                    problems.append(f"{where}: tool changed {orig['tool']} -> {t['gold']['tool']}")
    return problems


def main() -> int:
    B10.LIST_FIELDS = LIST_FIELDS15                  # the 10-field builder's merge() reads this global
    cases = [B10.expand(with_extra(c)) for c in B10.CASES] + [B10.expand(c) for c in NEW]
    originals = {}
    for ln in (HERE.parent / "nlu" / "cases.jsonl").read_text(encoding="utf-8").splitlines():
        oc = json.loads(ln)
        for t in oc["turns"]:
            originals[(oc["id"], t["i"])] = t["gold"]
    problems = check(cases, originals)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    out = HERE / "cases.jsonl"
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    turns = sum(len(c["turns"]) for c in cases)
    split = {s: sum(c["split"] == s for c in cases) for s in ("demo", "dev", "test")}
    cover = {k: sum(k in t["gold"]["args"] for c in cases for t in c["turns"]) for k in FIELDS}
    print(f"wrote {len(cases)} cases, {turns} turns to {out}")
    print(f"splits {split} · new cases {len(NEW)} · turns labelled per field {cover}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
