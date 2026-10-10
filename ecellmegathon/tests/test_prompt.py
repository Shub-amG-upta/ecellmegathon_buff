"""The system prompts must not contain the eval set's answers: no multi-word gold value and no person name
from eval/nlu/cases.jsonl may appear in a prompt's definitions or examples (that would inflate the score)."""
import json
import re
from pathlib import Path

import pytest

from app import prompt
from app.form import load_schema

ROOT = Path(__file__).resolve().parent.parent
CASES = [json.loads(ln) for ln in (ROOT / "eval" / "nlu" / "cases.jsonl").read_text().splitlines()]


def _gold_phrases():
    phrases, names = set(), set()
    for c in CASES:
        for t in c["turns"]:
            for k, v in t["gold"]["args"].items():
                for item in (v if isinstance(v, list) else [v]):
                    if k in ("t", "s") or not isinstance(item, str):
                        continue
                    if len(item.split()) >= 2:
                        phrases.add(item.lower())
                    if k in ("p", "w"):
                        names |= {w for w in re.findall(r"\b[A-Z][a-z]{2,}\b", item)}
    return phrases, names


@pytest.mark.parametrize("version", ["2"])
def test_prompt_holds_no_eval_answer(version, monkeypatch):
    monkeypatch.setenv("PROMPT", version)
    text = prompt.system_prompt(load_schema())
    phrases, names = _gold_phrases()
    leaked = sorted(p for p in phrases if p in text.lower())
    assert leaked == [], leaked
    common = {"Example", "Form", "Asked", "User", "Answer", "The", "One", "Two", "Three", "Our", "His", "Her",
              "Sir", "Block", "Lab", "Line", "Room", "Fire", "Never"}
    leaked_names = sorted(n for n in names if re.search(rf"\b{n}\b", text) and n not in common)
    assert leaked_names == [], leaked_names


def test_version_switch(monkeypatch):
    monkeypatch.setenv("PROMPT", "1")
    v1 = prompt.system_prompt(load_schema())
    monkeypatch.setenv("PROMPT", "2")
    v2 = prompt.system_prompt(load_schema())
    assert v1 != v2 and "Incident types, how to choose" in v2 and "Example 4" in v2
    monkeypatch.setenv("PROMPT", "3")
    with pytest.raises(ValueError):
        prompt.system_prompt(load_schema())
