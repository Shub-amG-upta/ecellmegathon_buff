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


# ---- T3 additions: number words, alignment, key words, subset, table ------------

@pytest.mark.parametrize("ref,hyp", [
    ("around 11:30", "around 1130"),
    ("around 1:30 AM", "around 1.30am"),
    ("conference hall on 2nd floor", "conference hall on second floor"),
    ("first floor", "1st floor"),
])
def test_same_time_or_ordinal_written_differently_is_not_an_error(ref, hyp):
    assert wer.wer(wer.edit_counts(wer.normalise(ref), wer.normalise(hyp))) == 0.0


def test_a_different_time_is_still_an_error():
    assert wer.edit_counts(wer.normalise("11:30"), wer.normalise("1130 pm"))["ins"] == 1
    assert wer.edit_counts(wer.normalise("11:30"), wer.normalise("11:13"))["sub"] == 1


def test_number_words_become_digits_on_both_sides():
    assert wer.normalise("ten thirty, quarter past two") == ["10", "30", "quarter", "past", "2"]
    assert wer.edit_counts(wer.normalise("around ten AM"), wer.normalise("around 10 a.m."))["sub"] == 0


def test_align_ops_match_counts():
    ref, hyp = "a b c d".split(), "a x c d e".split()
    ops = wer.align(ref, hyp)
    assert [op for op, _, _ in ops] == ["ok", "sub", "ok", "ok", "ins"]
    assert wer.edit_counts(ref, hyp) == {"sub": 1, "del": 0, "ins": 1, "ref_words": 4}


def test_names_come_from_gold_persons_and_witnesses():
    gold = {"w": ["Priya from the lab", "Mr Rao"], "p": ["the guard Mahesh"], "l": "Lab 4"}
    assert wer.names_in(gold) == {"priya", "rao", "mahesh"}


def test_keyword_errors_by_kind():
    ref = wer.normalise("sorry, it's the UPS room, not the server room, Priya saw it at 3 PM")
    hyp = wer.normalise("it's the UPS room the server room Rhea saw it at 3 PM")
    kw = wer.keyword_errors(ref, hyp, {"priya"})
    assert kw["flip"] == {"n": 2, "err": 2}          # "sorry" and "not" both lost
    assert kw["name"] == {"n": 1, "err": 1}          # Priya -> Rhea
    assert kw["number"] == {"n": 2, "err": 0}        # "3" and "pm" kept


def test_subset_run_reports_conditions_and_why(server, tmp_path):  # noqa: F811
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps({"id": "c001", "turns": [
        {"say": "There was a water leak.", "gold": {}}, {"say": "not medium", "gold": {}}]}),
        encoding="utf-8")
    audio = tmp_path / "audio"
    audio.mkdir()
    for name in ("c001_t1_sarthak.wav", "c001_t1_sarthak-noisy.wav", "c001_t2_sarthak.wav"):
        (audio / name).write_bytes(WAV)
    subset = {("c001", 1): {"case": "c001", "turn": 1, "why": ["demo"], "noisy": True}}
    res = wer.run(cases, audio, "LAPTOP", vocab="", repeat=1, subset=subset, note="test")
    assert {r["file"] for r in res["rows"]} == {"c001_t1_sarthak.wav", "c001_t1_sarthak-noisy.wav"}
    assert any("not in the speech subset" in s for s in res["skipped"])
    assert res["by_condition"]["quiet"]["files"] == 1 and res["by_condition"]["noisy"]["files"] == 1
    assert res["by_why"]["demo"]["files"] == 2 and res["note"] == "test"
    assert res["overall"]["wer"] == 0.0 and res["overall"]["ms_per_8s_median"] is not None
    md = wer.table([res])
    assert md.count("\n") == 2 and "| base.en | off | 2 | 0.0% |" in md


def test_committed_subset_lines_all_resolve():
    subset = wer.load_subset()
    turns = wer.load_turns(Path(__file__).resolve().parent.parent / "eval" / "cases.jsonl")
    assert subset and all(k in turns and turns[k]["say"] == v["say"] for k, v in subset.items())
