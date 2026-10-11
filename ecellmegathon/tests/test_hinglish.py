"""Hinglish input (Hindi in Latin letters): time phrases, fast-path answers, the opt-in prompt rule, the
STT launch flags, and the typed eval set (eval/hinglish/). English behaviour must not change."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.fastpath import answer
from app.form import load_schema
from app.prompt import system_prompt
from app.timeparse import hinglish_to_english, resolve

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
TYPES = [f for f in load_schema()["fields"] if f.get("key") == "t"][0]["values"]
NLU = [json.loads(ln) for ln in (ROOT / "eval" / "nlu" / "cases.jsonl").read_text().splitlines()]
HI = [json.loads(ln) for ln in (ROOT / "eval" / "hinglish" / "cases.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("phrase, iso", [
    ("kal raat 8 baje", "2026-10-09T20:00"), ("subah 10 baje", "2026-10-10T10:00"),
    ("das minute pehle", "2026-10-10T14:50"), ("abhi 10 minute pehle", "2026-10-10T14:50"),
    ("do ghante pehle", "2026-10-10T13:00"), ("aadha ghanta pehle", "2026-10-10T14:30"),
    ("saadhe teen baje", "2026-10-10T15:30"), ("dedh baje", "2026-10-10T13:30"), ("dhai baje", "2026-10-10T14:30"),
    ("dopahar 2 baje", "2026-10-10T14:00"), ("shaam 5 baje", "2026-10-09T17:00"), ("abhi abhi", "2026-10-10T15:00"),
    ("lagbhag 11 baje", "2026-10-10T11:00"), ("10 baje ke aas paas", "2026-10-10T10:00"),
])
def test_hinglish_times(phrase, iso):
    r = resolve(phrase, NOW)
    assert r["resolved"] and r["iso"].startswith(iso), (phrase, r)


def test_hinglish_approx_flag():
    assert resolve("lagbhag 11 baje", NOW)["approx"] and not resolve("subah 10 baje", NOW)["approx"]


def test_english_time_phrases_are_not_rewritten():
    phrases = {t["gold"]["args"]["d"] for c in NLU for t in c["turns"] if "d" in t["gold"]["args"]}
    assert phrases
    changed = [p for p in phrases if hinglish_to_english(p.lower().strip()) != p.lower().strip()]
    assert changed == [], changed


@pytest.mark.parametrize("text, asked, state, want", [
    ("haan, submit kar do", None, "review", {"tool": "submit_report"}),
    ("haan", None, "review", {"tool": "submit_report"}),
    ("submit karo", None, "collecting", {"tool": "submit_report"}),   # early submit: code then asks what's missing
    ("cancel karo", None, "collecting", {"tool": "cancel_report"}),
    ("ok cancel kar do", "d", "collecting", {"tool": "cancel_report"}),
    ("medium hai", "s", "collecting", {"tool": "update_report", "args": {"s": "medium"}}),
    ("mujhe lagta hai high", "s", "collecting", {"tool": "update_report", "args": {"s": "high"}}),
    ("subah 10 baje", "d", "collecting", {"tool": "update_report", "args": {"d": "subah 10 baje"}}),
    ("conference room 2 mein", "l", "collecting", {"tool": "update_report", "args": {"l": "conference room 2"}}),
])
def test_hinglish_fast_path(text, asked, state, want):
    assert answer(text, asked, state, TYPES) == want


@pytest.mark.parametrize("text, asked, state", [
    ("cancel mat karo", None, "collecting"),          # don't cancel
    ("high nahi hai", "s", "collecting"),             # not high
    ("haan", None, "collecting"),                     # "yes" outside review says nothing
    ("pantry mein aag lagi hai", "l", "collecting"),  # a sentence, not a bare place
    ("gate 3 ke paas", "l", "collecting"),            # "near": left to the LLM
])
def test_hinglish_negations_and_sentences_go_to_the_llm(text, asked, state):
    assert answer(text, asked, state, TYPES) is None


def test_fast_path_matches_gold_on_the_hinglish_set():
    answered = 0
    for c in HI:
        for t in c["turns"]:
            state = "review" if t["asked"] is None and t["i"] > 0 and t["gold"]["tool"] == "submit_report" \
                else "collecting"
            got = answer(t["user"], t["asked"], state, TYPES)
            if got is None:
                continue
            answered += 1
            want = {"tool": t["gold"]["tool"], **({"args": t["gold"]["args"]} if t["gold"]["args"] else {})}
            assert got == want, (c["id"], t["i"], got, want)
    assert answered >= 15


def test_hinglish_prompt_is_on_by_default_and_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("HINGLISH", "0")
    off = system_prompt(load_schema())
    assert "Hinglish" not in off
    monkeypatch.setenv("HINGLISH", "1")
    on = system_prompt(load_schema())
    assert "Hinglish" in on and "Example H" in on and on.startswith(off.split("Rules:")[0])
    monkeypatch.delenv("HINGLISH", raising=False)          # the default (2026-10-11): on
    assert system_prompt(load_schema()) == on
    monkeypatch.setenv("HINGLISH", "yes")
    with pytest.raises(ValueError):
        system_prompt(load_schema())


def test_hinglish_cases_regenerate_identically(tmp_path):
    before = (ROOT / "eval" / "hinglish" / "cases.jsonl").read_bytes()
    subprocess.run([sys.executable, "eval/hinglish/build_cases.py"],
                   cwd=ROOT, check=True, capture_output=True)
    assert (ROOT / "eval" / "hinglish" / "cases.jsonl").read_bytes() == before


@pytest.mark.parametrize("script", ["board/voice_up.sh", "voice/laptop_up.sh"])
@pytest.mark.parametrize("env, model, flags, ok", [
    ({}, "ggml-base.bin", "-l auto --translate", True),                  # the default (2026-10-11): Hinglish on
    ({"HINGLISH": "0"}, "ggml-base.en.bin", "-l en", True),
    ({"HINGLISH": "1"}, "ggml-base.bin", "-l auto --translate", True),
    ({"HINGLISH": "1", "STT_MODEL_FILE": "ggml-small-q5_1.bin"}, "ggml-small-q5_1.bin", "-l auto --translate", True),
    ({"HINGLISH": "1", "STT_TRANSLATE": "0"}, "ggml-base.bin", "-l auto", True),
    ({"HINGLISH": "1", "STT_MODEL_FILE": "ggml-small.en.bin"}, None, None, False),
])
def test_stt_launch_flags(script, env, model, flags, ok):
    text = (ROOT / script).read_text()
    block = text[text.index("MODEL=${STT_MODEL_FILE:-ggml-base.en.bin}"):text.index("\nfi\n") + 4]
    r = subprocess.run(["bash", "-c", block + 'echo "$MODEL|$STT_LANG_FLAGS"'], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", **env})
    assert (r.returncode == 0) == ok, r.stderr
    if ok:
        assert r.stdout.strip() == f"{model}|{flags}"
