"""app/form.py: the LLM's JSON schema and the form state."""
import pytest

from app.form import FormState, llm_json_schema, load_schema

S = load_schema()


def test_json_schema_enums_lengths_and_tools_come_from_the_form():
    js = llm_json_schema(S)
    assert js["properties"]["tool"]["enum"] == ["update_report", "submit_report",
                                                "cancel_report", "no_op"]
    args = js["properties"]["args"]["properties"]
    assert set(args) == {"t", "s", "l", "d", "p", "e", "a", "w"}
    assert "water_leak" in args["t"]["enum"] and args["s"]["enum"] == ["low", "medium", "high", "critical"]
    assert args["l"]["maxLength"] == 60 and args["d"]["maxLength"] == 40
    assert args["p"]["maxItems"] == 4 and args["p"]["items"]["maxLength"] == 40
    assert js["properties"]["args"]["additionalProperties"] is False
    assert set(llm_json_schema(S, only=["s"])["properties"]["args"]["properties"]) == {"s"}


def test_missing_in_schema_order_then_filled():
    f = FormState(S)
    assert [m["name"] for m in f.missing()] == ["incident_type", "severity", "location",
                                                "date_time", "description"]
    f.add_transcript("There was a water leak near Conference Room 2 around 10 AM.")
    f.apply({"t": "water_leak", "l": "near Conference Room 2", "d": "around 10 AM"})
    assert [m["name"] for m in f.missing()] == ["severity"]
    f.apply({"s": "medium"})
    assert f.missing() == []


def test_correction_overwrites_and_lists_append_without_duplicates():
    f = FormState(S)
    f.apply({"d": "around 10 AM", "a": ["area isolated"]})
    changed = f.apply({"d": "around 11 AM", "a": ["Area isolated", "called plumber"]})
    assert f.values["date_time"] == "around 11 AM"
    assert f.values["immediate_actions"] == ["area isolated", "called plumber"]
    assert set(changed) == {"date_time", "immediate_actions"}
    assert f.apply({"d": "around 11 AM"}) == []                 # no change, nothing reported
    f.apply({"a": ["x", "y", "z"]})
    assert len(f.values["immediate_actions"]) == 3              # max_items


def test_description_is_the_users_words_and_follow_up_comes_from_the_catalogue():
    f = FormState(S, catalogue={"water_leak": ["Find the source"]})
    f.add_transcript("  water everywhere ")
    f.add_transcript("")
    f.add_transcript("near the pantry")
    assert f.public()["description"] == "water everywhere near the pantry"
    assert f.public()["follow_up"] == []
    f.apply({"t": "water_leak"})
    assert f.public()["follow_up"] == ["Find the source"]
    assert f.compact() == {"t": "water_leak"}


def test_review_edit_validates_enums_and_splits_lists():
    f = FormState(S)
    f.set_field("severity", "high")
    f.set_field("witnesses", "Ravi, Priya")
    assert f.values["witnesses"] == ["Ravi", "Priya"]
    with pytest.raises(ValueError):
        f.set_field("severity", "extreme")
    with pytest.raises(KeyError):
        f.set_field("description", "x")
