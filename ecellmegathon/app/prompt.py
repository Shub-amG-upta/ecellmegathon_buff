"""
The prompt for the one LLM call per turn.

SYSTEM is identical on every turn, so llama-server can reuse its prefill
(cache_prompt). Each turn's user message carries only the form so far, the
field just asked about, and what the user said. Keep the whole thing well
under 600 tokens: prefill is cheap on the NPU, but not free.

Stdlib only.
"""
from __future__ import annotations

import json
import os

from app.form import keyed_fields

# Which system prompt: "1" (v1-v4) or "2" (v5: field definitions with "never" rules, type rules with
# contrasting cases, a dense worked example). PROMPT=1|2 overrides it for comparisons.
DEFAULT_PROMPT = "1"     # v6 = v4 + compact answers: prompt v2 (v5) failed its rule on the board (DECISION_v4_v5.md)

_KEY_MEANING = {
    "t": "incident type",
    "s": "severity",
    "l": "location, as the user said it",
    "d": "when it happened: copy the user's time phrase exactly (e.g. \"around 10 AM\", \"yesterday evening\")",
    "p": "people hurt, nearly hurt, or who helped on the spot",
    "e": "equipment or assets involved",
    "a": "actions already taken, by anyone, including calling or informing someone",
    "w": "witnesses: only people said to have seen it (never the speaker)",
    # FORM=15 only (schema/incident_form_15.json); the 10-field prompt never lists these
    "g": "task: what the person was doing just before it happened",
    "c": "immediate cause, only if the user names it (\"floor was wet\", \"not locked out\")",
    "k": "property or equipment damage, as said (\"the mast is bent\")",
    "h": "hazard status",
    "n": "who has been informed or called (ambulance, 108, supervisor, police, EHS)",
}

# FORM=15 only: extra rules and one example with the context fields. Shares no name, place or wording with
# eval/nlu15/cases.jsonl.
_RULES_15 = [
    "- h: controlled if the user says it is stopped, out or made safe; ongoing if it is still happening.",
    "- A call or message to someone is an action (a) and also goes in n.",
    "- An injury is never damage (k); a cause (c) is never just the event itself.",
]
_EXAMPLE_15 = """

Example 4
Form: {}
Asked: none
User: Ramesh was refilling the hydraulic press when a hose burst because the clamp was missing. Oil is all over \
the bay floor and the press guard is cracked. We switched the pump off and told the shift supervisor.
Answer: {"tool":"update_report","args":{"t":"chemical","p":["Ramesh"],"e":["hydraulic press"],\
"a":["switched the pump off","told the shift supervisor"],"g":"refilling the hydraulic press",\
"c":"the clamp was missing","k":["the press guard is cracked"],"h":"controlled","n":["the shift supervisor"]}}"""

_EXAMPLES = """\
Example 1
Form: {}
Asked: none
User: there's smoke coming out of the printer on the third floor, I unplugged it and told the floor warden, nobody's hurt
Answer: {"tool":"update_report","args":{"t":"fire","l":"third floor","e":["printer"],"a":["unplugged it","told the floor warden"]}}

Example 2
Form: {"t":"slip_trip","l":"main staircase","d":"this morning"}
Asked: s
User: uh I'd say medium, Imran saw it happen
Answer: {"tool":"update_report","args":{"s":"medium","w":["Imran"]}}

Example 3
Form: {"t":"water_leak","s":"low","l":"loading dock","d":"just now"}
Asked: none
User: yes that's right, go ahead and submit
Answer: {"tool":"submit_report"}"""


def prompt_version() -> str:
    v = os.environ.get("PROMPT", DEFAULT_PROMPT)
    if v not in ("1", "2"):
        raise ValueError(f"PROMPT must be 1 or 2, not {v!r}")
    return v


def system_prompt(schema: dict, answer: str | None = None) -> str:
    from app.form import answer_mode
    text = system_prompt_v2(schema) if prompt_version() == "2" else system_prompt_v1(schema)
    if hinglish():
        text = add_hinglish(text)
    return compact_prompt(text) if (answer or answer_mode()) == "compact" else text


# ---- Hinglish input (HINGLISH=1, on by default; HINGLISH=0 = English-only prompt) ---------------------------------------------------------
# The app stays in English. STT runs in translate mode (board/voice_up.sh, HINGLISH=1), so Hindi speech
# mostly arrives as English text; a Hinglish clip Whisper takes for English arrives in Latin-letter Hindi.
# These lines tell the model so. Text fields are still copied as said (the gate keeps only the user's own
# words), so a Hinglish answer gives Hinglish text values; times are resolved by app/timeparse.py.
# Off by default: it changes the prompt, so it is measured before it is used (eval/hinglish/README.md).
_HINGLISH_RULES = [
    "- The user may speak Hinglish: Hindi written in English letters, mixed with English words. Fill the",
    "  same fields. Copy text fields in the user's own words, Hindi or English; leave out a trailing",
    "  mein / pe / par after a place. Copy a Hindi time phrase exactly (\"kal shaam 6 baje\").",
    "- \"nahi\" means not: \"high nahi, medium\" is medium.",
]
_HINGLISH_EXAMPLE = """

Example H
Form: {}
Asked: none
User: sir godown number 2 mein ek rack gir gaya, Ramesh ka pair dab gaya, humne first aid diya, kal shaam 6 baje hua
Answer: {"tool":"update_report","args":{"t":"struck_by","l":"godown number 2","d":"kal shaam 6 baje",\
"p":["Ramesh"],"e":["rack"],"a":["humne first aid diya"]}}"""


def hinglish() -> bool:
    v = os.environ.get("HINGLISH", "1")
    if v not in ("0", "1"):
        raise ValueError(f"HINGLISH must be 0 or 1, not {v!r}")
    return v == "1"


def add_hinglish(text: str) -> str:
    """The prompt with the Hinglish rules after "Rules:" and one Hinglish example at the end."""
    assert "\nRules:\n" in text
    return text.replace("\nRules:\n", "\nRules:\n" + "\n".join(_HINGLISH_RULES) + "\n", 1) + _HINGLISH_EXAMPLE


_COMPACT_TOOLS = """Answers:
- an object with only the fields the user stated in this message: this updates the report
- "S": the user confirms the report or asks to submit it
- "C": the user wants to discard the report
- "N": the message holds nothing for the report (a question, noise, small talk)
"""


def compact_prompt(text: str) -> str:
    """The same prompt for compact answers (v6): the tool list becomes the four answer shapes, and every
    example answer is written the compact way."""
    import re
    from app.form import to_compact, to_tool_call
    text = text.replace("Reply with one JSON tool call and nothing else.", "Reply with one JSON value and nothing else.")
    text = re.sub(r"Tools:\n(?:- .*\n)+", _COMPACT_TOOLS, text)
    text = text.replace("Fields (args keys):", "Fields (keys of the object):")
    return re.sub(r"^Answer: (.+)$", lambda m: "Answer: " + to_compact(to_tool_call(json.loads(m.group(1)))),
                  text, flags=re.M)


def system_prompt_v1(schema: dict) -> str:
    lines = ["You fill in a workplace incident report from what the user says.",
             "Reply with one JSON tool call and nothing else.",
             "",
             "Tools:",
             "- update_report: args hold only the fields the user actually stated in this message",
             "- submit_report: the user confirms the report or asks to submit it",
             "- cancel_report: the user wants to discard the report",
             "- no_op: the message holds nothing for the report",
             "",
             "Fields (args keys):"]
    for f in keyed_fields(schema):
        meaning = _KEY_MEANING[f["key"]]
        if f["kind"] == "enum":
            meaning += ": one of " + ", ".join(f["values"])
        elif f["kind"] == "text_list":
            meaning += " (a list)"
        lines.append(f"- {f['key']}: {meaning}")
    lines += ["",
              "Rules:",
              "- Never guess. Leave out any field the user did not mention.",
              "- Use the user's own words for text fields; keep them short.",
              "- \"Asked\" is the field the assistant just asked about: a short answer belongs there.",
              "- If the user corrects something, send the corrected field again.",
              "- Only pick a severity if the user says how serious it is.",
              "- Type: if someone was hurt or nearly hurt, pick how (slip_trip, fall_height, struck_by, caught_in).",
              "  fire if there were flames or smoke; gas_leak only without fire; chemical for spills, splashes",
              "  and fumes; structural when something cracks or collapses; equipment_failure when something",
              "  broke and nobody was hurt; medical when someone fell ill."]
    if _has_context_fields(schema):
        lines += _RULES_15
    lines += ["",
              _EXAMPLES + (_EXAMPLE_15 if _has_context_fields(schema) else "")]
    return "\n".join(lines)


def _has_context_fields(schema: dict) -> bool:
    """The 15-field form (FORM=15): its prompt adds the rules and the example for the context fields."""
    return any(f["key"] == "n" for f in keyed_fields(schema))


# ---- prompt v2 (v5) ---------------------------------------------------------------------------------------
# Built from the board runs' error patterns (FINDINGS.md): list fields filled with the wrong things, places
# cut short, fields dropped from long first sentences, incident-type confusions. Examples share no name,
# place or wording with eval/nlu/cases.jsonl (checked by tests/test_prompt.py).
_MEANING_V2 = {
    "t": "incident type",
    "s": "severity, only if the user says how serious it is (minor, medium, serious, critical, ...)",
    "l": "location: the full place as said, room and floor together (\"store yard on the first floor\", "
         "not just \"first floor\")",
    "d": "when it happened: copy the user's time phrase exactly (\"half past six\", \"just after lunch\", "
         "\"an hour back\")",
    "p": "persons involved: people hurt, nearly hurt, or who helped on the spot. Never services or buildings "
         "(ambulance, hospital, fire brigade, police)",
    "e": "equipment: machines, tools, vehicles and objects involved (drill, trolley, pallet jack, heater). Never a "
         "place (room, floor, area)",
    "a": "actions taken: what someone did in response (shut the valve, called a doctor, told the "
         "shift lead). Never what happened in the incident itself",
    "w": "witnesses: people the user says saw it (\"Kiran saw it\" -> Kiran). Never the speaker",
    **{k: _KEY_MEANING[k] for k in ("g", "c", "k", "h", "n")},     # FORM=15 only
}
_TYPES_V2 = """Incident types, how to choose:
- slip_trip: slipped or tripped on the same level (wet floor, cable, step)
- fall_height: fell from something high (ladder, scaffolding, roof, platform)
- struck_by: something fell on, hit or nearly hit a person (falling object, vehicle)
- caught_in: a hand or body caught in a machine
- fire: any flames or smoke, even if a gas leak or a short circuit started it
- gas_leak: gas smell or leak with no fire; chemical: spills, splashes, fumes (oil too)
- electrical: shock, sparks or short circuit with no fire
- security: theft, missing items, intrusion, tailgating
- structural: a ceiling, wall, glass or scaffolding cracks or collapses
- equipment_failure: something broke down and nobody was hurt; lift: a lift stuck or fell
- medical: someone fell ill or collapsed; water_leak: water leaking; other: none of these"""
# v1's examples 1-3 with their time phrases changed: "this morning" and "just now" are eval answers.
_EXAMPLES_V2 = _EXAMPLES.replace('"d":"this morning"', '"d":"early today"').replace(
    '"d":"just now"', '"d":"a while ago"') + """

Example 4
Form: {}
Asked: none
User: At 4:50 today a delivery van reversed into a pallet stack in the dispatch area on level B2. \
The stack fell on Suresh, one of our loaders, and hurt his foot. Gopal saw it happen. We bandaged his foot and \
told the safety officer. I'd say it's high.
Answer: {"tool":"update_report","args":{"t":"struck_by","s":"high","l":"dispatch area on level B2",\
"d":"At 4:50 today","p":["Suresh"],"e":["delivery van","pallet stack"],\
"a":["bandaged his foot","told the safety officer"],"w":["Gopal"]}}

Example 5
Form: {"t":"water_leak","l":"pump room"}
Asked: d
User: wait, is this being recorded?
Answer: {"tool":"no_op"}"""


def system_prompt_v2(schema: dict) -> str:
    lines = ["You fill in a workplace incident report from what the user says.",
             "Reply with one JSON tool call and nothing else.",
             "",
             "Tools:",
             "- update_report: args hold only the fields the user stated in this message",
             "- submit_report: the user confirms the report or asks to submit it",
             "- cancel_report: the user wants to discard the report",
             "- no_op: the message holds nothing for the report (a question, noise, small talk)",
             "",
             "Fields (args keys):"]
    for f in keyed_fields(schema):
        meaning = _MEANING_V2[f["key"]]
        if f["key"] in ("s", "h"):
            meaning += ": one of " + ", ".join(f["values"])
        elif f["kind"] == "text_list":
            meaning += " (a list)"
        lines.append(f"- {f['key']}: {meaning}")
    lines += ["",
              _TYPES_V2,
              "",
              "Rules:",
              "- Never guess. Leave out any field the user did not mention.",
              "- In a long message, fill every field it mentions: type, place, time, severity, people, "
              "equipment, actions, witnesses.",
              "- Copy text fields from the user's own words.",
              "- Don't repeat fields that are already in Form, unless the user changes them.",
              "- \"Asked\" is the field the assistant just asked about: a short answer belongs there.",
              "- If the user corrects something, send the corrected field again."]
    if _has_context_fields(schema):
        lines += _RULES_15
    lines += ["",
              _EXAMPLES_V2 + (_EXAMPLE_15.replace("Example 4", "Example 6") if _has_context_fields(schema) else "")]
    return "\n".join(lines)


def user_message(form_compact: dict, asked: str | None, text: str) -> str:
    form = json.dumps(form_compact, ensure_ascii=False, separators=(",", ":"))
    return f"Form: {form}\nAsked: {asked or 'none'}\nUser: {text}"
