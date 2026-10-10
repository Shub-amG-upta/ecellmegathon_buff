"""app/gate.py: nothing reaches the form unless the user said it."""
from app.gate import check, severity_floor, support

T = ("There was a water leak near Conference Room 2 around 10 AM. "
     "Maintenance has already isolated the area.")


def test_the_problem_statement_example_passes_untouched():
    args = {"t": "water_leak", "l": "near Conference Room 2", "d": "around 10 AM",
            "a": ["area isolated by maintenance"]}
    kept, dropped = check(args, T)
    assert kept == args and dropped == []


def test_invented_values_are_dropped_and_logged():
    args = {"t": "water_leak", "l": "Server Room B", "w": ["John Smith"],
            "a": ["area isolated", "called the fire brigade"]}
    kept, dropped = check(args, T)
    assert kept == {"t": "water_leak", "a": ["area isolated"]}
    assert {d["field"] for d in dropped} == {"l", "w", "a"}
    assert any(d["value"] == "called the fire brigade" for d in dropped)


def test_room_numbers_must_match_exactly():
    assert support("Conference Room 2", T) == 1.0
    kept, dropped = check({"l": "Conference Room 3", "d": "around 11 AM"}, T)
    assert kept == {} and len(dropped) == 2                 # 2 of 3 words would pass otherwise


def test_small_spelling_differences_still_count():
    assert support("area isolate", T) == 1.0
    assert support("the", T) == 0.0                         # no content words → unsupported


def test_severity_floor_reports_but_never_decides():
    assert severity_floor("fire", "low") == "high"
    assert severity_floor("fire", "critical") is None
    assert severity_floor("chemical", "low") == "medium"
    assert severity_floor("gas_leak", "medium") == "high"
    assert severity_floor("water_leak", "low") is None
    assert severity_floor("fire", None) is None
