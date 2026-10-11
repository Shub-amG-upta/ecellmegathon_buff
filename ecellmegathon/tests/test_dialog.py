"""app/dialog.py with a scripted fake LLM: the state machine, the gate and the
saved report, end to end. Asserts on what the user would see and on the saved
file, not on internals."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import report
from app.backends import LLMReply
from app.dialog import GREETING, Dialog

IST = timezone(timedelta(hours=5, minutes=30))
CLOCK = lambda: datetime(2026, 10, 10, 15, 30, tzinfo=IST)
EXAMPLE = ("There was a water leak near Conference Room 2 around 10 AM. "
           "Maintenance has already isolated the area.")


class Scripted:
    """Returns the queued replies in order and records every request."""
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None, prefix=None):
        self.calls.append({"prompt": prompt, "json_schema": json_schema, "grammar": grammar, "system": system})
        r = self.replies.pop(0)
        if isinstance(r, LLMReply):
            return r
        return LLMReply(text=r if isinstance(r, str) else json.dumps(r), backend="npu",
                        model="m.gguf", prefill_tokens=500, decode_tokens=40,
                        prefill_ms=500.0, decode_ms=1900.0, latency_ms=2450.0)


@pytest.fixture
def make(tmp_path):
    def _make(*replies):
        b = Scripted(*replies)
        return Dialog(b, clock=CLOCK, report_dir=tmp_path), b
    return _make


def test_problem_statement_example_end_to_end(make, tmp_path):
    d, b = make({"tool": "update_report", "args": {"t": "water_leak", "l": "near Conference Room 2",
                                                    "d": "around 10 AM",
                                                    "a": ["area isolated by maintenance"]}},
                {"tool": "update_report", "args": {"s": "medium"}},
                {"tool": "submit_report"})
    assert d.last["say"] == GREETING

    r = d.turn(EXAMPLE)
    assert r["state"] == "collecting" and r["missing"] == ["severity"] and r["asked"] == "s"
    assert r["say"] == "How serious is it: low, medium, high or critical?"
    assert r["form"]["date_time"] == {"phrase": "around 10 AM", "iso": "2026-10-10T10:00:00+05:30",
                                      "resolved": True, "approx": True}
    assert r["timings_ms"]["llm_decode"] == 1900.0 and r["devices"]["llm"] == "npu"
    assert r["timings_ms"]["stt"] is None                     # not measured here, so None

    r = d.turn("Medium.")
    assert r["fast_path"] and len(b.calls) == 1                # a bare severity word: code, no LLM call
    assert r["timings_ms"]["llm_wall"] is None and r["tool_call"] == {"tool": "update_report", "args": {"s": "medium"}}
    assert r["state"] == "review" and r["missing"] == []
    assert r["say"] == ("Here's what I have. Water leak, medium severity. Where: near Conference "
                        "Room 2. When: around 10:00 AM today. Actions taken: area isolated by "
                        "maintenance. Say submit, or tell me what to change.")

    r = d.turn("Submit.")
    assert r["fast_path"] and len(b.calls) == 1
    assert r["state"] == "submitted" and r["say"] == "Report IR-0001 submitted."
    saved = report.load("IR-0001", tmp_path)
    assert saved["form"]["incident_type"] == "water_leak" and saved["form"]["severity"] == "medium"
    assert saved["form"]["description"] == EXAMPLE            # the user's words, not "Medium."
    assert saved["form"]["follow_up"][0] == "Find and repair the source of the leak"
    assert [t["transcript"] for t in saved["turns"]] == [EXAMPLE, "Medium.", "Submit."]
    # every call carried the system prompt and the JSON schema
    assert all(c["system"] and (c.get("grammar")                                 # LLM_CONSTRAINT=gbnf
                                or c["json_schema"].get("required") == ["tool"]     # full answers
                                or "anyOf" in c["json_schema"]) for c in b.calls)  # compact (ANSWER=compact)


def test_submit_is_refused_while_a_mandatory_field_is_missing(make, tmp_path):
    d, _ = make({"tool": "update_report", "args": {"t": "water_leak", "l": "Conference Room 2"}},
                {"tool": "submit_report"})
    d.turn(EXAMPLE)
    r = d.turn("just submit it")
    assert r["state"] == "collecting" and r["report_id"] is None
    # the time ("around 10 AM") is filled from the user's words by the v4 fallback, so severity is what's missing
    assert r["say"] == "I still need the severity. How serious is it: low, medium, high or critical?"
    assert list(tmp_path.glob("*.json")) == []


def test_invented_values_never_reach_the_form(make):
    d, _ = make({"tool": "update_report", "args": {"t": "water_leak", "l": "Server Room B",
                                                    "w": ["John Smith"]}})
    r = d.turn(EXAMPLE)
    assert r["form"]["location"] is None and r["form"]["witnesses"] == []
    assert {x["field"] for x in r["dropped"]} == {"l", "w"}
    assert r["form"]["incident_type"] == "water_leak"


def test_correction_in_review_overwrites_and_reads_back_again(make):
    d, _ = make({"tool": "update_report", "args": {"t": "water_leak", "s": "low", "l": "pantry",
                                                    "d": "around 10 AM"}},
                {"tool": "update_report", "args": {"d": "11 AM"}})
    d.turn("water leak in the pantry around 10 AM, low severity")
    r = d.turn("actually it was 11 AM")
    assert r["state"] == "review" and "When: 11:00 AM today." in r["say"]
    assert r["form"]["date_time"]["phrase"] == "11 AM"


def test_low_severity_for_a_fire_is_kept_but_questioned(make):
    d, _ = make({"tool": "update_report", "args": {"t": "fire", "s": "low", "l": "workshop",
                                                    "d": "just now"}})
    r = d.turn("small fire in the workshop just now, it's low")
    assert r["form"]["severity"] == "low"                      # the user's answer stands
    assert ("Fire is usually at least high severity; you said low. Say a new severity to change it."
            in r["say"])


def test_no_op_empty_text_and_llm_failure_reask_without_changing_anything(make):
    d, b = make({"tool": "no_op"}, LLMReply(text="", status="timeout", error="slow"))
    r = d.turn("hello?")
    assert r["say"].startswith("Sorry, I didn't get anything") and r["form"]["incident_type"] is None
    r = d.turn("   ")
    assert r["say"].startswith("Sorry, I didn't catch that.") and len(b.calls) == 1   # no LLM call
    r = d.turn("there's a fire")
    assert r["error"] == "slow" and r["say"].startswith("Sorry, I had trouble")
    assert d.heard == ["hello?"]                               # the failed turn isn't kept


def test_garbage_model_output_is_a_no_op_with_an_error(make):
    d, _ = make("not json at all")
    r = d.turn(EXAMPLE)
    assert r["tool_call"] == {"tool": "no_op"} and "unusable" in r["error"]


def test_cancel_then_a_new_report_starts_clean(make):
    d, _ = make({"tool": "update_report", "args": {"t": "water_leak"}},
                {"tool": "cancel_report"},
                {"tool": "update_report", "args": {"t": "security"}})
    d.turn("water leak")
    assert d.turn("forget it")["state"] == "cancelled"
    r = d.turn("someone tailgated through the security door")
    assert r["state"] == "collecting" and r["form"]["incident_type"] == "security"


def test_review_screen_edit(make):
    d, _ = make({"tool": "update_report", "args": {"t": "water_leak", "s": "low", "l": "pantry",
                                                    "d": "around 10 AM"}})
    d.turn("water leak in the pantry around 10 AM, low severity")
    r = d.edit("location", "kitchen")
    assert r["form"]["location"] == "kitchen" and "Where: kitchen." in r["say"]


def test_a_reply_that_is_not_a_bare_answer_goes_to_the_llm_with_the_question(make):
    d, b = make({"tool": "update_report", "args": {"t": "water_leak", "l": "pantry", "d": "around 10 AM"}},
                {"tool": "update_report", "args": {"s": "medium"}})
    d.turn("water leak in the pantry around 10 AM")
    r = d.turn("medium, it could get worse")
    assert not r["fast_path"] and len(b.calls) == 2
    assert "Asked: s" in b.calls[1]["prompt"]                 # the model is told what was asked


def test_fast_path_can_be_switched_off(tmp_path):
    b = Scripted({"tool": "update_report", "args": {"t": "fire"}}, {"tool": "update_report", "args": {"s": "low"}})
    d = Dialog(b, clock=CLOCK, report_dir=tmp_path, use_fast_path=False)
    d.turn("there is a fire")
    d.asked = "s"
    assert not d.turn("low")["fast_path"] and len(b.calls) == 2


def test_gbnf_constraint_sends_the_grammar_instead_of_the_schema(tmp_path, monkeypatch):
    b = Scripted({"tool": "update_report", "args": {"t": "fire"}})
    d = Dialog(b, clock=CLOCK, report_dir=tmp_path, constraint="gbnf")
    d.turn("there is a fire")
    assert b.calls[0]["json_schema"] is None and b.calls[0]["grammar"].startswith("root ::= ")
    monkeypatch.setenv("LLM_CONSTRAINT", "gbnf")
    assert Dialog(b, clock=CLOCK, report_dir=tmp_path).constraint == "gbnf"
    monkeypatch.setenv("LLM_CONSTRAINT", "regex")
    with pytest.raises(ValueError):
        Dialog(b, clock=CLOCK, report_dir=tmp_path)


# ---- severity fallback (code fills a blank severity the user stated) ----------------------------
CV01 = ("One of the mechanics got his finger caught in the conveyor chain at the tyre line around 1:45. "
        "We stopped the conveyor. Critical.")


def test_stated_severity_fills_the_blank_and_the_dialog_reaches_review(make):
    d, _ = make({"tool": "update_report", "args": {"t": "caught_in", "l": "tyre line", "d": "around 1:45"}})
    r = d.turn(CV01)
    assert r["form"]["severity"] == "critical"
    assert r["state"] == "review" and r["missing"] == []


def test_the_models_severity_is_never_changed(make):
    d, _ = make({"tool": "update_report",
                 "args": {"t": "caught_in", "s": "high", "l": "tyre line", "d": "around 1:45"}})
    assert d.turn(CV01)["form"]["severity"] == "high"


def test_a_severity_already_on_the_form_is_never_changed(tmp_path):
    b = Scripted({"tool": "update_report", "args": {"t": "caught_in", "s": "low", "l": "tyre line"}},
                 {"tool": "update_report", "args": {"d": "around 1:45"}})
    d = Dialog(b, clock=CLOCK, report_dir=tmp_path, use_fast_path=False)
    d.turn("A finger caught in the conveyor at the tyre line, low severity")
    r = d.turn("it was around 1:45, honestly it's critical")
    assert r["form"]["severity"] == "low"


def test_no_stated_severity_leaves_it_blank_and_asks(make):
    d, _ = make({"tool": "update_report", "args": {"t": "caught_in", "l": "tyre line", "d": "around 1:45"}})
    r = d.turn("One of the mechanics got his finger caught in the conveyor at the tyre line around 1:45.")
    assert r["form"]["severity"] is None and r["asked"] == "s"


def test_saved_report_names_the_model_even_when_submit_was_answered_by_code(make, tmp_path):
    d, b = make({"tool": "update_report", "args": {"t": "water_leak", "l": "near Conference Room 2",
                                                    "d": "around 10 AM", "s": "medium"}})
    d.turn(EXAMPLE + " Severity medium.")    # said, so gate.stated_severity_only keeps the model's "s"
    r = d.turn("submit")                                      # fast path: no model on this turn
    saved = report.load(r["report_id"], tmp_path)
    assert saved["models"] == {"llm": "m.gguf", "llm_device": "npu", "type_model": None,
                               "llm_calls": 1, "fast_path_turns": 1}
