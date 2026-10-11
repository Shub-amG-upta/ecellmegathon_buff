#!/usr/bin/env python3
"""Typed Hinglish eval set: 10 hand-written dialogs in the eval/nlu/cases.jsonl format, for measuring the
extraction on Hindi written in English letters (what STT gives when it leaves a Hinglish clip untranslated).

Source of truth: DIALOGS below. Run this file to regenerate cases.jsonl (byte-identical on rerun), then
    python eval/nlu/validate_cases.py eval/hinglish/cases.jsonl
All 10 dialogs are hand-written (source H); none is a translation of a test-split case. Labels follow
eval/nlu/LABELING.md: text values are copied from the user's words (Hindi or English), times are the phrase.
form_before, gold_final and gold_time are computed here, not typed. Stdlib only.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from app.timeparse import resolve  # noqa: E402

REF = "2026-10-10T15:00:00+05:30"

# (id, tags, [(asked, user, tool, args), ...], accept, english meaning of turn 0 for reviewers)
DIALOGS = [
    ("hi-01", ["ps_example", "hinglish", "follow_up"], [
        (None, "Conference Room 2 ke paas paani leak ho raha hai, subah 10 baje se. Maintenance ne area isolate "
               "kar diya hai.", "update_report",
         {"t": "water_leak", "l": "Conference Room 2 ke paas", "d": "subah 10 baje", "a": ["area isolate kar diya"]}),
        ("s", "medium hai", "update_report", {"s": "medium"}),
        (None, "haan submit kar do", "submit_report", {}),
    ], {"l": ["Conference Room 2"], "d": ["subah 10 baje se"], "a": ["Maintenance ne area isolate kar diya hai"]},
     "Water leaking near Conference Room 2 since 10 in the morning; maintenance has isolated the area."),
    ("hi-02", ["hinglish", "follow_up"], [
        (None, "Third floor ki pantry mein aag lag gayi hai! Sab log bahar aa gaye, fire extinguisher use kiya.",
         "update_report", {"t": "fire", "l": "Third floor ki pantry",
                           "a": ["Sab log bahar aa gaye", "fire extinguisher use kiya"]}),
        ("d", "abhi 10 minute pehle", "update_report", {"d": "abhi 10 minute pehle"}),
        ("s", "high", "update_report", {"s": "high"}),
        (None, "submit karo", "submit_report", {}),
    ], {"l": ["pantry"], "d": ["10 minute pehle"], "e": ["fire extinguisher"]},
     "Fire in the third-floor pantry; everyone got out, used a fire extinguisher."),
    ("hi-03", ["hinglish", "follow_up"], [
        (None, "Kal raat 8 baje ward 3 ke corridor mein ek nurse fisal gayi, floor geela tha. Uske ghutne mein "
               "chot aayi.", "update_report",
         {"t": "slip_trip", "l": "ward 3 ke corridor", "d": "Kal raat 8 baje", "p": ["ek nurse"]}),
        ("s", "low, bas thodi chot hai", "update_report", {"s": "low"}),
        (None, "haan", "submit_report", {}),
    ], {"l": ["ward 3"], "p": ["nurse"]},
     "Last night at 8 a nurse slipped in the ward 3 corridor, the floor was wet; she hurt her knee."),
    ("hi-04", ["hinglish", "negation", "correction"], [
        (None, "Second floor ke lab mein switchboard mein short circuit hua, sparks aaye. Electrician ko bula "
               "liya hai.", "update_report",
         {"t": "electrical", "l": "Second floor ke lab", "e": ["switchboard"], "a": ["Electrician ko bula liya"]}),
        ("d", "dopahar 2 baje", "update_report", {"d": "dopahar 2 baje"}),
        ("s", "high nahi, medium", "update_report", {"s": "medium"}),
        (None, "sorry, time 2 nahi, dopahar 3 baje tha", "update_report", {"d": "dopahar 3 baje"}),
        (None, "submit kar do", "submit_report", {}),
    ], {"l": ["lab"], "a": ["Electrician ko bula liya hai"]},
     "Short circuit in the switchboard of the second-floor lab, sparks; electrician called."),
    ("hi-05", ["hinglish", "follow_up"], [
        (None, "Lift number 2 ground aur first floor ke beech mein atak gayi, andar do log phase hain. Security "
               "ko bata diya hai.", "update_report",
         {"t": "lift", "l": "ground aur first floor ke beech", "e": ["Lift number 2"], "p": ["do log"],
          "a": ["Security ko bata diya"]}),
        ("d", "abhi abhi", "update_report", {"d": "abhi abhi"}),
        ("s", "critical", "update_report", {"s": "critical"}),
        (None, "haan ji", "submit_report", {}),
    ], {"l": ["Lift number 2", "ground aur first floor ke beech mein"], "e": ["Lift"]},
     "Lift 2 is stuck between ground and first floor with two people inside; security informed."),
    ("hi-06", ["hinglish", "follow_up"], [
        (None, "Canteen kitchen mein gas ki smell aa rahi hai, cylinder ka valve band kar diya hai, sabko bahar "
               "nikal diya.", "update_report",
         {"t": "gas_leak", "l": "Canteen kitchen", "e": ["cylinder"],
          "a": ["cylinder ka valve band kar diya", "sabko bahar nikal diya"]}),
        ("d", "aadha ghanta pehle", "update_report", {"d": "aadha ghanta pehle"}),
        ("s", "high hai sir", "update_report", {"s": "high"}),
        (None, "submit karo", "submit_report", {}),
    ], {"a": ["valve band kar diya"]},
     "Gas smell in the canteen kitchen; closed the cylinder valve, got everyone out."),
    ("hi-07", ["hinglish", "theft", "witness"], [
        (None, "Mera laptop chori ho gaya, 4th floor pe meri desk se, lunch ke time.", "update_report",
         {"t": "security", "l": "meri desk", "d": "lunch ke time", "e": ["laptop"]}),
        ("s", "medium", "update_report", {"s": "medium"}),
        (None, "Ravi ne kisi ko bag le jaate dekha tha", "update_report", {"w": ["Ravi"]}),
        (None, "haan submit", "submit_report", {}),
    ], {"l": ["4th floor", "4th floor pe meri desk", "4th floor pe meri desk se"]},
     "My laptop was stolen from my desk on the 4th floor at lunch time."),
    ("hi-08", ["hinglish", "follow_up"], [
        (None, "Reception pe ek visitor behosh ho gaya, abhi hosh mein hai, humne paani diya aur doctor ko bulaya.",
         "update_report", {"t": "medical", "l": "Reception", "p": ["ek visitor"],
                           "a": ["paani diya", "doctor ko bulaya"]}),
        ("d", "lagbhag 11 baje", "update_report", {"d": "lagbhag 11 baje"}),
        ("s", "low", "update_report", {"s": "low"}),
        (None, "haan", "submit_report", {}),
    ], {"p": ["visitor"], "a": ["humne paani diya"]},
     "A visitor fainted at reception, is conscious now; we gave him water and called a doctor."),
    ("hi-09", ["hinglish", "cancel"], [
        (None, "Basement parking mein pipe se paani tapak raha hai", "update_report",
         {"t": "water_leak", "l": "Basement parking", "e": ["pipe"]}),
        ("d", "ek minute, galat report ho gaya, cancel karo", "cancel_report", {}),
    ], {},
     "Water is dripping from a pipe in the basement parking. (Then: wait, wrong report, cancel.)"),
    ("hi-10", ["hinglish", "noise", "follow_up"], [
        (None, "Store room mein chemical ki bottle gir ke toot gayi, floor pe acid fail gaya hai", "update_report",
         {"t": "chemical", "l": "Store room", "e": ["chemical ki bottle"]}),
        ("d", "yeh record ho raha hai kya?", "no_op", {}),
        ("d", "shaam 5 baje", "update_report", {"d": "shaam 5 baje"}),
        ("s", "medium", "update_report", {"s": "medium"}),
        (None, "submit kar dijiye", "submit_report", {}),
    ], {"e": ["bottle"]},
     "A chemical bottle fell and broke in the store room; acid spread on the floor."),
]


def merge(form: dict, args: dict) -> dict:
    out = dict(form)
    for k, v in args.items():
        out[k] = list(dict.fromkeys((out.get(k) or []) + v)) if isinstance(v, list) else v
    return out


def build() -> list[dict]:
    ref = datetime.fromisoformat(REF)
    cases = []
    for cid, tags, turns, accept, english in DIALOGS:
        form, out_turns = {}, []
        for i, (asked, user, tool, args) in enumerate(turns):
            t = {"i": i, "asked": asked, "user": user, "form_before": form,
                 "gold": {"tool": tool, "args": args}}
            if "d" in args and resolve(args["d"], ref)["resolved"]:
                t["gold_time"] = resolve(args["d"], ref)["iso"]
            out_turns.append(t)
            form = merge(form, args) if tool == "update_report" else form
        cases.append({"id": cid, "source": "H", "split": "dev", "lang": "hinglish", "tags": tags, "ref_time": REF,
                      "english": english, "turns": out_turns, "gold_final": form,
                      "final_tool": turns[-1][2], "accept": accept})
    return cases


if __name__ == "__main__":
    path = HERE / "cases.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in build()))
    print(f"wrote {path}: {len(DIALOGS)} dialogs, {sum(len(d[2]) for d in DIALOGS)} turns")
