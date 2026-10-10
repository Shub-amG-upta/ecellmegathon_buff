"""
Time phrases → timestamps. The LLM copies the user's phrase ("around 10 AM");
code resolves it against the clock. Use the laptop clock: the board's reads 2023.

resolve(phrase, now) -> {"phrase", "iso", "resolved", "approx"}
  resolved False: kept as the phrase, flagged at review (still counts as filled)
  approx True:    a part of day ("this morning") or "around"; shown as approximate

Stdlib only.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20,
        "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "couple": 2, "few": 3}

# DESIGN CHOICE: the clock time a part of day stands for. Shown as approximate.
_PART_OF_DAY = {"morning": 9, "afternoon": 14, "evening": 18, "night": 21, "tonight": 21}

_CLOCK_12 = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])")
_CLOCK_24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_AGO = re.compile(r"\b(\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|"
                  r"twenty|thirty|forty|forty-five|fifty|couple|few)\s+(?:of\s+)?"
                  r"(min(?:ute)?s?|hours?|hrs?)\s+(?:ago|back)\b")


def _out(phrase: str, dt: datetime | None, approx: bool = False) -> dict:
    return {"phrase": phrase, "iso": dt.isoformat(timespec="seconds") if dt else None,
            "resolved": dt is not None, "approx": approx and dt is not None}


def resolve(phrase: str, now: datetime | None = None) -> dict:
    now = (now or datetime.now().astimezone()).replace(microsecond=0)
    p = phrase.lower().strip()
    approx = bool(re.search(r"\b(around|about|roughly|approximately|ish|maybe)\b|-ish", p))

    if re.search(r"\b(just now|right now|now|just happened|a moment ago|moments ago)\b", p):
        return _out(phrase, now)
    if "half an hour" in p and re.search(r"\b(ago|back)\b", p):
        return _out(phrase, now - timedelta(minutes=30), approx)
    m = _AGO.search(p)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUM[m.group(1)]
        unit = timedelta(hours=1) if m.group(2).startswith("h") else timedelta(minutes=1)
        return _out(phrase, now - n * unit, approx or m.group(1) in ("couple", "few"))

    day = now
    explicit_day = False
    if "yesterday" in p or "last night" in p:
        day, explicit_day = now - timedelta(days=1), True
    elif re.search(r"\b(today|this|tonight)\b", p):
        explicit_day = True

    hour = minute = None
    m = _CLOCK_12.search(p)
    if m:
        hour, minute = int(m.group(1)) % 12, int(m.group(2) or 0)
        if m.group(3).startswith("p"):
            hour += 12
        if int(m.group(1)) > 12:
            return _out(phrase, None)
    elif (m := _CLOCK_24.search(p)):
        hour, minute = int(m.group(1)), int(m.group(2))
    elif "noon" in p or "midday" in p:
        hour, minute = 12, 0
    elif "midnight" in p:
        hour, minute = 0, 0
    else:
        for word, h in _PART_OF_DAY.items():
            if word in p or (word == "night" and "last night" in p):
                hour, minute, approx = h, 0, True
                break

    if hour is None:
        # "yesterday" or "today" alone: we know the day, not the time
        return _out(phrase, None)
    dt = day.replace(hour=hour, minute=minute, second=0)
    # "at 11 PM" said at 9 AM with no day word means last night, not tonight.
    # DESIGN CHOICE: more than 1 hour in the future → the previous day.
    if not explicit_day and dt - now > timedelta(hours=1):
        dt -= timedelta(days=1)
    return _out(phrase, dt, approx)
