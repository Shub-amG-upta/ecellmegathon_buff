"""multi.compact.note_source: raw. No model note reader; the diagnostician reads
the selected notes' own text in the data fence; record-facts and the token
guard unchanged; a newly selected note is new evidence for its side.
Outcome-based: assertions on the prompts and calls of real (mock) runs."""

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.multi import compact
from fieldmind.multi.agents.diagnostician import DiagnosticianAgent
from fieldmind.runtime.llm_backend import MockBackend

_ORIG_GENERATE = MockBackend.generate      # the unpatched mock, once per module

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
EP = "dev_C03_wet_coal_mild"                 # has an injected (instruction-like) note
needs_dev = pytest.mark.skipif(not (DEV / EP).exists(), reason="dev episodes not generated")


def _cfg(**compact_over):
    from run_demo import _deep_merge
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    _deep_merge(cfg, yaml.safe_load((ROOT / "configs/v3.yaml").read_text()))
    cfg["llm"]["backend"] = "mock"
    cfg["multi"]["compact"].update(compact_over)
    return cfg


def _calls(monkeypatch, cfg, ep=EP):
    calls, orig = [], _ORIG_GENERATE

    def gen(self, prompt, role="generic", max_tokens=512, mock_hint=None, **kw):
        calls.append((role, prompt))
        return orig(self, prompt, role=role, max_tokens=max_tokens, mock_hint=mock_hint)

    monkeypatch.setattr(MockBackend, "generate", gen)
    run = run_episode(Episode(DEV / ep), cfg, arch="multi")
    return calls, run


def _fence(prompt):
    return prompt.split("<<<\n")[1].split("\n>>>")[0]


def test_v3_overlay_uses_raw_notes_and_hybrid_while_fast_keeps_the_reader():
    v3 = yaml.safe_load((ROOT / "configs/v3.yaml").read_text())["multi"]
    assert v3["compact"]["note_source"] == "raw" and v3["compact"]["text_reader"] is False
    assert v3["merge_rule"] == "hybrid"
    fast = yaml.safe_load((ROOT / "configs/fast.yaml").read_text())["multi"]
    assert "note_source" not in fast["compact"] and "merge_rule" not in fast
    # v3 is fast.yaml plus exactly these three keys
    for k in ("note_source", "text_reader"):
        v3["compact"].pop(k)
    v3.pop("merge_rule")
    assert v3 == fast
    assert compact.compact_switches({"schema": True, "notes": True})["note_source"] == "reader"
    with pytest.raises(ValueError):
        compact.compact_switches({"schema": True, "notes": False, "note_source": "raw"})


def test_raw_notes_with_the_reader_still_on_are_refused():
    from bench.harness import build_multi_agent
    with pytest.raises(ValueError, match="text_reader"):
        build_multi_agent(_cfg(text_reader=True), [])


@needs_dev
def test_the_diagnostician_reads_the_notes_own_text_and_no_reader_runs(monkeypatch):
    calls, _ = _calls(monkeypatch, _cfg())
    assert calls and {r for r, _ in calls} <= {"diagnostician", "verifier"}   # no reader
    notes = {n["id"]: n for n in map(json.loads, (DEV / EP / "notes.jsonl").read_text().splitlines())}
    seen = set()
    for role, p in calls:
        if role != "diagnostician" or "Previous output" in p:
            continue
        for line in _fence(p).splitlines():
            body = line.split(". ", 1)[1] if ". " in line else line
            nid = body.split(" ", 1)[0]
            if nid in notes:
                assert body.endswith(notes[nid]["text"])          # the note's own text
                seen.add(nid)
            else:
                assert nid.startswith("R") or body == "(none relevant)"   # record-facts
        assert math.ceil(len(p) / 2.91) + 60 < 1280                # guard estimate
    assert seen
    # every note's text appears only inside the data fence
    for role, p in calls:
        outside = p.replace(_fence(p), "") if "<<<" in p else p
        assert not any(n["text"] in outside for n in notes.values())


@needs_dev
def test_reader_mode_prompts_are_unchanged_by_the_new_switch(monkeypatch):
    """note_source reader (the default) gives the same prompts as no key at all."""
    a, _ = _calls(monkeypatch, _cfg(text_reader=True, note_source="reader"))
    cfg = _cfg(text_reader=True)
    cfg["multi"]["compact"].pop("note_source")
    b, _ = _calls(monkeypatch, cfg)
    assert a == b and any("[note, " in p for r, p in a if r == "diagnostician")


def _bb(notes):
    data = {"retrieval": {"cases": [], "experience": [], "notes": notes},
            "signature": {"signature": {}}, "findings": [], "notefacts": {}}
    return SimpleNamespace(read=lambda k: data[k])


def test_a_new_note_changes_its_sides_evidence_only_with_raw_notes():
    n1 = {"id": "n1", "tags": ["drum_level"], "text": "a"}
    n2 = {"id": "n2", "tags": ["bed_temp_avg"], "text": "b"}
    fp = DiagnosticianAgent.fingerprint
    assert fp(_bb([n1]), "water", True) != fp(_bb([]), "water", True)
    assert fp(_bb([n2]), "water", True) == fp(_bb([]), "water", True)   # other side's note
    assert fp(_bb([n2]), "heat", True) != fp(_bb([]), "heat", True)
    assert fp(_bb([n1]), "water", False) == fp(_bb([]), "water", False)  # reader mode: no


@needs_dev
def test_gate_and_diagnostician_agree_on_raw_note_evidence():
    from bench.harness import build_multi_agent
    orch = build_multi_agent(_cfg(), [])[0]
    assert orch.diag.raw_notes is True and orch.gate.raw_notes is True


@needs_dev
def test_each_side_sees_only_its_own_notes(monkeypatch):
    """Retrieval made to return one water-only and one heat-only note on every
    tick (the episodes rarely select a note of the other side by themselves)."""
    from fieldmind.kb.stores import NotesStore
    extra = [{"id": "n_wat", "t": 0.0, "author": "test", "tags": ["drum_level"],
              "text": "water side test note"},
             {"id": "n_hot", "t": 0.0, "author": "test", "tags": ["bed_temp_avg"],
              "text": "heat side test note"}]
    real = NotesStore.search
    monkeypatch.setattr(NotesStore, "search",
                        lambda self, *a, **k: real(self, *a, **k) + [dict(n) for n in extra])
    seen = {"water": set(), "heat": set()}
    for ep in ("dev_A03_bfp_suction", "dev_C02_feeder_trip"):     # water, and both sides
        calls, _ = _calls(monkeypatch, _cfg(), ep)
        for role, p in calls:
            if role != "diagnostician" or "Previous output" in p:
                continue
            side = "water" if "covers the WATER side" in p else "heat"
            seen[side] |= {i for i in ("n_wat", "n_hot") if f" {i} [note," in " " + _fence(p)
                           or f"{i} [note," in _fence(p)}
    assert seen == {"water": {"n_wat"}, "heat": {"n_hot"}}
