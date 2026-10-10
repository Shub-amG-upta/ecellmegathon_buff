"""eval/wer.py: normalisation, word alignment counts, and a full run against a fake STT server."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_voice import WAV, Fake, server  # noqa: F401  (fixture)

_spec = importlib.util.spec_from_file_location(
    "wer", Path(__file__).resolve().parent.parent / "eval" / "wer.py")
wer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wer)


@pytest.mark.parametrize("raw,words", [
    ("Around 10 AM.", ["around", "10", "am"]),
    ("around 10am", ["around", "10", "am"]),
    ("at 3 p.m.", ["at", "3", "pm"]),
    ("It's medium, I think!", ["it's", "medium", "i", "think"]),
    ("'quoted' room2", ["quoted", "room", "2"]),
])
def test_normalise(raw, words):
    assert wer.normalise(raw) == words


def test_edit_counts_each_kind():
    ref = "there was a water leak".split()
    assert wer.edit_counts(ref, ref) == {"sub": 0, "del": 0, "ins": 0, "ref_words": 5}
    assert wer.edit_counts(ref, "there was a water leaf".split())["sub"] == 1
    assert wer.edit_counts(ref, "there a water leak".split())["del"] == 1
    assert wer.edit_counts(ref, "there was a big water leak".split())["ins"] == 1
    c = wer.edit_counts(ref, [])
    assert (c["del"], wer.wer(c)) == (5, 1.0)
    assert wer.wer(wer.edit_counts([], ["x"])) is None


def test_run_matches_files_to_turns(server, tmp_path):  # noqa: F811
    cases = tmp_path / "cases.jsonl"
    cases.write_text("\n".join(json.dumps(c) for c in [
        {"id": "c001", "turns": [{"say": "There was a water leak."}, {"say": "Medium."}]},
    ]), encoding="utf-8")
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "c001_t1_sarthak.wav").write_bytes(WAV)    # fake STT replies "There was a water leak."
    (audio / "c001_t2_sarthak.wav").write_bytes(WAV)    # ...but the reference is "Medium."
    (audio / "c001_t9_sarthak.wav").write_bytes(WAV)    # no turn 9
    (audio / "random.wav").write_bytes(WAV)             # bad name
    res = wer.run(cases, audio, "LAPTOP", vocab="", repeat=1)
    by_file = {r["file"]: r for r in res["rows"]}
    assert set(by_file) == {"c001_t1_sarthak.wav", "c001_t2_sarthak.wav"}
    assert by_file["c001_t1_sarthak.wav"]["wer"] == 0.0
    assert by_file["c001_t2_sarthak.wav"]["wer"] == 5.0          # 1 sub + 4 ins over 1 word
    assert len(res["skipped"]) == 2
    assert res["label"] == "LAPTOP" and res["overall"]["ref_words"] == 6   # 5 + 1
    assert res["overall"]["wer"] == round(5 / 6, 4)                        # errors pooled, not averaged
    assert set(res["by_speaker"]) == {"sarthak"}
