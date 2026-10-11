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
# DESIGN CHOICE: the severity an incident type usually has at least. Code never
# changes the user's answer: below this, the read-back asks them to confirm it.
# (Raising it silently overrode explicit answers in 3 eval dialogs: ch-02,
# gs-02, lb-02.)
SEVERITY_FLOOR = {"fire": "high", "gas_leak": "high", "chemical": "medium"}

TEXT_KEYS = ("l", "d", "p", "e", "a", "w",
             "g", "c", "k", "n")           # FORM=15 context fields (task, cause, damage, reported to)

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


def severity_floor(incident_type: str | None, severity: str | None) -> str | None:
    """The usual minimum when the given severity is below it, else None."""
    floor = SEVERITY_FLOOR.get(incident_type or "")
    if severity and floor and SEVERITY_ORDER.index(severity) < SEVERITY_ORDER.index(floor):
        return floor
    return None


# ---- severity fallback --------------------------------------------------------------------------
# When the model leaves severity empty although the user stated it (seen on long first descriptions:
# "... This is critical."), code fills the blank from the user's own words. It never changes a value
# the model gave or one the form already has. Mapping (eval/nlu/LABELING.md, "Severity"): explicit
# words only, a later statement overwrites, never inferred from injuries or the incident type.

_LEVEL = {"minor": "low", "low": "low", "notserious": "low",
          "medium": "medium", "moderate": "medium",
          "high": "high", "serious": "high", "bad": "high",
          "critical": "critical", "veryserious": "critical", "emergency": "critical"}
_LEVEL_WORD = "|".join(sorted(_LEVEL, key=len, reverse=True))

# A level word that is part of another noun phrase says nothing about the incident's severity.
_NOT_SEVERITY = re.compile(
    r"\bemergency\s+(?:room|ward|stop|exit|services?|team|number|department|vehicle|lights?|button|alarm|"
    r"door|staircase|stairs|response|contact)s?\b"
    r"|\bhigh\s+(?:voltage|shelf|shelves|ladder|rack|racks|pressure|level|temperature|speed|ceiling|up)\b"
    r"|\blow\s+(?:voltage|level|wall|height|pressure|speed|light|ceiling)\b"
    r"|\b(?:minor|bad|serious|critical)\s+(?:burns?|injur(?:y|ies)|cuts?|bruises?|wounds?|fractures?|damage)\b"
    r"|\bcritical\s+condition\b")

# Words that can open a statement about the incident just before the level word.
_STATEMENT = (r"(?:it'?s|it\s+is|it\s+was|this\s+is|this\s+was|that'?s|that\s+is|i'?d\s+say|i\s+would\s+say|"
              r"i\s+think(?:\s+it'?s)?|make\s+it|mark\s+it|put\s+it|call\s+it|severity(?:\s+(?:is|was|level))?:?)"
              r"\s+(?:(?:really|quite|pretty|definitely|probably|very)\s+)?")
# After a level word that opens a short answer, what may follow when there is no punctuation.
_OPENERS = {"he", "she", "it", "they", "someone", "somebody", "we", "i", "nobody", "no", "not", "but", "and",
            "because", "since", "so", "there", "there's", "its", "it's", "he's", "she's", "they're", "severity"}
_LINKING = {"is", "was", "are", "were", "be", "been", "being", "seems", "seemed", "looks", "looked", "got",
            "getting", "too", "very", "so", "quite", "pretty", "really"}


def _prepare(text: str) -> str:
    t = (text or "").lower().replace("\u2019", "'")
    t = _NOT_SEVERITY.sub(" ~ ", t)                              # a noun phrase, not a severity
    t = re.sub(r"\bnot\s+serious\b", "notserious", t)            # "not serious" IS low (LABELING.md)
    t = re.sub(r"\bvery\s+serious\b", "veryserious", t)
    t = re.sub(rf"\bnot\s+(?:(?:really|very|that)\s+)?(?:{_LEVEL_WORD})\b", " ~ ", t)   # "not high" cancels
    return t


def severity_from_words(text: str) -> str | None:
    """The severity the user explicitly states in `text` (low / medium / high / critical), or None.

    Only a level word used as a statement about the incident counts: on its own or opening a short
    answer ("high", "critical, he can't move his leg", "Critical."), "<level> severity",
    "severity (is) <level>", "it's / this is / I'd say <level>", "a <level> incident". Part of another
    noun phrase ("emergency room", "high shelf", "bad burns", "critical condition") or negated ("not
    high") does not count; "not serious" is low. If several levels are stated, the last one wins.
    Works on raw STT text: any case, punctuation present or missing."""
    t = _prepare(text)
    found: list[tuple[int, str]] = []
    lv = rf"(?P<lv>{_LEVEL_WORD})"
    for pat in (rf"\b{lv}\s+severity\b",                                   # "medium severity"
                rf"\b{_STATEMENT}{lv}\b",                                  # "it's critical", "severity high"
                rf"\ban?\s+{lv}\s+(?:incident|accident|issue|problem|situation|case|one)\b"):
        found += [(m.start("lv"), _LEVEL[m.group("lv")]) for m in re.finditer(pat, t)]
    # A level word opening a sentence or clause: "Critical." / "high, he has a fracture" / "critical he can't".
    for seg in re.finditer(r"[^.!?;,:\n]+", t):
        words = seg.group().split()
        if not words or words[0] not in _LEVEL:
            continue
        if len(words) == 1 or words[1] in _OPENERS or words[1] in _LEVEL:
            found.append((seg.start() + seg.group().index(words[0]), _LEVEL[words[0]]))
    # Raw STT with no punctuation at all: a level word appended at the very end ("... to hospital critical").
    words = re.findall(r"[a-z'~]+", t)
    if words and words[-1] in _LEVEL and not re.search(r"[.!?;,:]", text or "") \
            and (len(words) == 1 or words[-2] not in _LINKING):
        found.append((t.rfind(words[-1]), _LEVEL[words[-1]]))
    return max(found)[1] if found else None


def fill_severity(kept: dict, text: str, form_has_severity: bool) -> dict:
    """Fill a blank severity from this turn's words: only when the model gave no `s` this turn and the
    form has no severity yet. Never changes a value. Returns `kept`, with `s` added if it was filled."""
    if "s" in kept or form_has_severity:
        return kept
    level = severity_from_words(text)
    return {**kept, "s": level} if level else kept
