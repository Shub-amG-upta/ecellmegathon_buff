"""
The gate: code checks every model answer before it touches the form.

- Free-text values (location, date phrase, persons, equipment, actions,
  witnesses) must be supported by what the user actually said. A value whose
  content words are mostly absent from the transcript is dropped and logged.
- Enum values (type, severity) are already valid: the JSON schema allows
  nothing else. They pass through.
- Severity floors: some incident types are never below a minimum level.

Stdlib only.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# DESIGN CHOICE, tune on eval cases (never on the demo script): the fraction
# of a value's content words that must appear in the transcript. Range 0.5–1.0.
# Lower lets paraphrases through ("isolated the area" -> "area isolated");
# higher drops more invented detail.
SUPPORT_THRESHOLD = 0.6
# DESIGN CHOICE: two words count as the same when their similarity is at least
# this (catches "isolated"/"isolate", STT spelling slips). Range 0.8–0.9.
WORD_MATCH = 0.85

SEVERITY_ORDER = ["low", "medium", "high", "critical"]
# DESIGN CHOICE: the minimum severity per incident type. Shown to the user at
# review when it raised their answer.
SEVERITY_FLOOR = {"injury": "high", "fire_smoke": "high", "chemical_spill": "medium"}

TEXT_KEYS = ("l", "d", "p", "e", "a", "w")

_STOP = frozenset("""
a an the and or but of to in on at by for from with near into onto over under
is was were be been being are am it its it's this that these those there here
has have had do did does done some any very really just already also then so
i we you he she they me us him her them my our your his their
""".split())


def content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]


def _word_in(word: str, vocab: set[str]) -> bool:
    if word in vocab:
        return True
    if len(word) < 4:                      # short words and numbers must match exactly
        return False
    return any(len(v) >= 4 and SequenceMatcher(None, word, v).ratio() >= WORD_MATCH
               for v in vocab)


def support(value: str, transcript: str) -> float:
    """Fraction of the value's content words found in the transcript (0 if none).
    0 when the value holds a number the user never said: "Room 3" for "Room 2"
    is the worst kind of wrong, so numbers are all-or-nothing."""
    words = content_words(value)
    if not words:
        return 0.0
    vocab = set(content_words(transcript))
    if any(w.isdigit() and w not in vocab for w in words):
        return 0.0
    return sum(_word_in(w, vocab) for w in words) / len(words)


def check(args: dict, transcript: str) -> tuple[dict, list[dict]]:
    """Split LLM args into (kept, dropped). `transcript` is everything the user
    has said in this report so far."""
    kept, dropped = {}, []
    for key, val in args.items():
        if key not in TEXT_KEYS:
            kept[key] = val
            continue
        items = val if isinstance(val, list) else [val]
        ok = []
        for item in items:
            s = support(item, transcript)
            if s >= SUPPORT_THRESHOLD:
                ok.append(item)
            else:
                dropped.append({"field": key, "value": item,
                                "reason": f"not in transcript (support {s:.2f})"})
        if ok:
            kept[key] = ok if isinstance(val, list) else ok[0]
    return kept, dropped


def severity_floor(incident_type: str | None, severity: str | None) -> tuple[str | None, bool]:
    """(severity after the floor, whether the floor raised it)."""
    floor = SEVERITY_FLOOR.get(incident_type or "")
    if not severity or not floor:
        return severity, False
    if SEVERITY_ORDER.index(severity) < SEVERITY_ORDER.index(floor):
        return floor, True
    return severity, False
