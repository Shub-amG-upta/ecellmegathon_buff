"""Single-writer enforcement on the multi-agent blackboard (plan rule 7).

Each test asserts on the error raised or on the board's state, never on the
owner table expression itself.
"""

import pytest

from fieldmind.agent.world_model import new_world_model
from fieldmind.multi.blackboard import (AGENTS, OWNERS, Blackboard, FactsBook,
                                        WriterError)
from fieldmind.schemas import Finding, Hypothesis


def _bb(audit=True):
    return Blackboard(new_world_model("t", []), audit=audit)


def test_owner_write_lands_on_the_board():
    bb = _bb()
    with bb.step("triage"):
        bb.write("triage", {"level": "WATCH"}, "triage")
    assert bb.read("triage")["level"] == "WATCH"


@pytest.mark.parametrize("section", sorted(OWNERS))
def test_every_section_rejects_every_non_owner(section):
    bb = _bb(audit=False)
    for agent in AGENTS:
        if agent == OWNERS[section]:
            continue
        with pytest.raises(WriterError):
            bb.write(section, {}, agent)
        with pytest.raises(WriterError):
            bb.mutable(section, agent)


def test_wrong_writer_leaves_section_unchanged():
    bb = _bb(audit=False)
    bb.write("status", {"degraded_mode": None}, "gate")
    with pytest.raises(WriterError):
        bb.write("status", {"degraded_mode": "llm_timeout"}, "diagnostician")
    assert bb.wm.degraded_mode is None


def test_owner_writing_during_another_agents_step_raises():
    bb = _bb(audit=False)
    with bb.step("sensor"):
        with pytest.raises(WriterError):
            bb.write("triage", {}, "triage")


def test_audit_catches_in_place_mutation_by_non_owner():
    # The reused world-model functions mutate in place. A non-owner that gets
    # hold of the live object (not via mutable()) must still be caught.
    bb = _bb(audit=True)
    live = bb.wm.hypotheses                     # bypasses the API on purpose
    with pytest.raises(WriterError, match="belief"):
        with bb.step("gate"):
            live.append(Hypothesis(cause="x"))


def test_audit_catches_deep_mutation_inside_a_section():
    bb = _bb(audit=True)
    with bb.step("sensor"):
        bb.mutable("findings", "sensor").append(
            Finding(id="FND1", signature_key="k", first_tick=1, last_tick=1,
                    severity="WATCH", detail="d"))
    fnd = bb.wm.open_findings[0]
    with pytest.raises(WriterError, match="findings"):
        with bb.step("triage"):
            fnd.resolved = True                  # field of an object in the list


def test_audit_allows_owner_in_place_mutation():
    bb = _bb(audit=True)
    with bb.step("retriever"):
        bb.mutable("belief", "retriever").append(Hypothesis(cause="x"))
    assert [h.cause for h in bb.wm.hypotheses] == ["x"]


def test_read_handles_are_read_only():
    bb = _bb()
    with pytest.raises((TypeError, AttributeError)):
        bb.read("belief").append(Hypothesis(cause="x"))
    with pytest.raises((TypeError, AttributeError)):
        bb.read("trust").add("drum_level")
    with pytest.raises(TypeError):
        bb.read("residuals")["last_drum_level"] = 1.0
    with pytest.raises(AttributeError):
        bb.read("facts").add(1, [])


def test_events_reach_the_log_only_through_the_gate():
    # Outbox events are logged by the gate; the sensor cannot write the log.
    bb = _bb(audit=False)
    with pytest.raises(WriterError):
        bb.mutable("events", "sensor")


def test_factsbook_keeps_two_ticks():
    b = FactsBook(keep=2)
    for t in (1, 2, 3):
        b.add(t, [])
    assert b.ticks() == [2, 3]
    with pytest.raises(KeyError):
        b.facts_of(1)


def test_read_handles_are_shallow_and_the_audit_covers_the_inside():
    # Documented limit: read() freezes the top level only. A non-owner changing
    # an object INSIDE a section goes unnoticed without the audit and raises
    # with it.
    for audit in (False, True):
        bb = _bb(audit=audit)
        with bb.step("retriever"):
            bb.mutable("belief", "retriever").append(Hypothesis(cause="x"))
        h = bb.read("belief")[0]
        if audit:
            with pytest.raises(WriterError, match="belief"):
                with bb.step("verifier"):
                    h.confidence = 0.99
        else:
            with bb.step("verifier"):
                h.confidence = 0.99
            assert bb.wm.hypotheses[0].confidence == 0.99


def test_every_world_model_field_is_in_a_section():
    from dataclasses import fields

    from fieldmind.schemas import WorldModel
    bb = _bb()
    covered = set(bb._WM_FIELDS.values()) | {"degraded_mode", "tick", "state",
                                             "episode_id"}
    assert {f.name for f in fields(WorldModel)} == covered


def test_audit_catches_a_change_to_equipment_or_episode_id():
    from fieldmind.schemas import EquipmentState
    bb = _bb()
    with pytest.raises(WriterError, match="equipment"):
        with bb.step("sensor"):
            bb.wm.equipment["FCV"] = EquipmentState(name="FCV")
    with pytest.raises(WriterError, match="status"):
        with bb.step("triage"):
            bb.wm.episode_id = "other"
