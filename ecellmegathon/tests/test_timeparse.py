"""app/timeparse.py against a fixed clock: 2026-10-10 15:30 IST."""
from datetime import datetime, timedelta, timezone

import pytest

from app.timeparse import resolve

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 10, 15, 30, 0, tzinfo=IST)


@pytest.mark.parametrize("phrase,iso,approx", [
    ("around 10 AM", "2026-10-10T10:00:00+05:30", True),
    ("10 am", "2026-10-10T10:00:00+05:30", False),
    ("at 10:45 a.m.", "2026-10-10T10:45:00+05:30", False),
    ("3 pm", "2026-10-10T15:00:00+05:30", False),
    ("12 pm", "2026-10-10T12:00:00+05:30", False),
    ("12 am", "2026-10-10T00:00:00+05:30", False),
    ("14:20", "2026-10-10T14:20:00+05:30", False),
    ("just now", "2026-10-10T15:30:00+05:30", False),
    ("20 minutes ago", "2026-10-10T15:10:00+05:30", False),
    ("an hour ago", "2026-10-10T14:30:00+05:30", False),
    ("half an hour ago", "2026-10-10T15:00:00+05:30", False),
    ("a couple of hours ago", "2026-10-10T13:30:00+05:30", True),
    ("this morning", "2026-10-10T09:00:00+05:30", True),
    ("yesterday evening", "2026-10-09T18:00:00+05:30", True),
    ("yesterday at 3 pm", "2026-10-09T15:00:00+05:30", False),
    ("last night", "2026-10-09T21:00:00+05:30", True),
    ("at noon", "2026-10-10T12:00:00+05:30", False),
    ("11 pm", "2026-10-09T23:00:00+05:30", False),        # future with no day word → yesterday
    ("4 pm", "2026-10-10T16:00:00+05:30", False),         # within the hour: kept today
])
def test_resolves(phrase, iso, approx):
    r = resolve(phrase, NOW)
    assert r == {"phrase": phrase, "iso": iso, "resolved": True, "approx": approx}


@pytest.mark.parametrize("phrase", ["sometime last week", "yesterday", "not sure", "13 pm", ""])
def test_unresolved_keeps_the_phrase(phrase):
    assert resolve(phrase, NOW) == {"phrase": phrase, "iso": None, "resolved": False,
                                    "approx": False}
