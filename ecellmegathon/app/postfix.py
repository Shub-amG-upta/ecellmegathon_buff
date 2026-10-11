"""
Post-model fixes (v4): small, rule-based corrections code applies to the model's answer after the gate and
the severity fallback. Each one targets an error pattern measured on the board
(benchmarks/2026-10-11_board_SM8750P/FINDINGS.md) and is checked against every eval turn
(tests/test_postfix.py: on the gold answers they change nothing).

  drop_repeats      a value the form already has, sent again          (harmless, but noise)
  veto              a place in equipment, a service in persons         ("meter room", "ambulance")
  witnesses         "X saw it", "X and I both saw it" -> witnesses      (the model put them in persons)
  expand_location   "ground floor" -> "meter room on the ground floor" (the full place the user said)
  fill_time         a time the user said, left out by the model         ("Last night around 8 PM")
  drop_parts        a damaged part of equipment already in the form    ("the forklift mast is bent")
list fixes (LISTFIX=off turns only these off):
  snap_lists        the user's whole phrase on the left                ("staff" -> "one of our staff")
  keep_responses    an action must say what someone did in response    (drops "One cook has bad burns")
  fill_actions      "has (already) <done> ..." the model left out       ("has already isolated the area")

apply() runs them in that order. POSTFIX=off (env) turns all of them off, for comparison.
Every fix only uses the user's own words; none invents a value. Stdlib only.
"""
from __future__ import annotations

import os
import re

from app.fastpath import TIME_WORDS
from app.timeparse import resolve

# ---- shared vocab -------------------------------------------------------------------------------------
# DESIGN CHOICE: nouns that make a phrase a place (not equipment). Checked against every gold equipment
# value in the eval set: none contains one (tests/test_postfix.py).
PLACE_NOUNS = ("room", "floor", "ward", "corridor", "hall", "kitchen", "area", "section", "parking", "lobby",
               "desk", "site", "building", "terrace", "pantry", "lab", "bay", "office", "canteen", "cafeteria",
               "reception", "washroom", "warehouse", "aisle", "driveway", "ramp", "basement", "entrance",
               "staircase", "stairs", "corridor", "wing", "premises", "compound")
_PLACE = re.compile(r"\b(?:" + "|".join(PLACE_NOUNS) + r")s?\b", re.I)
# DESIGN CHOICE: services and buildings that are not persons involved.
SERVICES = ("ambulance", "hospital", "fire brigade", "fire department", "fire tender", "fire tenders", "police",
            "108", "clinic")
_SERVICE = re.compile(r"\b(?:" + "|".join(re.escape(s) for s in SERVICES) + r")\b", re.I)


def enabled() -> bool:
    return os.environ.get("POSTFIX", "on") != "off"


# ---- the fixes ---------------------------------------------------------------------------------------------
def drop_repeats(args: dict, form: dict) -> dict:
    """Drop what the form already holds: the same scalar value, or list items already there."""
    out = {}
    for k, v in args.items():
        have = form.get(k)
        if isinstance(v, list):
            have_l = {str(x).lower() for x in (have or [])}
            new = [x for x in v if str(x).lower() not in have_l]
            if new:
                out[k] = new
        elif not (have is not None and str(have).lower() == str(v).lower()):
            out[k] = v
    return out


# DESIGN CHOICE: words that say a thing is damaged. Only then is "<equipment> <part>" the same equipment again.
_DAMAGE = re.compile(r"\b(?:bent|broken|broke|damaged|cracked|dented|burnt|burned|smashed|torn|shattered)\b", re.I)


def drop_parts(args: dict, form: dict, text: str) -> dict:
    """'the forklift mast is bent' when the form has 'forklift': a damaged part of equipment already in the form
    is that equipment again, not new equipment (fk-03). Only when the turn talks about damage; a new item that
    doesn't start with an existing one ('a charger') is kept."""
    if "e" not in args or not _DAMAGE.search(text):
        return args
    have = [str(x).lower() for x in form.get("e") or []]
    keep = [x for x in args["e"] if not any(str(x).lower().startswith(h + " ") for h in have)]
    out = dict(args)
    if keep:
        out["e"] = keep
    else:
        del out["e"]
    return out


def veto(args: dict, location: str | None) -> dict:
    """Equipment must not be a place (or the location itself); persons must not be services."""
    out = dict(args)
    if "e" in out:
        loc = (location or "").lower()
        out["e"] = [x for x in out["e"] if not _PLACE.search(x) and not (loc and x.lower() in loc)]
    if "p" in out:
        out["p"] = [x for x in out["p"] if not _SERVICE.search(x)]
    return {k: v for k, v in out.items() if v not in ([], None, "")}


_ROLE = r"(?:guard|receptionist|attendant|supervisor|manager|worker|cleaner|technician|nurse|driver|operator|staff)"
_NAME = (rf"(?:[Tt]he\s+)?(?:[a-z]+\s+)?{_ROLE}(?:\s+[A-Z][a-z]+)?"     # "the guard Mahesh", "the parking attendant"
         r"|(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]+"                         # "Mr Rao"
         r"|[A-Z][a-z]+(?:\s+from\s+(?:the\s+)?[A-Za-z]+)?")            # "Ravi", "Priya from the lab"
_SEE = re.compile(rf"((?:{_NAME})(?:(?:\s*,\s*|\s+and\s+)(?:{_NAME}))*)(?:\s+and\s+(?:I|me))?\s+"
                  rf"(?:both\s+|all\s+)?(?:saw|noticed|witnessed|watched)\b")
_ME_AND = re.compile(r"\b(?:me|I)\s+and\s+([A-Z][a-z]+)\s+(?:both\s+)?(?:saw|noticed|witnessed)\b")
_NOT_A_NAME = {"i", "me", "we", "nobody", "no one", "someone", "somebody", "everyone", "everybody", "he", "she",
               "they", "it", "the", "this", "that", "there", "sir", "madam", "yes", "no", "but", "and", "so"}


def witness_names(text: str) -> list[str]:
    """People the user says saw it, in their own words."""
    out = []
    for m in _SEE.finditer(text):
        for part in re.split(r"\s*,\s*|\s+and\s+", m.group(1)):
            p = part.strip()
            first = p.split()[0].lower() if p else ""
            if p and p.lower() not in _NOT_A_NAME and (first == "the" or first not in _NOT_A_NAME):
                out.append(p)
    out += [m.group(1) for m in _ME_AND.finditer(text)]
    return list(dict.fromkeys(out))


def witnesses(args: dict, text: str) -> dict:
    names = witness_names(text)
    if not names:
        return args
    out = dict(args)
    out["w"] = list(dict.fromkeys((out.get("w") or []) + names))
    if "p" in out:                                    # the model often files witnesses under persons
        low = [n.lower() for n in names]
        out["p"] = [x for x in out["p"] if not any(x.lower() in n or n in x.lower() for n in low)]
        if not out["p"]:
            del out["p"]
    return out


_LEFT = re.compile(r"((?:[\w'-]+\s+){1,3})((?:on|in|at|of)\s+(?:the\s+)?)$", re.I)
_RIGHT = re.compile(r"\s+((?:on|in|at)\s+(?:the\s+)?(?:[\w-]+\s+){0,2}(?:floor|block|wing|building|level))\b", re.I)
_LEAD = {"in", "at", "the", "a", "an", "on", "our", "near", "is", "was", "there", "fire", "smoke", "leak",
         "and", "from", "to", "inside", "outside"}


def expand_location(location: str, text: str) -> str:
    """Extend the model's place to the full place the user said, using only the user's words:
    a room before "on/in the <floor>", or "on the <floor>" after a room. Unchanged if not found."""
    i = text.lower().find(location.lower())
    if i < 0:
        return location
    left, right = text[:i], text[i + len(location):]
    m = _LEFT.search(left)
    if m:
        words = m.group(1).split()
        while words and words[0].lower() in _LEAD:
            words = words[1:]
        if words and _PLACE.search(" ".join(words)):
            return " ".join(words) + " " + m.group(2) + text[i:i + len(location)]
    m = _RIGHT.match(right)
    if m and _PLACE.search(location):
        return text[i:i + len(location)] + " " + m.group(1)
    return location


_W = re.compile(r"[\w:'.-]+")
_TRAIL = {"a", "an", "the", "at", "in", "of", "to", "or", "so", "by", "this", "last", "just", "about", "around",
          "roughly", "approximately", "since", "one"}


def time_phrase(text: str) -> str | None:
    """The longest run of time words in `text` that timeparse resolves ("Last night around 8 PM",
    "about twenty minutes ago"). A bare "now" doesn't count ("she's in hospital now" is not a time)."""
    words = _W.findall(text)
    best = None
    for i in range(len(words)):
        for j in range(min(len(words), i + 7), i, -1):
            span = [w.strip(".") for w in words[i:j]]
            if not all(w.lower() in TIME_WORDS or re.fullmatch(r"\d{1,2}(:\d{2})?(am|pm)?", w.lower())
                       for w in span):
                continue
            while len(span) > 1 and span[-1].lower() in _TRAIL:      # "... 8 PM a nursing aide" -> "... 8 PM"
                span = span[:-1]
            s = " ".join(span)
            low = s.lower()
            if low in ("now", "the", "a", "an", "at", "in", "now the") or (low.startswith(("now ", "the ", "in "))
                                                                       and len(span) < 3):
                break
            if resolve(s)["resolved"] and (any(c.isdigit() for c in s) or re.search(
                    r"night|morning|evening|afternoon|noon|midnight|ago|back|just now|right now", low)):
                if best is None or len(s) > len(best):
                    best = s
            break
    return best


def fill_time(args: dict, text: str, form_has_time: bool) -> dict:
    if "d" in args or form_has_time:
        return args
    s = time_phrase(text)
    return {**args, "d": s} if s else args


# ---- list fixes (LISTFIX) ------------------------------------------------------------------------------------
# DESIGN CHOICE: verbs that make an action a response (what someone did about it), not the event or an injury.
# Checked against every gold action in the eval set: each one contains one (tests/test_postfix.py).
RESPONSE_VERBS = (r"shutting(?:\s+down)?|shut\s+down|call(?:ed|ing)?|inform(?:ed)?|notif(?:y|ied)|told|tell|stop(?:ped)?|"
                  r"shut|switch(?:ed)?\s+off|turn(?:ed)?\s+off|isolat(?:e|ed|ing)|evacuat(?:e|ed|ing)|flush(?:ed)?|"
                  r"cordon(?:ed)?(?:\s+off)?|block(?:ed)?(?:\s+off)?|barricad(?:e|ed)|dri(?:ed)|dry|mop(?:ped)?|"
                  r"wip(?:ed|ing)|clean(?:ed)?|put\s+(?:it\s+|the\s+fire\s+)?out|extinguish(?:ed)?|gave|give|giving|"
                  r"sent|send|shift(?:ed)?|rush(?:ed)?|took|taken|escort(?:ed)?|remov(?:e|ed)|clos(?:e|ed)|"
                  r"open(?:ed)?|press(?:ed)?|hit|revers(?:e|ed)|kept|fann(?:ed)|pull(?:ed)|brok(?:e)|"
                  r"got\s+(?:everyone|them|him|her)\s+out|put|cover(?:ed)?|appl(?:y|ied)|doing|did|start(?:ed)?|"
                  r"check(?:ing|ed)|trying|calling|moved|placed|keeping|"
                  # Hinglish (Latin-letter Hindi, HINGLISH=1): did / gave / called / told / closed / got out / sent
                  r"kar\s+diya|kar\s+diye|kar\s+di|kiya|kiye|diya|diye|bula(?:ya|\s+liya|\s+liye)?|"
                  r"bata(?:ya|\s+diya)|band|bahar|nikal(?:a|\s+diya|\s+diye)?|bhej(?:a|\s+diya)?|"
                  r"le\s+gaye|le\s+gaya|saaf|pocha")
_RESPONSE = re.compile(r"\b(?:" + RESPONSE_VERBS + r")\b", re.I)
# DESIGN CHOICE: completed responses said with has/have ("Maintenance has already isolated the area"). Narrower
# than RESPONSE_VERBS on purpose: "has hit", "have started" are not responses.
_DONE = (r"isolated|dried|mopped|cordoned\s+off|blocked\s+off|barricaded|evacuated|informed|called|stopped|"
         r"switched\s+off|shut\s+off|turned\s+off|closed|flushed|cleaned|wiped|removed|covered|put\s+out|"
         r"extinguished|sent|shifted|escorted")
_DONE_CLAUSE = re.compile(r"\b(?:has|have|had)\s+(?:already\s+|just\s+)?((?:" + _DONE +
                          r")\b[^,.;!?]*?)(?=\s*(?:[,.;!?]|\band\b|\bbut\b|$))", re.I)
# Words that belong to a phrase on its left: "one of our staff", "a nursing aide", "two workers".
_LEFT_WORDS = {"a", "an", "the", "one", "of", "our", "their", "his", "her", "my", "two", "three", "four", "five",
               "six", "seven", "eight", "nine", "ten", "some", "both", "several", "all"}


def listfix_enabled() -> bool:
    return os.environ.get("LISTFIX", "on") != "off"


def _snap_one(item: str, text: str, key: str) -> str:
    m = re.search(r"\b" + re.escape(item) + r"(s|es)?\b", text, re.I)
    if not m:
        return item
    words = re.findall(r"\S+", text[:m.start()])
    take = []
    for w in reversed(words):
        lw = w.lower()
        if w.endswith((",", ".")):
            break
        if lw in _LEFT_WORDS or (key == "a" and not take and _RESPONSE.fullmatch(lw)):
            take.insert(0, w)
            continue
        break
    while take and take[0].lower() in {"of", "and"}:
        take = take[1:]
    if not take and m.end() - m.start() == len(item):
        return item
    return " ".join(take + [text[m.start():m.end()]])


def snap_lists(args: dict, text: str) -> dict:
    """Extend persons / equipment / actions to the user's whole phrase on the left, in the user's words:
    "staff" -> "one of our staff", "CPR" -> "gave CPR". Unchanged if the value isn't in this turn."""
    out = dict(args)
    for k in ("p", "e", "a"):
        if k in out:
            out[k] = list(dict.fromkeys(_snap_one(str(x), text, k) for x in out[k]))
    return out


def keep_responses(args: dict) -> dict:
    """An action must say what someone did about it; the model also files the event, the cause and injuries
    there ("someone moved the base", "One cook has bad burns")."""
    if "a" not in args:
        return args
    keep = [x for x in args["a"] if _RESPONSE.search(str(x))]
    out = dict(args)
    if keep:
        out["a"] = keep
    else:
        del out["a"]
    return out


def fill_actions(args: dict, text: str, form: dict) -> dict:
    """The model gave no action and the form has none, but the user said "has (already) <done> ...":
    copy that clause ("security has cordoned off the driveway" -> "cordoned off the driveway")."""
    if "a" in args or form.get("a"):
        return args
    found = [m.group(1).strip() for m in _DONE_CLAUSE.finditer(text)]
    return {**args, "a": found[:4]} if found else args


# ---- all together ------------------------------------------------------------------------------------------
def apply(args: dict, text: str, form: dict) -> dict:
    """The v4 fixes on one turn's gated args. `form` = the form before the turn (one-letter keys)."""
    if not enabled():
        return args
    out = drop_repeats(args, form)
    out = drop_parts(out, form, text)
    out = veto(out, out.get("l") or form.get("l"))
    out = witnesses(out, text)
    if "l" in out:
        out["l"] = expand_location(out["l"], text)
    out = fill_time(out, text, bool(form.get("d")))
    if listfix_enabled():
        out = snap_lists(out, text)
        out = keep_responses(out)
        out = fill_actions(out, text, form)
    return out
