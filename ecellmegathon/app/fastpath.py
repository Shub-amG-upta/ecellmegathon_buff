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
  asked location    a short plain place, at most 8 words, nothing but the place
                    ("meter room", "near gate 3", "the store room on ground floor") -> l
                    (no verb, pronoun, severity, time or correction word)
  review            "submit", "yes submit it", "go ahead"                     -> submit_report
  any state         "cancel", "cancel this report", "discard it"              -> cancel_report

Hinglish (Hindi in Latin letters, when STT leaves it untranslated) is handled the same way, with a closed
list of its own words: "haan, submit kar do", "cancel karo", "medium hai", "subah 10 baje",
"conference room 2 mein". "nahi" / "mat" (not / don't) are in no vocabulary, so a Hinglish negation also
goes to the LLM.

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
           "level", "pretty", "about", "around", "sir", "madam", "ma'am",
           # Hinglish fillers: "medium hai", "mujhe lagta hai high", "critical hai sir"
           "hai", "tha", "thi", "ji", "toh", "yeh", "ye", "woh", "bhai", "bas", "mujhe", "lagta", "lagti",
           "main", "keh", "sakte", "hain"}
SEVERITY_WORDS = {"low": "low", "minor": "low", "medium": "medium", "moderate": "medium",
                  "high": "high", "serious": "high", "critical": "critical",
                  "bad": "high", "major": "high", "emergency": "critical"}
# Everyday answers to the severity question, on the scale of eval/nlu/LABELING.md (not serious -> low,
# serious / bad -> high, very serious / emergency -> critical). The whole answer must be one of these (fillers
# aside); anything else still goes to the LLM.
SEVERITY_PHRASES = {
    "low": ["not serious", "nothing serious", "not that serious", "not very serious", "not too serious",
            "nothing major", "not major", "not too bad", "not that bad", "not bad", "nothing much", "very minor",
            "quite minor", "just minor", "small", "very small", "a small one", "kam", "thoda sa", "zyada nahi",
            "kuch khaas nahi", "halka"],
    "medium": ["in between", "somewhere in between", "average", "kind of moderate", "beech ka", "theek thaak"],
    "high": ["quite serious", "fairly serious", "quite bad", "really bad", "a major one", "zyada", "serious hai"],
    "critical": ["very serious", "extremely serious", "very very serious", "life threatening", "life-threatening",
                 "very bad", "extremely bad", "an emergency", "bahut serious", "bahut zyada", "bahut kharab"],
}
_SEVERITY_PHRASE = {ph: lv for lv, phs in SEVERITY_PHRASES.items() for ph in phs}
SUBMIT = {"submit", "submit it", "yes submit", "yes submit it", "submit the report", "please submit",
          "yes please submit", "go ahead", "yes go ahead", "go ahead and submit", "ok submit", "okay submit",
          "now submit", "yes", "yes please", "confirm", "correct submit", "that's correct submit",
          "submit please", "okay submit it", "ok submit it",
          # Hinglish
          "haan", "haan ji", "ji haan", "haan submit", "haan submit karo", "haan submit kar do",
          "haan submit kardo", "submit karo", "submit kar do", "submit kardo", "submit kar dijiye",
          "submit kar dijie", "report submit karo", "report submit kar do", "haan report submit kar do",
          "haan theek hai submit karo", "theek hai submit karo", "ok submit karo", "okay submit karo"}
CANCEL = {"cancel", "cancel it", "cancel this", "cancel the report", "cancel this report", "discard it",
          "discard the report", "discard this report", "never mind cancel", "please cancel",
          # Hinglish
          "cancel karo", "cancel kar do", "cancel kardo", "cancel kar dijiye", "report cancel karo",
          "report cancel kar do", "isko cancel karo", "isko cancel kar do"}
TIME_WORDS = {"at", "around", "about", "approximately", "roughly", "by", "since", "today", "yesterday", "this",
              "morning", "afternoon", "evening", "night", "tonight", "last", "am", "pm", "a.m.", "p.m.", "a", "m",
              "p", "o'clock", "oclock", "clock", "quarter", "half", "past", "to", "ago", "back", "minute", "minutes",
              "min", "mins", "a.m", "p.m", "hour", "hours", "hr", "hrs", "an", "just", "now", "noon", "midnight", "or", "so",
              "ish", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
              "twelve", "fifteen", "twenty", "thirty", "forty", "fifty", "few", "couple", "of", "the", "in",
              # Hinglish time words (app/timeparse.py rewrites them)
              "baje", "subah", "dopahar", "shaam", "sham", "raat", "kal", "aaj", "abhi", "pehle", "pahle",
              "ghante", "ghanta", "minute", "mint", "lagbhag", "kareeb", "karib", "saadhe", "sadhe", "sawa",
              "paune", "dedh", "dhai", "aadha", "aadhe", "ke", "aas", "paas", "aaspaas", "ek", "do", "teen",
              "char", "chaar", "paanch", "chhe", "saat", "aath", "nau", "das", "gyarah", "barah", "bees",
              "tees", "hai", "tha", "ji"}


# a location answer must not contain any of these: they mean the reply says more than the place
NOT_A_PLACE = {"is", "was", "were", "are", "be", "been", "it", "it's", "its", "he", "she", "they", "we", "i",
               "you", "my", "his", "her", "their", "there", "this", "that", "fell", "fall", "fallen", "slipped",
               "happened", "happen", "saw", "see", "seen", "hurt", "broke", "broken", "leaking", "leak", "fire",
               "smoke", "think", "guess", "not", "no", "yes", "and", "but", "because", "then", "also", "still",
               "actually", "sorry", "wait", "correction", "instead", "submit", "cancel", "ago", "today",
               "yesterday", "morning", "afternoon", "evening", "night", "tonight", "now", "am", "pm", "a.m",
               "p.m", "o'clock", "minutes", "hours", "when", "where", "what", "who", "why", "how",
               # Hinglish: verbs, pronouns, negation, question and time words mean more than a place
               "hai", "tha", "thi", "hua", "hui", "hue", "gaya", "gayi", "gaye", "gira", "giri", "gire",
               "ho", "raha", "rahi", "rahe", "nahi", "nahin", "mat", "haan", "kab", "kahan", "kya", "kaun",
               "mujhe", "maine", "usne", "humne", "hum", "woh", "yeh", "ye", "aag", "lagi", "laga", "baje",
               "subah", "shaam", "raat", "kal", "aaj", "abhi", "pehle", "ke", "paas", "ki", "ka", "se"}
# Hinglish postposition after a place ("conference room 2 mein", "gate pe"): dropped, the place is kept
_PLACE_TAIL = re.compile(r"\s+(?:mein|me|mai|pe|par)\s*$", re.I)
_PLACE_LEAD = re.compile(r"^(?:(?:it\s+(?:was|is)|it'?s|that\s+was)\s+)?(?:in\s+|at\s+|inside\s+)?(?:the\s+)?", re.I)


# v7: "…, cancel this" / "ok then cancel this one" anywhere in the turn (not "don't cancel it")
_CANCEL_IN = re.compile(r"\b(?:cancel|discard|scrap)\s+(?:this|it|that|the report|this report|this one|"
                        r"the whole thing|everything)\b"
                        r"|\bcancel\s+(?:kar\s*do|karo|kar\s+dijiye)\b")
_NO_CANCEL = re.compile(r"\b(?:don'?t|do not|not|no need to|never)\s+(?:\w+\s+)?(?:cancel|discard|scrap)\b")
FILLER_ONLY = {"um", "uh", "umm", "uhh", "uhm", "hmm", "erm", "er", "ah", "eh", "mm", "..."}


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
    if t in CANCEL or (_CANCEL_IN.search(t) and not _NO_CANCEL.search(t)):
        return {"tool": "cancel_report"}
    if (state == "review" and t in SUBMIT) or (t in SUBMIT and "submit" in t):
        return {"tool": "submit_report"}         # an early "submit": code then refuses and asks what's missing
    if all(w in FILLER_ONLY for w in words):
        return {"tool": "no_op"}                 # "um... uh...": nothing for the report
    if state != "collecting":
        return None
    if asked == "s":
        said = " ".join(w for w in words if w not in FILLERS)
        if said in _SEVERITY_PHRASE:
            return {"tool": "update_report", "args": {"s": _SEVERITY_PHRASE[said]}}
        sev = [SEVERITY_WORDS[w] for w in words if w in SEVERITY_WORDS]
        rest = [w for w in words if w not in SEVERITY_WORDS and w not in FILLERS]
        if len(sev) == 1 and not rest:
            return {"tool": "update_report", "args": {"s": sev[0]}}
    if asked == "t":
        spoken = {v.replace("_", " "): v for v in type_values if v != "other"}
        if t in spoken:
            return {"tool": "update_report", "args": {"t": spoken[t]}}
    if asked == "l":
        stripped = _PLACE_TAIL.sub("", text.strip().rstrip(".!?"))
        if stripped != text.strip().rstrip(".!?"):
            text, words = stripped, _norm(stripped).split()
        if (1 <= len(words) <= 8 and not any(w in NOT_A_PLACE or w in SEVERITY_WORDS for w in words)
                and not resolve(text)["resolved"]):
            place = _PLACE_LEAD.sub("", text.strip().rstrip(".!?")).strip()
            if place:
                return {"tool": "update_report", "args": {"l": place}}
    if asked == "d":
        only_time = all(w in TIME_WORDS or re.fullmatch(r"\d{1,2}(:\d{2})?(am|pm)?", w) for w in words)
        if only_time and resolve(text)["resolved"]:
            return {"tool": "update_report", "args": {"d": text.strip().rstrip(".!?,")}}
    return None
