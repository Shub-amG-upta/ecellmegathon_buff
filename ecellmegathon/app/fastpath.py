"""
Fast path: answer a turn in code, without the LLM, when the utterance is
unambiguous. Writing output is ~80% of LLM time on the NPU, so a turn that
skips the model saves ~0.7-1 s (ESTIMATE from FieldMind's decode rate).

Only exact, closed-vocabulary answers qualify; anything else returns None and
goes to the LLM as before:

  asked severity    "medium", "it's high", "I'd say low", "critical please"  -> s
  asked type        an incident type said as its name ("water leak", "fire")  -> t
  asked time        an utterance made only of time words that timeparse
                    resolves ("3 PM", "around 10 AM", "quarter past two")     -> d
  review            "submit", "yes submit it", "go ahead"                     -> submit_report
  any state         "cancel", "cancel this report", "discard it"              -> cancel_report

A negation or a correction ("not high, medium", "no wait, 3:30") never
qualifies: those words are not in any vocabulary below. Checked against every
turn of eval/nlu/cases.jsonl: when the fast path answers, it matches the gold
(tests/test_fastpath.py).

Stdlib only.
"""
from __future__ import annotations

import re

from app.timeparse import resolve

FILLERS = {"uh", "um", "umm", "hmm", "ok", "okay", "so", "well", "i", "think", "guess", "would", "say",
           "i'd", "it's", "its", "it", "is", "was", "please", "maybe", "probably", "just", "the", "severity",
           "level", "pretty", "about", "around", "sir", "madam", "ma'am"}
SEVERITY_WORDS = {"low": "low", "minor": "low", "medium": "medium", "moderate": "medium",
                  "high": "high", "serious": "high", "critical": "critical"}
SUBMIT = {"submit", "submit it", "yes submit", "yes submit it", "submit the report", "please submit",
          "yes please submit", "go ahead", "yes go ahead", "go ahead and submit", "ok submit", "okay submit",
          "now submit", "yes", "yes please", "confirm", "correct submit", "that's correct submit",
          "submit please", "okay submit it", "ok submit it"}
CANCEL = {"cancel", "cancel it", "cancel this", "cancel the report", "cancel this report", "discard it",
          "discard the report", "discard this report", "never mind cancel", "please cancel"}
TIME_WORDS = {"at", "around", "about", "approximately", "roughly", "by", "since", "today", "yesterday", "this",
              "morning", "afternoon", "evening", "night", "tonight", "last", "am", "pm", "a.m.", "p.m.", "a", "m",
              "p", "o'clock", "oclock", "clock", "quarter", "half", "past", "to", "ago", "back", "minute", "minutes",
              "min", "mins", "a.m", "p.m", "hour", "hours", "hr", "hrs", "an", "just", "now", "noon", "midnight", "or", "so",
              "ish", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
              "twelve", "fifteen", "twenty", "thirty", "forty", "fifty", "few", "couple", "of", "the", "in"}


def _norm(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[“”\"!?,.;]+(?=\s|$)", " ", t)          # punctuation at word ends (keeps 10:30, a.m.)
    return re.sub(r"\s+", " ", t).strip()


def answer(text: str, asked: str | None, state: str, type_values: list[str]) -> dict | None:
    """A tool call for this turn, or None to use the LLM."""
    t = _norm(text)
    if not t:
        return None
    words = t.split()
    if t in CANCEL:
        return {"tool": "cancel_report"}
    if state == "review" and t in SUBMIT:
        return {"tool": "submit_report"}
    if state != "collecting":
        return None
    if asked == "s":
        sev = [SEVERITY_WORDS[w] for w in words if w in SEVERITY_WORDS]
        rest = [w for w in words if w not in SEVERITY_WORDS and w not in FILLERS]
        if len(sev) == 1 and not rest:
            return {"tool": "update_report", "args": {"s": sev[0]}}
    if asked == "t":
        spoken = {v.replace("_", " "): v for v in type_values if v != "other"}
        if t in spoken:
            return {"tool": "update_report", "args": {"t": spoken[t]}}
    if asked == "d":
        only_time = all(w in TIME_WORDS or re.fullmatch(r"\d{1,2}(:\d{2})?(am|pm)?", w) for w in words)
        if only_time and resolve(text)["resolved"]:
            return {"tool": "update_report", "args": {"d": text.strip().rstrip(".!?,")}}
    return None
