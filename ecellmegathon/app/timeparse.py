"""
Time phrases → timestamps. The LLM copies the user's phrase ("around 10 AM");
code resolves it against the clock. Use the laptop clock: the board's reads 2023.

resolve(phrase, now) -> {"phrase", "iso", "resolved", "approx"}
  resolved False: kept as the phrase, flagged at review (still counts as filled)
  approx True:    "around", "or so", or a bare part of day ("this morning")

A clock time without AM/PM ("at 2:15", "around 4", "9 o'clock") means its most
recent occurrence: said at 3 PM, "2:15" is 14:15 and "9" is 09:00. A part of
day settles it: "8 last night" is 20:00, "5:45 in the evening" is 17:45.
Checked against every resolved time in eval/nlu/cases.jsonl (tests/test_timeparse.py).

Stdlib only.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
        "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "couple": 2, "few": 3}
_HOUR_WORD = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"

# DESIGN CHOICE: the clock time a bare part of day stands for. Shown as approximate.
_PART_OF_DAY = {"morning": 9, "afternoon": 14, "evening": 18, "night": 21, "tonight": 21}
_PM_PARTS = ("afternoon", "evening", "night", "tonight")
# DESIGN CHOICE: how far in the future a resolved time may be before it is read
# as the previous occurrence ("4 PM" said at 3:30 PM is today; "11 PM" is last night).
_FUTURE_SLACK = timedelta(hours=1)

_CLOCK_12 = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])")
_CLOCK_HM = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_OCLOCK = re.compile(r"\b" + _HOUR_WORD + r"\s*o'?\s?clock\b")
_PAST_TO = re.compile(r"\b(quarter|half)\s+(past|to)\s+" + _HOUR_WORD + r"\b")
_BARE_HOUR = re.compile(r"\b(?:at|around|about|by|since|till|until|closer to|roughly|approximately|maybe|"
                        r"near|before|after)\s+" + _HOUR_WORD +
                        r"\b(?!\s*(?::|min|minute|hour|hr|day|week|people|person|floor|th\b|st\b|nd\b|rd\b))")
_AGO = re.compile(r"\b(\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|"
                  r"twenty|thirty|forty|forty-five|fifty|couple|few)\s+(?:of\s+)?"
                  r"(min(?:ute)?s?|hours?|hrs?)\s+(?:ago|back)\b")


def _n(word: str) -> int:
    return int(word) if word.isdigit() else _NUM[word]


def _out(phrase: str, dt: datetime | None, approx: bool = False) -> dict:
    return {"phrase": phrase, "iso": dt.isoformat(timespec="seconds") if dt else None,
            "resolved": dt is not None, "approx": approx and dt is not None}


def _clock(p: str) -> tuple[int, int, bool] | None:
    """(hour, minute, meridiem_known) from the phrase, or None."""
    m = _CLOCK_12.search(p)
    if m:
        h = int(m.group(1))
        if not 1 <= h <= 12:
            return None
        return h % 12 + (12 if m.group(3).startswith("p") else 0), int(m.group(2) or 0), True
    m = _CLOCK_HM.search(p)
    if m:
        h = int(m.group(1))
        return h, int(m.group(2)), h > 12 or h == 0
    m = _PAST_TO.search(p)
    if m:
        h = _n(m.group(3))
        if m.group(2) == "past":
            return h, 15 if m.group(1) == "quarter" else 30, False
        return (h - 1) or 12, 45 if m.group(1) == "quarter" else 30, False
    m = _OCLOCK.search(p) or _BARE_HOUR.search(p)
    if m:
        h = _n(m.group(1))
        return (h, 0, False) if 1 <= h <= 12 else None
    if "noon" in p or "midday" in p:
        return 12, 0, True
    if "midnight" in p:
        return 0, 0, True
    return None


def resolve(phrase: str, now: datetime | None = None) -> dict:
    now = (now or datetime.now().astimezone()).replace(microsecond=0)
    p = phrase.lower().strip()
    approx = bool(re.search(r"\b(around|about|roughly|approximately|ish|maybe|or so|closer to)\b|-ish", p))

    if re.search(r"\b(just now|right now|now|just happened|a moment ago|moments ago)\b", p):
        return _out(phrase, now)
    if "half an hour" in p and re.search(r"\b(ago|back)\b", p):
        return _out(phrase, now - timedelta(minutes=30), approx)
    m = _AGO.search(p)
    if m:
        unit = timedelta(hours=1) if m.group(2).startswith("h") else timedelta(minutes=1)
        return _out(phrase, now - _n(m.group(1)) * unit, approx or m.group(1) in ("couple", "few"))

    day, explicit_day = now, False
    if "yesterday" in p or "last night" in p:
        day, explicit_day = now - timedelta(days=1), True
    elif re.search(r"\b(today|this|tonight)\b", p):
        explicit_day = True
    part = next((w for w in _PART_OF_DAY if re.search(rf"\b{w}\b", p)), None)

    c = _clock(p)
    if c is None:
        if part is None:
            return _out(phrase, None)          # "yesterday" alone: the day, not the time
        return _out(phrase, day.replace(hour=_PART_OF_DAY[part], minute=0, second=0), True)

    hour, minute, known = c
    if not known and part in _PM_PARTS and hour < 12:
        hour, known = hour + 12, True
    elif not known and part == "morning":
        hour, known = hour % 12, True

    if known:
        dt = day.replace(hour=hour, minute=minute, second=0)
        if not explicit_day and dt - now > _FUTURE_SLACK:
            dt -= timedelta(days=1)
        return _out(phrase, dt, approx)

    # AM or PM unknown: the most recent of the two that isn't in the future
    cands = [day.replace(hour=hour % 12 + off, minute=minute, second=0) for off in (0, 12)]
    if explicit_day and day.date() != now.date():
        return _out(phrase, max(cands), approx)        # "yesterday at 8": the later one
    past = [d for d in cands if d - now <= _FUTURE_SLACK]
    dt = max(past) if past else max(cands) - timedelta(days=1)
    return _out(phrase, dt, approx)
