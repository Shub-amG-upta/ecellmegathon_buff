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

from app.form import keyed_fields

_KEY_MEANING = {
    "t": "incident type",
    "s": "severity",
    "l": "location, as the user said it",
    "d": "when it happened: copy the user's time phrase exactly (e.g. \"around 10 AM\", \"yesterday evening\")",
    "p": "people involved or hurt",
    "e": "equipment or assets involved",
    "a": "actions already taken",
    "w": "witnesses",
}

_EXAMPLES = """\
Example 1
Form: {}
Asked: none
User: there's smoke coming out of the printer on the third floor, I unplugged it, nobody's hurt
Answer: {"tool":"update_report","args":{"t":"fire_smoke","l":"third floor","e":["printer"],"a":["unplugged the printer"]}}

Example 2
Form: {"t":"slip_trip_fall","l":"main staircase","d":"this morning"}
Asked: s
User: uh I'd say medium, Ravi saw it happen
Answer: {"tool":"update_report","args":{"s":"medium","w":["Ravi"]}}

Example 3
Form: {"t":"water_leak","s":"low","l":"pantry","d":"just now"}
Asked: none
User: yes that's right, go ahead and submit
Answer: {"tool":"submit_report"}"""


def system_prompt(schema: dict) -> str:
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
              "",
              _EXAMPLES]
    return "\n".join(lines)


def user_message(form_compact: dict, asked: str | None, text: str) -> str:
    form = json.dumps(form_compact, ensure_ascii=False, separators=(",", ":"))
    return f"Form: {form}\nAsked: {asked or 'none'}\nUser: {text}"
