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


# ---- persons and actions (PEOPLEFIX, 2026-10-11) -------------------------------------------------------------
# From the board runs' remaining errors: persons / witnesses holding injuries or clauses ("he wasn't wearing
# goggles", "we gave him water", "one has both legs broken"), the injured worker missed ("Our lineman was ...",
# "the operator's eyes"), actions cut short ("flushed his eyes" for "... for fifteen minutes") or missed
# ("we hit the emergency stop"). PEOPLEFIX=off turns only these off.
# DESIGN CHOICE: who counts as a person: a person word or a name. Checked against every gold person and witness
# in the eval sets (tests/test_postfix.py).
PERSON_NOUNS = (r"worker|staff|man|men|woman|women|boy|girl|guard|supervisor|operator|technician|mechanic|plumber|"
                r"lineman|linemen|fitter|packer|loader|driver|carpenter|electrician|nurse|aide|doctor|patient|"
                r"visitor|resident|colleague|coworker|co-worker|people|person|employee|contractor|foreman|helper|"
                r"labou?rer|cook|chef|cleaner|housekeeper|housekeeping|attendant|assistant|manager|student|teacher|"
                r"child|children|kid|member|crew|officer|engineer|painter|welder|mason|rigger|porter|customer|"
                r"passenger|tenant|guest|trainee|intern|apprentice|janitor|watchman|firefighter|paramedic|pilot|"
                r"lady|gentleman|employees|team|executive|clerk|receptionist|peon|sweeper|cashier|salesman|vendor|"
                r"guy|fellow|chap|boss|bhaiya|didi|uncle|aunty")
_PERSON = re.compile(r"\b(?:" + PERSON_NOUNS + r")(?:s|es)?\b", re.I)
# a person value ends where a clause begins
_CLAUSE = re.compile(r"\s+(?:is|was|were|are|has|have|had|can'?t|cannot|couldn'?t|wasn'?t|isn'?t|got|gets|gave|"
                     r"give|slipped|fell|tripped|hurt|injured|broke|broken|nearly|almost|who|with|having|wearing|"
                     r"suffered|hit|lost|felt)\b.*$", re.I)
_BODY = re.compile(r"'s\s+(?:legs?|arms?|hands?|eyes?|head|back|fingers?|feet|foot|chest|neck|face)\b", re.I)
_PRONOUN_ONLY = re.compile(r"^(?:he|she|they|we|i|it|one|the other|someone|somebody|him|her|them)$", re.I)
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")
_NOT_NAME = {"The", "One", "Our", "Two", "Three", "Four", "Five", "Six", "Seven", "Some", "Both", "Several", "All",
             "Mostly", "Their", "His", "Her", "My", "Its", "This", "That", "Sir", "Madam"}


def peoplefix_enabled() -> bool:
    return os.environ.get("PEOPLEFIX", "on") != "off"


_INJURY = re.compile(r"\b(?:injur\w*|fractur\w*|burns?|bleeding|wounds?|bruis\w*|pain|unconscious|dizzy)\b", re.I)


def person_value(x: str) -> str | None:
    """The person in a persons / witnesses value, or None when it holds none. Only a clear sign removes a
    value: a clause ("he can't move his leg" -> he), an injury ("mostly leg injuries"), a body part
    ("fitter's legs"), or nothing but a pronoun. A value with none of these is kept as it is, person word or
    not ("one of our developers", "do log")."""
    s = str(x).strip()
    if _BODY.search(s):
        return None
    cut = _CLAUSE.sub("", s).strip(" ,.")
    if not cut or _PRONOUN_ONLY.match(cut):
        return None
    if cut == s and not _INJURY.search(s):
        return s
    names = [w for w in _NAME.findall(cut) if w not in _NOT_NAME]
    return cut if (_PERSON.search(cut) or names) and not _INJURY.search(cut) else None


def people_only(args: dict) -> dict:
    out = dict(args)
    for k in ("p", "w"):
        if k in out:
            keep = list(dict.fromkeys(v for v in (person_value(x) for x in out[k]) if v))
            if keep:
                out[k] = keep
            else:
                del out[k]
    return out


# only the reporter's own people ("Our lineman", "one of our packers"): "seven people", "a guy", "the staff"
# are not labelled as persons in the eval sets
_DET = r"(?:(?:one|two|three|four|some) of our |our )"
_SUBJECT = re.compile(r"(?:^|[.;!?]\s+|,\s+|\bwhen\s+|\bwhile\s+|\band\s+)(" + _DET + r"(?:[\w-]+ ){0,2}?(?:"
                      + PERSON_NOUNS + r")(?:s|es)?)(?='s\b|\s+(?:was|were|is|are|got|has|had|fell|slipped|tripped|"
                      r"went|came|collapsed|fainted|cut|burnt|burned|touched|lost|caught|stuck|injured|hurt|broke|"
                      r"received|suffered|fell)\b)", re.I)


_OWNER = re.compile(r"\b((?:our|the) (?:[\w-]+ )?(?:" + PERSON_NOUNS + r"))'s\b", re.I)


def fill_person(args: dict, text: str, form: dict) -> dict:
    """First description only (empty form), no person from the model: the person a clause starts with
    ("Our lineman was ...", "Two people are stuck", "the operator's eyes")."""
    if "p" in args or form:
        return args
    found = [m.group(1).strip() for m in _SUBJECT.finditer(text)] + [m.group(1).strip() for m in _OWNER.finditer(text)]
    found = list(dict.fromkeys(found))
    return {**args, "p": found[:2]} if found else args


# an action runs on to the end of its clause; "and" + a new subject or verb starts another clause
_NEW_CLAUSE = re.compile(r"^(?:\s*(?:and|but|then|so)\s+(?:\S+\s+){0,2}?(?:he|she|they|we|i|it|has|have|had|is|was|"
                         r"are|were|came|went|arrived|took|got|left|ran|\w+ed)\b)", re.I)


def _extend_right(item: str, text: str) -> str:
    m = re.search(re.escape(item), text, re.I)
    if not m:
        return item
    rest = text[m.end():]
    stop = re.search(r"[,.;!?]", rest)
    tail = rest[:stop.start()] if stop else rest
    sub = re.search(r"\s+(?:till|until|before|after|when|while|because|since|as soon as)\s+", tail, re.I)
    if sub:                                     # "fanned him till the ambulance came": the action ends at "till"
        tail = tail[:sub.start()]
    aur = re.search(r"\s+aur\s+", tail)       # Hinglish "and": always a new clause
    if aur:
        tail = tail[:aur.start()]
    for nc in re.finditer(r"\s+(?:and|but|then|so)\s+", tail):   # keep "and our security team",
        if _NEW_CLAUSE.match(tail[nc.start():]):                  # stop at "and EHS has been informed"
            tail = tail[:nc.start()]
            break
    words = tail.split()
    if not words or len(words) > 6:
        return item
    return (text[m.start():m.end()] + " " + " ".join(words)).strip()


def complete_actions(args: dict, text: str) -> dict:
    if "a" not in args:
        return args
    return {**args, "a": list(dict.fromkeys(_extend_right(str(x), text) for x in args["a"]))}


# done by a person ("we hit the emergency stop"), not by a machine ("the forklift reversed into ...")
_DID = re.compile(r"\b(?:we|they|i|he|she|security|someone|everyone|(?:the |our |two |three )?(?:[\w-]+ )?(?:"
                  + PERSON_NOUNS + r")(?:s|es)?)\s+(?:have\s+|has\s+|"
                  r"had\s+|then\s+|also\s+|immediately\s+|quickly\s+)?((?:called|informed|hit the (?:emergency )?"
                  r"(?:stop|switch|button|alarm)|pressed|reversed the|"
                  r"kept|gave|stopped|evacuated|isolated|closed|rushed|sent|cordoned off|flushed|cleaned|removed|"
                  r"covered|applied|shut down|switched off|turned off|put it out|put out|took him|took her|"
                  r"carried)\b[^,.;!?]*)", re.I)
_DOING = re.compile(r"\b(?:is|are)\s+(?:now\s+|already\s+)?((?:shutting down|switching off|turning off|evacuating|"
                    r"isolating|cordoning off|flushing|cleaning|mopping|drying)\b[^,.;!?]*)", re.I)


def fill_more_actions(args: dict, text: str, form: dict) -> dict:
    """No action from the model this turn: what the user says was done ("we hit the emergency stop", "IT is
    shutting down the rack"), each to the end of its clause."""
    if "a" in args:
        return args
    found = []
    for rx in (_DID, _DOING):
        for m in rx.finditer(text):
            clause = m.group(1).strip()
            parts = re.split(r"\s+and\s+(?=(?:called|informed|kept|gave|hit|reversed|stopped|took|sent)\b)", clause)
            found += [x.strip() for x in parts if x.strip()]
    found = [x for x in dict.fromkeys(found) if x.lower() not in {str(v).lower() for v in form.get("a") or []}]
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
    if peoplefix_enabled():
        out = people_only(out)
        out = fill_person(out, text, form)
        out = complete_actions(out, text)
        out = fill_more_actions(out, text, form)
    return out
