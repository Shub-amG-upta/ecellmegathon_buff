"""multi.compact.group_letters: the "[group X]" tag on compact case lines and
the diagnosis answer's leading "g". On (default) = every earlier prompt; off
(configs/fast.yaml since 2026-10-07) = neither shown nor asked for.
Outcome-based: assertions on the prompts and grammars a run sends."""

import json
import random
import re
from pathlib import Path

import pytest
import yaml

from bench.harness import Episode, run_episode
from fieldmind.multi import compact, grammar as G
from fieldmind.runtime.llm_backend import MockBackend

_ORIG_GENERATE = MockBackend.generate      # the unpatched mock, once per module

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
needs_dev = pytest.mark.skipif(not (DEV / "dev_A01_fcv_seize").exists(),
                               reason="dev episodes not generated")


def _cfg(group_letters: bool | None):
    from run_demo import _deep_merge
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    _deep_merge(cfg, yaml.safe_load((ROOT / "configs/fast.yaml").read_text()))
    cfg["llm"]["backend"] = "mock"
    if group_letters is None:
        cfg["multi"]["compact"].pop("group_letters", None)
    else:
        cfg["multi"]["compact"]["group_letters"] = group_letters
    return cfg


def _calls(monkeypatch, cfg, ep="dev_A01_fcv_seize"):
    """Every diagnosis call of one dev episode: (prompt, grammar)."""
    calls, orig = [], _ORIG_GENERATE

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None, **extra):
        if role == "diagnostician":
            calls.append((prompt, extra.get("grammar")))
        return orig(self, prompt, role=role, max_tokens=max_tokens, mock_hint=mock_hint)

    monkeypatch.setattr(MockBackend, "generate", generate)
    monkeypatch.setattr(MockBackend, "supports_grammar", True, raising=False)
    run_episode(Episode(DEV / ep), cfg, arch="multi")
    return [c for c in calls if "Previous output" not in c[0]]


def test_fast_overlay_turns_group_letters_off_and_base_keeps_them():
    fast = yaml.safe_load((ROOT / "configs/fast.yaml").read_text())
    base = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    assert fast["multi"]["compact"]["group_letters"] is False
    assert base["multi"]["compact"]["group_letters"] is True
    assert compact.compact_switches({"schema": True})["group_letters"] is True


@needs_dev
def test_off_no_letter_reaches_the_model_and_the_grammar_cannot_write_one(monkeypatch):
    calls = _calls(monkeypatch, _cfg(False))
    assert calls
    for prompt, gbnf in calls:
        assert "[group " not in prompt
        assert '"g"' not in prompt
        g = _parse(gbnf)
        assert G.accepts(g, '{"r":[],"sep":null,"n":[],"x":[]}')
        assert not G.accepts(g, '{"g":"A","r":[],"sep":null,"n":[],"x":[]}')


@needs_dev
def test_off_prompt_is_the_on_prompt_minus_the_letters_and_g(monkeypatch):
    on, off = _calls(monkeypatch, _cfg(True)), _calls(monkeypatch, _cfg(False))
    assert len(on) == len(off) > 0                      # same ticks, same calls
    strip = lambda p: re.sub(r" \[group [A-Z?]\]", "", p) \
        .replace('{"g":"<group letter of your first case>","r":', '{"r":') \
        .replace('{"g":"A","r":', '{"r":')
    assert all("[group " in p for p, _ in on)
    assert [strip(p) for p, _ in on] == [p for p, _ in off]


@needs_dev
def test_default_on_keeps_the_letters_and_the_g_grammar(monkeypatch):
    calls = _calls(monkeypatch, _cfg(None))
    assert calls and all("[group " in p and '"g":"<group letter' in p for p, _ in calls)
    assert all(G.accepts(_parse(gb), '{"g":"' + _first_letter(p) + '","r":[],"sep":null,"n":[],"x":[]}')
               for p, gb in calls)


def test_every_answer_the_no_letter_grammar_allows_is_clean_for_the_gate():
    lm = {"cases": [{"case_id": f"RCA-0{i}", "root_cause": "c"} for i in range(1, 5)],
          "facts": [f"F{i}" for i in range(1, 9)], "fact_detail": ["d"] * 8, "conf": [0.5] * 4,
          "context": list("abcdef"), "evidence_tick": 1}
    g, rng = G.diagnosis(8, 4, 6, ["A", "B", "C", "D"], with_group=False), random.Random(3)
    for s in {G.longest(g)} | {G.sample(g, rng) for _ in range(300)}:
        p = json.loads(s)
        assert "g" not in p and compact.check_diag_answer_shape(p) == (True, "")
        _, info = compact.expand_diag_answer(p, lm)
        assert info["g"] is None and info["bad_case_lines"] == info["bad_fact_lines"] == 0


def _first_letter(prompt: str) -> str:
    return re.search(r"\[group ([A-Z?])\]", prompt).group(1)


def _parse(text: str) -> dict:
    from tests.test_grammar import from_gbnf
    return from_gbnf(text)
