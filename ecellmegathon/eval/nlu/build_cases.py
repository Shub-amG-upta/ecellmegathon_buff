#!/usr/bin/env python3
"""Source of truth for the NLU (text) eval set. Writes cases.jsonl next to this file.

Each case: a scripted dialog. Each turn: what the user says + the gold tool call for that turn
(only what the user said in that turn). The script derives, per turn:
  asked        the field the assistant asks about before this turn (first missing mandatory field)
  form_before  the gold form state before this turn (used by the teacher-forced eval mode)
and per case gold_final (the form after the last update).

Every gold string value must be copied from the user's words (validate_cases.py checks this).
Sources for every case are in SOURCES.md (S-codes). Stdlib only.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

ASK_ORDER = ["t", "l", "d", "s"]
LIST_FIELDS = {"p", "e", "a", "o", "w"}
CASES = []


def C(cid, src, split, tags, ref, turns, accept=None, notes=""):
    """turns: list of (user_text, args_dict | tool_name) or (user_text, args_dict, gold_time)."""
    CASES.append(dict(id=cid, source=src, split=split, tags=tags, ref_time=ref,
                      turns=turns, accept=accept or {}, notes=notes))


def at(ref, hh, mm, day_offset=0):
    d = datetime.fromisoformat(ref).replace(hour=hh, minute=mm, second=0) + timedelta(days=day_offset)
    return d.isoformat()


def ago(ref, minutes):
    return (datetime.fromisoformat(ref) - timedelta(minutes=minutes)).isoformat()


R = "2026-10-10T15:00:00+05:30"

# ---------------------------------------------------------------- S00 problem statement
C("ps-01", "S00", "demo", ["ps_example", "narration", "follow_up"], R, [
    ("There was a water leak near Conference Room 2 around 10 AM. Maintenance has already isolated the area.",
     {"t": "water_leak", "l": "near Conference Room 2", "d": "around 10 AM", "a": ["isolated the area"],
      "o": ["Maintenance"], "h": "contained"}, at(R, 10, 0)),
    ("it's medium", {"s": "medium"}),
    ("submit", "submit_report")],
  accept={"l": ["Conference Room 2"], "a": ["area isolated"]})

C("ps-02", "S00", "dev", ["paraphrase", "informal", "follow_up"], R, [
    ("water leaking near conf room 2 since 10 or so, maintenance guys have isolated it",
     {"t": "water_leak", "l": "near conf room 2", "d": "since 10 or so", "a": ["isolated it"],
      "o": ["maintenance guys"], "h": "contained"}, at(R, 10, 0)),
    ("medium", {"s": "medium"}),
    ("yes submit it", "submit_report")],
  accept={"l": ["conf room 2"], "o": ["maintenance"]})

C("ps-03", "S00", "dev", ["premature_submit", "short_answers"], R, [
    ("water leak near Conference Room 2", {"t": "water_leak", "l": "near Conference Room 2"}),
    ("submit", "submit_report"),
    ("around 10 AM", {"d": "around 10 AM"}, at(R, 10, 0)),
    ("medium", {"s": "medium"}),
    ("submit", "submit_report")],
  accept={"l": ["Conference Room 2"]},
  notes="turn 2: model should still call submit_report; code refuses because d and s are missing")

# ---------------------------------------------------------------- S01 OSHA 201169307 electrocution
R1 = "2026-10-10T17:00:00+05:30"
C("el-01", "S01", "dev", ["narration", "dense", "severity_word"], R1, [
    ("This is to report a serious incident in the meter room on the ground floor around 3 PM today. "
     "Our electrical contractor's foreman was installing panels and got a shock from the 480 volt pull panel. "
     "The cover was off and one live wire was hanging loose. He was working alone, coworkers found him on the "
     "floor and called the ambulance. He has been taken to hospital.",
     {"t": "electrical", "s": "high", "l": "meter room on the ground floor", "d": "around 3 PM today",
      "p": ["electrical contractor's foreman"], "g": "installing panels", "i": "shock",
      "c": "one live wire was hanging loose", "e": ["480 volt pull panel"], "a": ["called the ambulance"],
      "o": ["ambulance"], "m": "hospitalised"}, at(R1, 15, 0)),
    ("submit", "submit_report")],
  accept={"c": ["cover was off", "480 volt pull panel"], "i": ["electric shock"], "l": ["meter room"]},
  notes="'serious' maps to high (LABELING.md). Source was fatal; outcome softened to hospitalised.")

C("el-02", "S01", "test", ["fillers", "self_correction", "follow_up"], R1, [
    ("uh hello, I want to report, the electrician got a shock in the meter room",
     {"t": "electrical", "l": "meter room", "p": ["the electrician"], "i": "shock"}),
    ("around 3, no wait, closer to 3:30", {"d": "closer to 3:30"}, at(R1, 15, 30)),
    ("high, he was taken to hospital", {"s": "high", "m": "hospitalised"}),
    ("submit", "submit_report")],
  accept={"d": ["3:30"]})

C("el-03", "S01", "dev", ["short_answers", "late_detail"], R1, [
    ("electrical problem", {"t": "electrical"}),
    ("meter room", {"l": "meter room"}),
    ("3 PM", {"d": "3 PM"}, at(R1, 15, 0)),
    ("high", {"s": "high"}),
    ("the panel cover was open and a live wire was hanging", {"c": "a live wire was hanging", "e": ["panel"]}),
    ("submit", "submit_report")],
  accept={"c": ["panel cover was open", "live wire"]})

# ---------------------------------------------------------------- S02 OSHA 960484 slip, ER
R2 = "2026-10-10T11:00:00+05:30"
C("sl-01", "S02", "dev", ["narration", "dense", "body_part"], R2, [
    ("Around 10:30 this morning one of our staff slipped on the wet floor in the emergency room while preparing "
     "medical equipment. He hit his right arm on a metal cabinet, looks like his elbow and wrist are fractured. "
     "He has been admitted. I'd say severity high.",
     {"t": "slip_trip", "d": "Around 10:30 this morning", "p": ["one of our staff"], "c": "wet floor",
      "l": "emergency room", "g": "preparing medical equipment", "i": "fractured", "b": "elbow and wrist",
      "m": "hospitalised", "s": "high"}, at(R2, 10, 30)),
    ("submit", "submit_report")],
  accept={"c": ["slipped on the wet floor", "metal cabinet"], "b": ["right arm", "right elbow and wrist"],
          "i": ["fracture"]})

C("sl-02", "S02", "test", ["sparse_start", "relative_time", "volunteered_detail"], R2, [
    ("Someone slipped near the ER, wet floor", {"t": "slip_trip", "l": "near the ER", "c": "wet floor"}),
    ("half an hour back", {"d": "half an hour back"}, ago(R2, 30)),
    ("medium I think", {"s": "medium"}),
    ("oh and he has hurt his wrist, we gave first aid", {"b": "wrist", "m": "first_aid", "a": ["gave first aid"]}),
    ("submit", "submit_report")],
  accept={"l": ["ER"]})

C("sl-03", "S02", "dev", ["indian_english", "follow_up"], R2, [
    ("Sir one housekeeping staff fell down in the ER corridor, floor was wet after mopping, she is having pain in wrist",
     {"t": "slip_trip", "p": ["one housekeeping staff"], "l": "ER corridor", "c": "floor was wet after mopping",
      "b": "wrist", "i": "pain"}),
    ("today morning 10:30", {"d": "today morning 10:30"}, at(R2, 10, 30)),
    ("medium", {"s": "medium"}),
    ("we gave her ice pack and sent her to doctor", {"a": ["gave her ice pack"], "m": "doctor_or_clinic"}),
    ("submit", "submit_report")],
  accept={"a": ["ice pack", "sent her to doctor"]})

# ---------------------------------------------------------------- S03 OSHA 902387 trip, shower room
C("na-01", "S03", "dev", ["narration", "hazard_status", "yesterday"], R, [
    ("Last night around 8 PM a nursing aide tripped over the metal strip in the first floor shower room and hurt "
     "her ankle on the wet floor, it's fractured. She was showering a resident at the time. The floor is still "
     "wet and the strip is still loose.",
     {"t": "slip_trip", "d": "Last night around 8 PM", "p": ["a nursing aide"], "c": "metal strip",
      "l": "first floor shower room", "b": "ankle", "i": "fractured", "g": "showering a resident",
      "h": "ongoing"}, at(R, 20, 0, -1)),
    ("high", {"s": "high"}),
    ("she's in hospital now", {"m": "hospitalised"}),
    ("submit", "submit_report")],
  accept={"c": ["tripped over the metal strip", "wet floor"], "l": ["shower room"]})

C("na-02", "S03", "dev", ["negation", "near_miss"], R, [
    ("A resident nearly slipped in the shower room this morning, but no one was hurt. Housekeeping has dried the floor.",
     {"t": "slip_trip", "l": "shower room", "d": "this morning", "n": 0, "m": "none",
      "a": ["dried the floor"], "o": ["Housekeeping"]}),
    ("low", {"s": "low"}),
    ("submit", "submit_report")],
  notes="'nearly slipped ... no one was hurt': n=0, m=none; nothing in i/b")

C("na-03", "S03", "test", ["two_incidents", "trap"], R, [
    ("Two things, a nurse tripped on the metal strip in the shower room, and also the shower tap is leaking.",
     {"t": "slip_trip", "p": ["a nurse"], "c": "metal strip", "l": "shower room"}),
    ("around 8 last night", {"d": "around 8 last night"}, at(R, 20, 0, -1)),
    ("low, she's fine", {"s": "low"}),
    ("submit", "submit_report")],
  accept={"c": ["tripped on the metal strip"]},
  notes="second incident (tap leak) must not change t; report the first incident")

# ---------------------------------------------------------------- S04/S05 OSHA forklift
R4 = "2026-10-10T17:00:00+05:30"
C("fk-01", "S04", "dev", ["narration", "dense", "severity_word"], R4, [
    ("At about 2 in the afternoon in the warehouse, a forklift driver hit the stacked material, got down to "
     "straighten it and the load fell on his chest. He couldn't move it. We called an ambulance and he's being "
     "taken to hospital. This is critical.",
     {"t": "struck_by", "d": "about 2 in the afternoon", "l": "warehouse", "p": ["a forklift driver"],
      "e": ["forklift"], "g": "straighten it", "c": "the load fell on his chest", "b": "chest",
      "a": ["called an ambulance"], "o": ["ambulance"], "m": "hospitalised", "s": "critical"}, at(R4, 14, 0)),
    ("submit", "submit_report")],
  accept={"g": ["got down to straighten it"], "c": ["stacked material", "load fell"]},
  notes="source was fatal; outcome softened")

C("fk-02", "S05", "test", ["narration", "correction", "severity_change"], R4, [
    ("So, a guy was reversing the forklift out of the warehouse and it hit the overhead door. Then he tried to "
     "turn it around with the forks up and the whole thing tipped over on him.",
     {"t": "struck_by", "l": "warehouse", "e": ["forklift"], "g": "reversing the forklift",
      "c": "tipped over on him", "k": "hit the overhead door"}),
    ("uh around 4:15", {"d": "around 4:15"}, at(R4, 16, 15)),
    ("high, he has a fracture, he's admitted", {"s": "high", "i": "fracture", "m": "hospitalised"}),
    ("actually make it critical", {"s": "critical"}),
    ("submit", "submit_report")],
  accept={"g": ["turn it around with the forks up"], "c": ["forks up", "forklift tipped over"],
          "k": ["overhead door"]})

C("fk-03", "S04", "dev", ["negation", "damage", "time_words"], R4, [
    ("Material fell from the rack in warehouse aisle 4 and hit the forklift, no one hurt",
     {"t": "struck_by", "l": "warehouse aisle 4", "c": "Material fell from the rack", "e": ["forklift"],
      "n": 0, "m": "none"}),
    ("quarter past two", {"d": "quarter past two"}, at(R4, 14, 15)),
    ("medium", {"s": "medium"}),
    ("the forklift mast is bent", {"k": "the forklift mast is bent"}),
    ("submit", "submit_report")],
  accept={"k": ["forklift mast"]})

# ---------------------------------------------------------------- S06 OSHA 889352 ladder + leaking valve
R6 = "2026-10-10T12:00:00+05:30"
C("lw-01", "S06", "dev", ["narration", "type_priority", "hazard_status"], R6, [
    ("There's water leaking from a valve in the basement parking driveway. Our maintenance mechanic went up the "
     "extension ladder at 11:30 to shut it, the ladder slipped in the water and he fell onto the concrete. Both "
     "his arms look broken. The water is still leaking.",
     {"t": "fall_height", "l": "basement parking driveway", "e": ["extension ladder"],
      "p": ["maintenance mechanic"], "d": "at 11:30", "g": "shut it", "c": "the ladder slipped in the water",
      "b": "arms", "i": "broken", "h": "ongoing"}, at(R6, 11, 30)),
    ("high", {"s": "high"}),
    ("he's been taken to hospital, and security has cordoned off the driveway",
     {"m": "hospitalised", "a": ["cordoned off the driveway"], "o": ["security"]}),
    ("submit", "submit_report")],
  accept={"g": ["went up the extension ladder"], "e": ["valve"]},
  notes="injury mechanism wins over the water leak for t (LABELING.md)")

C("lw-02", "S06", "test", ["negation", "relative_time", "hazard_status"], R, [
    ("The valve in the basement is leaking again, there's about two inches of water near the ramp. "
     "Nobody's hurt. Can someone shut it off?",
     {"t": "water_leak", "l": "basement", "e": ["valve"], "h": "ongoing", "n": 0, "m": "none"}),
    ("started like an hour ago", {"d": "an hour ago"}, ago(R, 60)),
    ("low, but it's slippery", {"s": "low"}),
    ("submit", "submit_report")],
  accept={"l": ["near the ramp"]})

C("lw-03", "S06", "dev", ["witness", "first_person"], R6, [
    ("I saw the maintenance guy fall off the ladder at the parking ramp, me and Ravi both saw it, he was trying "
     "to shut the water valve",
     {"t": "fall_height", "l": "parking ramp", "p": ["the maintenance guy"], "e": ["ladder"], "w": ["Ravi"],
      "g": "trying to shut the water valve"}),
    ("11:30", {"d": "11:30"}, at(R6, 11, 30)),
    ("high", {"s": "high"}),
    ("submit", "submit_report")],
  accept={"w": ["me and Ravi"], "e": ["water valve"]})

# ---------------------------------------------------------------- S07 OSHA 787184 extension ladder
C("ld-01", "S07", "dev", ["narration", "multi_action"], R, [
    ("A carpenter fell from the thirty foot extension ladder near the east wall about twenty minutes ago. "
     "Someone moved the base while the top was tied, then he cut the tie wires and the top section came down. "
     "He is conscious, we have called the ambulance and informed the site supervisor.",
     {"t": "fall_height", "p": ["A carpenter"], "e": ["thirty foot extension ladder"], "l": "near the east wall",
      "d": "about twenty minutes ago", "c": "the top section came down",
      "a": ["called the ambulance", "informed the site supervisor"], "o": ["ambulance", "site supervisor"]},
     ago(R, 20)),
    ("critical, he can't move his leg", {"s": "critical", "b": "leg"}),
    ("submit", "submit_report")],
  accept={"c": ["moved the base", "cut the tie wires"]})

C("ld-02", "S07", "dev", ["no_op", "cancel", "trap"], R, [
    ("Is the carpenter ladder fall already reported? Rakesh said he filed it.", "no_op"),
    ("ok then cancel this one", "cancel_report")],
  notes="a question to the assistant: no fields should be filled")

# ---------------------------------------------------------------- S08/S09 OSHA chemical splash
R8 = "2026-10-10T10:00:00+05:30"
C("ch-01", "S08", "dev", ["narration", "dense"], R8, [
    ("Reporting a chemical splash at the tanker wash bay around 9 AM. The manager was checking the vapour "
     "cleaning, he pressurised the internal valve before closing the outer one and the solvent splashed into his "
     "eyes. He wasn't wearing goggles. We flushed his eyes at the eyewash and he has gone to the clinic. "
     "Medium severity.",
     {"t": "chemical", "l": "tanker wash bay", "d": "around 9 AM", "p": ["The manager"],
      "g": "checking the vapour cleaning", "c": "the solvent splashed into his eyes", "b": "eyes",
      "a": ["flushed his eyes at the eyewash"], "m": "doctor_or_clinic", "s": "medium"}, at(R8, 9, 0)),
    ("submit", "submit_report")],
  accept={"c": ["pressurised the internal valve before closing the outer one", "wasn't wearing goggles"],
          "e": ["internal valve", "eyewash"]})

C("ch-02", "S09", "test", ["sparse_start", "indian_english", "severity_negation"], R8, [
    ("chemical splashed in a worker's eye while mixing the sanitiser",
     {"t": "chemical", "p": ["a worker"], "b": "eye", "g": "mixing the sanitiser", "c": "chemical splashed"}),
    ("the store room on ground floor", {"l": "store room on ground floor"}),
    ("8:15 today morning", {"d": "8:15 today morning"}, at(R8, 8, 15)),
    ("low, not serious, just irritation", {"s": "low", "i": "irritation"}),
    ("submit", "submit_report")],
  accept={"c": ["sanitiser"]})

C("ch-03", "S08", "dev", ["follow_up", "late_detail"], R8, [
    ("Solvent got into the operator's eyes at the wash bay, he wasn't wearing goggles",
     {"t": "chemical", "l": "wash bay", "b": "eyes", "c": "Solvent", "p": ["the operator"]}),
    ("9 o'clock", {"d": "9 o'clock"}, at(R8, 9, 0)),
    ("medium", {"s": "medium"}),
    ("we have flushed his eyes for fifteen minutes and EHS has been informed",
     {"a": ["flushed his eyes for fifteen minutes"], "o": ["EHS"]}),
    ("submit", "submit_report")],
  accept={"c": ["wasn't wearing goggles"], "a": ["EHS has been informed"]})

# ---------------------------------------------------------------- S10 OSHA 955005 conveyor
C("cv-01", "S10", "dev", ["narration", "dense", "amputation"], R, [
    ("One of the mechanics got his finger caught in the conveyor chain while taking tyres off. The fingertip is "
     "cut off. It happened at about 1:45 at the tyre line. We stopped the conveyor and he is on the way to "
     "hospital. Critical.",
     {"t": "caught_in", "p": ["One of the mechanics"], "e": ["conveyor chain"], "g": "taking tyres off",
      "i": "fingertip is cut off", "b": "finger", "d": "about 1:45", "l": "tyre line",
      "a": ["stopped the conveyor"], "m": "hospitalised", "s": "critical"}, at(R, 13, 45)),
    ("submit", "submit_report")],
  accept={"i": ["amputation", "cut off"], "b": ["fingertip"]})

C("cv-02", "S10", "dev", ["cancel", "negation"], R, [
    ("The conveyor at the tyre line jammed, nobody hurt",
     {"t": "equipment_failure", "l": "tyre line", "e": ["conveyor"], "n": 0, "m": "none"}),
    ("sorry, it's fine actually, maintenance already fixed it, cancel this", "cancel_report")],
  notes="turn 2 mentions a fix but the intent is cancel")

C("cv-03", "S10", "test", ["follow_up", "indian_number", "multi_action"], R, [
    ("The guard on the packing machine was removed and a helper's glove got pulled in, his hand is crushed",
     {"t": "caught_in", "e": ["packing machine"], "c": "guard on the packing machine was removed",
      "p": ["a helper"], "b": "hand", "i": "crushed"}),
    ("packing section, ground floor", {"l": "packing section, ground floor"}),
    ("about 20 minutes back", {"d": "about 20 minutes back"}, ago(R, 20)),
    ("critical", {"s": "critical"}),
    ("we pressed the emergency stop and called 108", {"a": ["pressed the emergency stop", "called 108"],
                                                      "o": ["108"]}),
    ("submit", "submit_report")],
  accept={"c": ["glove got pulled in"], "l": ["packing section"]},
  notes="108 is India's emergency ambulance number")

# ---------------------------------------------------------------- S11 Jubilee Hills office fire
C("fi-01", "S11", "dev", ["narration", "negation", "status_update"], R, [
    ("Fire on the fourth floor, in the software company office, started maybe ten minutes back. Looks like a "
     "short circuit. We have evacuated everyone and the fire brigade is on the way. Nobody is hurt.",
     {"t": "fire", "l": "fourth floor", "d": "ten minutes back", "c": "short circuit",
      "a": ["evacuated everyone"], "o": ["fire brigade"], "n": 0, "m": "none"}, ago(R, 10)),
    ("high", {"s": "high"}),
    ("fire is out now, the firefighters put it out", {"h": "resolved", "a": ["the firefighters put it out"]}),
    ("submit", "submit_report")],
  accept={"l": ["software company office", "fourth floor, in the software company office"],
          "a": ["put it out"], "o": ["firefighters"]})

C("fi-02", "S11", "test", ["no_op", "noise_start", "indian_english"], R, [
    ("hello? is this recording?", "no_op"),
    ("ok so there is smoke coming from the electrical room on 4th floor, fire alarm is ringing",
     {"t": "fire", "l": "electrical room on 4th floor", "e": ["fire alarm"]}),
    ("just now only", {"d": "just now"}, R),
    ("very serious, critical", {"s": "critical"}),
    ("submit", "submit_report")],
  accept={"t": ["electrical"]})

C("fi-03", "S11", "dev", ["correction", "location_change"], R, [
    ("There's a fire in the server room on the third floor! Everyone is evacuating.",
     {"t": "fire", "l": "server room on the third floor", "a": ["evacuating"]}),
    ("right now", {"d": "right now"}, R),
    ("critical", {"s": "critical"}),
    ("sorry, it's the UPS room, not the server room", {"l": "UPS room"}),
    ("submit", "submit_report")],
  accept={"a": ["Everyone is evacuating"], "l": ["UPS room on the third floor"]})

# ---------------------------------------------------------------- S15 Connaught Place kitchen fire
R15 = "2026-10-10T12:30:00+05:30"
C("fi-04", "S15", "dev", ["single_turn_complete", "dense", "count"], R15, [
    ("Kitchen fire in the cafeteria at 11:55 AM, LPG leak, six staff with burns, evacuated everyone, called "
     "fire brigade, it's critical.",
     {"t": "fire", "l": "cafeteria", "d": "11:55 AM", "c": "LPG leak", "n": 6, "i": "burns",
      "a": ["evacuated everyone", "called fire brigade"], "o": ["fire brigade"], "s": "critical"},
     at(R15, 11, 55)),
    ("submit", "submit_report")],
  accept={"l": ["Kitchen", "cafeteria kitchen"]})

C("rf-01", "S15", "test", ["fillers", "correction", "late_cause"], R15, [
    ("Okay so, fire in the cafeteria kitchen, it spread really fast, six staff have burns, two of them very "
     "badly. We have evacuated staff and customers. Fire brigade has sent six tenders.",
     {"t": "fire", "l": "cafeteria kitchen", "n": 6, "i": "burns", "a": ["evacuated staff and customers"],
      "o": ["Fire brigade"]}),
    ("around 11:55", {"d": "around 11:55"}, at(R15, 11, 55)),
    ("critical", {"s": "critical"}),
    ("correction, the kitchen is on the second floor", {"l": "the kitchen is on the second floor"}),
    ("they think the LPG cylinder leaked", {"c": "LPG cylinder leaked", "e": ["LPG cylinder"]}),
    ("submit", "submit_report")],
  accept={"l": ["second floor", "cafeteria kitchen on the second floor"]},
  notes="'six tenders' is not an injured count; 'two of them' must not overwrite n")

# ---------------------------------------------------------------- S12/S13 lift crashes
R12 = "2026-10-10T16:00:00+05:30"
C("lf-01", "S12", "dev", ["narration", "dense", "count"], R12, [
    ("The lift in Block C fell from the second floor at around 3:15 PM. Seven people were inside, mostly leg "
     "injuries, one man has fractures in both legs. The security guard and police broke the lift door and got "
     "everyone out, they are all at the hospital. It's an old lift, probably overloaded.",
     {"t": "lift", "l": "Block C", "d": "around 3:15 PM", "n": 7, "i": "fractures", "b": "legs",
      "a": ["broke the lift door", "got everyone out"], "o": ["security guard", "police"],
      "m": "hospitalised", "c": "probably overloaded", "e": ["lift"]}, at(R12, 15, 15)),
    ("critical", {"s": "critical"}),
    ("submit", "submit_report")],
  accept={"l": ["lift in Block C", "second floor"], "i": ["leg injuries"], "b": ["both legs", "leg"],
          "c": ["old lift", "overloaded"]})

R13 = "2026-10-10T18:30:00+05:30"
C("lf-02", "S13", "test", ["count", "count_trap", "follow_up"], R13, [
    ("Lift crash in our tower, it dropped from the 8th floor while people were going home. Nine of our employees "
     "inside are injured.",
     {"t": "lift", "l": "8th floor", "n": 9, "g": "going home"}),
    ("5:45 in the evening", {"d": "5:45 in the evening"}, at(R13, 17, 45)),
    ("high. five of them are in ICU with arm and leg injuries", {"s": "high", "m": "hospitalised",
                                                                  "b": "arm and leg"}),
    ("also we have informed the lift maintenance company", {"a": ["informed the lift maintenance company"],
                                                            "o": ["lift maintenance company"]}),
    ("submit", "submit_report")],
  accept={"l": ["our tower"], "b": ["arm and leg injuries"], "i": ["arm and leg injuries"]},
  notes="'five of them' must not overwrite n=9")

C("lf-03", "S12", "dev", ["status_update", "negation"], R, [
    ("Two people are stuck in lift number 3 between second and third floor, the lift stopped suddenly about ten "
     "minutes ago. Security is trying to open it.",
     {"t": "lift", "p": ["Two people"], "e": ["lift number 3"], "l": "between second and third floor",
      "d": "about ten minutes ago", "o": ["Security"]}, ago(R, 10)),
    ("medium", {"s": "medium"}),
    ("they're out now, nobody hurt", {"n": 0, "m": "none", "h": "resolved"}),
    ("submit", "submit_report")])

C("lf-04", "S13", "dev", ["no_op", "noise_start", "short_answers"], R13, [
    ("um... uh...", "no_op"),
    ("the lift in tower B fell, people are hurt", {"t": "lift", "l": "tower B"}),
    ("5:45", {"d": "5:45"}, at(R13, 17, 45)),
    ("high", {"s": "high"}),
    ("submit", "submit_report")],
  accept={"l": ["lift in tower B"]})

# ---------------------------------------------------------------- S14 Shah Ghouse LPG leak fire
C("gs-01", "S14", "dev", ["narration", "count_sum", "type_priority"], R15, [
    ("Gas leaked from the LPG cylinder in the canteen kitchen and caught fire around 12:30. One cook has bad "
     "burns, nine others are minor. The fire brigade put it out in twenty minutes, the injured were shifted to "
     "KIMS hospital.",
     {"t": "fire", "c": "Gas leaked from the LPG cylinder", "e": ["LPG cylinder"], "l": "canteen kitchen",
      "d": "around 12:30", "n": 10, "i": "burns", "m": "hospitalised", "o": ["fire brigade"],
      "a": ["put it out in twenty minutes", "shifted to KIMS hospital"], "h": "resolved"}, at(R15, 12, 30)),
    ("critical", {"s": "critical"}),
    ("submit", "submit_report")],
  accept={"t": ["gas_leak"], "a": ["put it out"]},
  notes="n = one + nine others = 10; 'fire' because it ignited (LABELING.md)")

C("gs-02", "S14", "test", ["negation", "severity_negation", "multi_action"], R, [
    ("There's a smell of gas in the canteen kitchen, I think the LPG cylinder regulator is leaking. We have "
     "closed the valve and opened the windows. Nobody is hurt.",
     {"t": "gas_leak", "l": "canteen kitchen", "c": "LPG cylinder regulator is leaking",
      "e": ["LPG cylinder regulator"], "a": ["closed the valve", "opened the windows"], "n": 0, "m": "none"}),
    ("around five minutes ago", {"d": "around five minutes ago"}, ago(R, 5)),
    ("not high, medium", {"s": "medium"}),
    ("submit", "submit_report")])

C("gs-03", "S14", "dev", ["short", "follow_up"], R, [
    ("Strong gas smell in the pantry on 6th floor, everyone moved out, Facilities is checking the pipeline",
     {"t": "gas_leak", "l": "pantry on 6th floor", "a": ["everyone moved out"], "o": ["Facilities"]}),
    ("15 min ago", {"d": "15 min ago"}, ago(R, 15)),
    ("high", {"s": "high"}),
    ("submit", "submit_report")],
  accept={"a": ["checking the pipeline"]})

# ---------------------------------------------------------------- S16/S21 ceiling collapse
C("cl-01", "S16", "dev", ["count_sum", "damage", "vague_time"], R, [
    ("Part of the roof and false ceiling collapsed in the ward onto the beds. Three patients and two sanitation "
     "workers are injured, they were shifted to the emergency ward. Ceiling fans are damaged.",
     {"t": "structural", "l": "the ward", "n": 5, "p": ["Three patients", "two sanitation workers"],
      "m": "emergency_room", "k": "Ceiling fans are damaged"}),
    ("sometime yesterday night", {"d": "yesterday night"}),
    ("high", {"s": "high"}),
    ("submit", "submit_report")],
  accept={"l": ["ward"], "k": ["ceiling fans"]})

C("cl-02", "S21", "test", ["negation", "cause"], R, [
    ("Chunks of plaster of paris from the false ceiling fell near the staff chairs in the accounts section this "
     "afternoon. No one got hurt. Water is leaking from the roof because of the rain.",
     {"t": "structural", "l": "accounts section", "d": "this afternoon", "n": 0, "m": "none",
      "c": "Water is leaking from the roof"}),
    ("medium", {"s": "medium"}),
    ("submit", "submit_report")],
  accept={"l": ["near the staff chairs"], "c": ["rain"]})

C("cl-03", "S16", "dev", ["indian_english", "damage"], R, [
    ("False ceiling has fallen down in the conference hall on 2nd floor, luckily meeting was not going on. "
     "Light fittings broken.",
     {"t": "structural", "l": "conference hall on 2nd floor", "k": "Light fittings broken"}),
    ("morning around 9", {"d": "morning around 9"}, at(R, 9, 0)),
    ("medium", {"s": "medium"}),
    ("submit", "submit_report")])

# ---------------------------------------------------------------- S17 Kukatpally scaffolding collapse
R17 = "2026-10-10T11:00:00+05:30"
C("sc-01", "S17", "dev", ["narration", "count"], R17, [
    ("At the new block construction site the parapet wall on the top floor fell on workers standing on the "
     "wooden scaffolding, this morning after the heavy rain. Three workers are badly injured, the ambulance took "
     "them. We have stopped all work at the site.",
     {"t": "structural", "l": "new block construction site", "e": ["wooden scaffolding"],
      "c": "the parapet wall on the top floor fell", "d": "this morning", "n": 3, "m": "hospitalised",
      "a": ["stopped all work at the site"], "o": ["ambulance"]}),
    ("critical", {"s": "critical"}),
    ("submit", "submit_report")],
  accept={"l": ["top floor"], "c": ["heavy rain"], "m": ["emergency_room"]},
  notes="source was fatal; outcome softened")

C("sc-02", "S17", "test", ["follow_up", "multi_action"], R17, [
    ("A worker fell from the scaffolding on the fifth floor of the new block, it was raining. He is alive but "
     "bleeding from the head.",
     {"t": "fall_height", "p": ["A worker"], "e": ["scaffolding"], "l": "fifth floor of the new block",
      "c": "it was raining", "b": "head", "i": "bleeding"}),
    ("around 10", {"d": "around 10"}, at(R17, 10, 0)),
    ("critical", {"s": "critical"}),
    ("the ambulance has taken him to the hospital, supervisor has stopped work",
     {"m": "hospitalised", "a": ["stopped work"], "o": ["ambulance", "supervisor"]}),
    ("submit", "submit_report")],
  accept={"c": ["raining"]})

# ---------------------------------------------------------------- S18 Chennai school lab gas
R18 = "2026-10-10T11:30:00+05:30"
C("lb-01", "S18", "test", ["narration", "count", "follow_up"], R18, [
    ("Students in the chemistry lab on the second floor are having breathing problems and vomiting, there is some "
     "smell of chemicals. We have taken them out to the ground for fresh air and called the fire department. "
     "About thirty students.",
     {"t": "chemical", "l": "chemistry lab on the second floor", "i": "breathing problems", "n": 30,
      "a": ["taken them out to the ground", "called the fire department"], "o": ["fire department"],
      "c": "smell of chemicals", "p": ["Students"]}),
    ("around 11 o'clock", {"d": "around 11 o'clock"}, at(R18, 11, 0)),
    ("critical, three girls fainted", {"s": "critical"}),
    ("they have all been sent to the government hospital", {"m": "hospitalised"}),
    ("submit", "submit_report")],
  accept={"i": ["vomiting"]},
  notes="'three girls fainted' must not overwrite n=30")

C("lb-02", "S18", "dev", ["spill", "short"], "2026-10-10T12:00:00+05:30", [
    ("An acid bottle broke in the chemistry lab, some spilled on the floor, no one touched it. The lab assistant "
     "put sand on it.",
     {"t": "chemical", "l": "chemistry lab", "c": "acid bottle broke", "a": ["put sand on it"]}),
    ("11:40", {"d": "11:40"}, "2026-10-10T11:40:00+05:30"),
    ("low", {"s": "low"}),
    ("submit", "submit_report")],
  accept={"c": ["acid"]})

# ---------------------------------------------------------------- S19/S20 medical
R19 = "2026-10-10T06:00:00+05:30"
C("md-01", "S19", "dev", ["narration", "witness", "night"], R19, [
    ("A security supervisor collapsed at the front desk during night shift, around 2 AM. Two colleagues gave CPR "
     "and fanned him till the ambulance came. He is in hospital, stable now.",
     {"t": "medical", "p": ["A security supervisor"], "l": "front desk", "d": "around 2 AM", "g": "night shift",
      "a": ["gave CPR", "fanned him"], "w": ["Two colleagues"], "o": ["ambulance"], "m": "hospitalised"},
     at(R19, 2, 0)),
    ("high", {"s": "high"}),
    ("submit", "submit_report")])

R20 = "2026-10-10T19:10:00+05:30"
C("md-02", "S20", "test", ["urgent", "late_action"], R20, [
    ("Found one of our developers unresponsive in the third floor washroom around 7 PM. We have called the ambulance.",
     {"t": "medical", "p": ["one of our developers"], "l": "third floor washroom", "d": "around 7 PM",
      "a": ["called the ambulance"], "o": ["ambulance"], "i": "unresponsive"}, at(R20, 19, 0)),
    ("critical", {"s": "critical"}),
    ("we are doing CPR, he is not breathing", {"a": ["doing CPR"]}),
    ("submit", "submit_report")])

C("md-03", "S19", "dev", ["minor", "follow_up"], R, [
    ("A visitor fainted at the reception, he is conscious now, we gave him water and he is sitting",
     {"t": "medical", "p": ["A visitor"], "l": "reception", "i": "fainted", "a": ["gave him water"]}),
    ("just now", {"d": "just now"}, R),
    ("low", {"s": "low"}),
    ("submit", "submit_report")])

# ---------------------------------------------------------------- S21 Jalandhar seepage + hand-written leaks
C("wl-02", "S21", "test", ["damage_negation", "severity_change", "hazard_status"], R, [
    ("Rain water is leaking into the record room and computer room on first floor. We have put buckets and the "
     "staff are wiping the floor. No damage to files yet.",
     {"t": "water_leak", "l": "record room and computer room on first floor", "c": "Rain water",
      "a": ["put buckets", "wiping the floor"], "h": "ongoing"}),
    ("since this morning", {"d": "since this morning"}),
    ("low", {"s": "low"}),
    ("actually make it medium, it's close to the server rack", {"s": "medium", "e": ["server rack"]}),
    ("submit", "submit_report")],
  accept={"c": ["rain"]},
  notes="'No damage to files yet' must leave k empty")

C("wl-03", "H", "dev", ["asset_name", "follow_up"], R, [
    ("Ceiling is dripping water in the server room, right on top of rack 2",
     {"t": "water_leak", "l": "server room", "e": ["rack 2"]}),
    ("noticed it 20 minutes ago", {"d": "20 minutes ago"}, ago(R, 20)),
    ("critical, IT is shutting down the rack", {"s": "critical", "a": ["shutting down the rack"], "o": ["IT"]}),
    ("submit", "submit_report")],
  accept={"l": ["rack 2"]})

C("wl-04", "H", "test", ["no_op_mid", "off_topic"], "2026-10-10T16:20:00+05:30", [
    ("Water leak in the 3rd floor gents washroom, pipe below the sink",
     {"t": "water_leak", "l": "3rd floor gents washroom", "c": "pipe below the sink"}),
    ("what time is it now?", "no_op"),
    ("ok say around 4", {"d": "around 4"}, "2026-10-10T16:00:00+05:30"),
    ("low", {"s": "low"}),
    ("submit", "submit_report")])

# ---------------------------------------------------------------- S22 University of Hyderabad laptop theft
C("sec-01", "S22", "dev", ["theft", "follow_up"], R, [
    ("Sixty laptops are missing from the computer lab. Looks like someone broke open the window last night and "
     "cut the CCTV cables.",
     {"t": "security", "l": "computer lab", "d": "last night", "k": "Sixty laptops are missing",
      "c": "broke open the window", "e": ["CCTV cables"]}),
    ("high", {"s": "high"}),
    ("we have informed the police and our security team",
     {"o": ["police", "security team"], "a": ["informed the police and our security team"]}),
    ("submit", "submit_report")],
  accept={"k": ["sixty laptops"], "c": ["cut the CCTV cables"]})

C("sec-02", "S22", "test", ["intrusion", "single_narration"], "2026-10-10T14:00:00+05:30", [
    ("An unknown person was roaming on the 5th floor without ID card, security escorted him out, around 1 PM",
     {"t": "security", "l": "5th floor", "d": "around 1 PM", "a": ["security escorted him out"],
      "o": ["security"], "p": ["An unknown person"]}, "2026-10-10T13:00:00+05:30"),
    ("low", {"s": "low"}),
    ("submit", "submit_report")],
  accept={"a": ["escorted him out"]})

C("sec-03", "S22", "dev", ["theft", "first_person"], R, [
    ("My laptop was stolen from my desk on the 4th floor during lunch",
     {"t": "security", "k": "My laptop was stolen", "l": "my desk on the 4th floor", "d": "during lunch"}),
    ("medium", {"s": "medium"}),
    ("I have informed security and they are checking CCTV", {"o": ["security"], "a": ["checking CCTV"]}),
    ("submit", "submit_report")],
  accept={"k": ["laptop"], "a": ["informed security"]})

# ---------------------------------------------------------------- hand-written gap fillers
C("eq-01", "H", "dev", ["asset_name", "equipment"], R, [
    ("AHU-2 on the terrace tripped and the whole 3rd floor has no cooling since 2 PM",
     {"t": "equipment_failure", "e": ["AHU-2"], "l": "terrace", "d": "since 2 PM"}, at(R, 14, 0)),
    ("low", {"s": "low"}),
    ("submit", "submit_report")],
  accept={"l": ["3rd floor"], "k": ["no cooling"]})

C("oth-01", "H", "dev", ["ambiguous_type", "follow_up"], R, [
    ("There is a strong burning smell near the pantry on 3rd floor, we can't find where it is coming from",
     {"t": "other", "l": "near the pantry on 3rd floor"}),
    ("started around 10 minutes ago", {"d": "around 10 minutes ago"}, ago(R, 10)),
    ("medium", {"s": "medium"}),
    ("Facilities team is checking the wiring", {"o": ["Facilities team"], "a": ["checking the wiring"]}),
    ("submit", "submit_report")],
  accept={"t": ["fire", "electrical"], "c": ["burning smell"]})


# ---------------------------------------------------------------- expand + write
def merge(form, args):
    for k, v in args.items():
        if k in LIST_FIELDS:
            cur = form.setdefault(k, [])
            cur.extend(x for x in v if x not in cur)
        else:
            form[k] = v


def next_ask(form):
    for k in ASK_ORDER:
        if k not in form:
            return k
    return None


def expand(c):
    form, turns, started = {}, [], False
    for idx, t in enumerate(c["turns"]):
        text, gold = t[0], t[1]
        gold_time = t[2] if len(t) > 2 else None
        asked = next_ask(form) if started else None
        tool, args = (gold, {}) if isinstance(gold, str) else ("update_report", gold)
        turns.append({"i": idx, "asked": asked, "user": text, "form_before": json.loads(json.dumps(form)),
                      "gold": {"tool": tool, "args": args}, **({"gold_time": gold_time} if gold_time else {})})
        merge(form, args)
        started = True
        if tool == "cancel_report":
            break
    ended = turns[-1]["gold"]["tool"]
    out = {k: c[k] for k in ("id", "source", "split", "tags", "ref_time")}
    out.update(turns=turns, gold_final=form, final_tool=ended, accept=c["accept"])
    if c["notes"]:
        out["notes"] = c["notes"]
    return out


if __name__ == "__main__":
    path = Path(__file__).with_name("cases.jsonl")
    with path.open("w") as f:
        for c in CASES:
            f.write(json.dumps(expand(c), ensure_ascii=False) + "\n")
    print(f"wrote {len(CASES)} cases to {path}")
