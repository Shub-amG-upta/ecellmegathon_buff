"""v6 compact answers (ANSWER=compact): the model writes a fields object or "S"/"C"/"N"; code turns it back
into exactly the same tool call, so everything after the model is unchanged."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.backends import LLMReply  # noqa: E402
from app.dialog import Dialog  # noqa: E402
from app.form import llm_json_schema, load_schema, to_compact, to_tool_call  # noqa: E402
from app.grammar import incident_gbnf  # noqa: E402
from app.prompt import system_prompt  # noqa: E402
from test_grammar import gbnf_to_regex  # noqa: E402

CASES = [json.loads(ln) for ln in (ROOT / "eval" / "nlu" / "cases.jsonl").read_text().splitlines()]
GOLD = [t["gold"] for c in CASES for t in c["turns"]]


def _full(g):
    return {"tool": g["tool"], **({"args": g["args"]} if g["args"] else {})}


def test_every_gold_answer_round_trips_to_the_same_tool_call():
    for g in GOLD:
        compact = to_compact(_full(g))
        call, err = Dialog._parse(compact)
        assert not err and call["tool"] == g["tool"] and (call.get("args") or {}) == g["args"], compact
    assert to_compact({"tool": "submit_report"}) == '"S"' and to_compact({"tool": "no_op"}) == '"N"'


def test_compact_is_shorter_on_the_gold_answers():
    full = sum(len(json.dumps(_full(g), separators=(",", ":"))) for g in GOLD)
    compact = sum(len(to_compact(_full(g))) for g in GOLD)
    assert compact < 0.75 * full


def test_full_answers_are_still_understood_and_junk_is_not():
    assert Dialog._parse('{"tool":"cancel_report"}')[0] == {"tool": "cancel_report"}
    assert Dialog._parse('"X"')[1] and Dialog._parse("[1]")[1] and Dialog._parse("nope")[1]
    assert to_tool_call({"t": "fire"}) == {"tool": "update_report", "args": {"t": "fire"}}


def test_compact_grammar_accepts_every_gold_answer_and_rejects_the_full_form():
    rx = gbnf_to_regex(incident_gbnf(load_schema(), compact=True))
    for g in GOLD:
        assert rx.fullmatch(to_compact(_full(g))), to_compact(_full(g))
    for bad in ['{"tool":"update_report","args":{"t":"fire"}}', '"X"', "{}", '{"t":"flood"}', '{"t": "fire"}']:
        assert not rx.fullmatch(bad), bad


def test_compact_json_schema_shape():
    js = llm_json_schema(load_schema(), compact=True)
    obj, codes = js["anyOf"]
    assert set(obj["properties"]) == {"t", "s", "l", "d", "p", "e", "a", "w"} and obj["additionalProperties"] is False
    assert codes == {"type": "string", "enum": ["S", "C", "N"]}


@pytest.mark.parametrize("version", ["1", "2"])
def test_compact_prompt_examples_are_compact_and_valid(version, monkeypatch):
    monkeypatch.setenv("PROMPT", version)
    text = system_prompt(load_schema(), "compact")
    assert "update_report" not in text and '"tool"' not in text and "args" not in text
    answers = [ln[len("Answer: "):] for ln in text.splitlines() if ln.startswith("Answer: ")]
    assert len(answers) >= 3
    rx = gbnf_to_regex(incident_gbnf(load_schema(), compact=True))
    for a in answers:
        assert rx.fullmatch(a) and to_tool_call(json.loads(a)) is not None, a
    monkeypatch.setenv("ANSWER", "full")
    assert '"tool"' in system_prompt(load_schema())                 # full mode keeps the old prompt


class Scripted:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None):
        self.calls.append({"json_schema": json_schema, "grammar": grammar, "system": system})
        return LLMReply(text=self.replies.pop(0), backend="npu", model="m.gguf", decode_tokens=10,
                        decode_ms=480.0, prefill_ms=50.0, latency_ms=560.0)


def test_dialog_gives_the_same_result_with_compact_answers(tmp_path):
    clock = lambda: datetime(2026, 10, 10, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))  # noqa: E731
    example = ("There was a water leak near Conference Room 2 around 10 AM. "
               "Maintenance has already isolated the area.")
    args = {"t": "water_leak", "l": "near Conference Room 2", "d": "around 10 AM", "a": ["isolated the area"]}
    full = Dialog(Scripted(json.dumps({"tool": "update_report", "args": args})), clock=clock,
                  report_dir=tmp_path / "f", answer="full")
    b = Scripted(json.dumps(args))
    compact = Dialog(b, clock=clock, report_dir=tmp_path / "c", answer="compact")
    rf, rc = full.turn(example), compact.turn(example)
    assert rc["tool_call"] == rf["tool_call"] == {"tool": "update_report", "args": args}
    assert rc["form"] == rf["form"] and rc["say"] == rf["say"]
    sent = b.calls[0]
    assert (sent["grammar"] and sent["grammar"].startswith('root ::= "{" member')) or "anyOf" in sent["json_schema"]
    assert '"S"' in sent["system"]
