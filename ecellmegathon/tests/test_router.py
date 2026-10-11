"""app/router.py (v7): the type model replaces a type the main model gave, on the first description only."""
import json

import pytest

from app.backends import LLMReply
from app.dialog import Dialog
from app.form import load_schema
from app.router import TypeRouter



@pytest.fixture(autouse=True)
def _switches_off(monkeypatch):
    """Tests set TYPE_PREFILL / TYPE_PARALLEL themselves (constructor or env)."""
    monkeypatch.delenv("TYPE_PREFILL", raising=False)
    monkeypatch.delenv("TYPE_PARALLEL", raising=False)


TYPES = next(f for f in load_schema()["fields"] if f.get("key") == "t")["values"]


class Fake:
    def __init__(self, *texts, status="ok", model="m"):
        self.texts, self.status, self.model_file, self.calls = list(texts), status, model, []

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None, prefix=None):
        self.calls.append({"prompt": prompt, "json_schema": json_schema, "max_tokens": max_tokens})
        return LLMReply(text=self.texts.pop(0) if self.texts else "", status=self.status, model=self.model_file,
                        latency_ms=1000.0, prefill_ms=100.0, decode_ms=800.0, prefill_tokens=40, decode_tokens=20)


def _msg(form, text="A box fell off the shelf onto Ravi"):
    return f"Form: {json.dumps(form)}\nAsked: none\nUser: {text}"


def test_replaces_the_type_on_the_first_description_and_adds_the_cost():
    main = Fake(json.dumps({"tool": "update_report", "args": {"t": "fall_height", "l": "store room"}}))
    typ = Fake('{"t":"struck_by"}', model="gemma")
    r = TypeRouter(main, typ, TYPES).generate(_msg({}), system="sys", json_schema={"x": 1})
    assert json.loads(r.text) == {"tool": "update_report", "args": {"t": "struck_by", "l": "store room"}}
    assert typ.calls[0]["json_schema"]["properties"]["t"]["enum"] == TYPES and typ.calls[0]["max_tokens"] == 16
    assert r.latency_ms == 2000.0 and r.decode_tokens == 40 and r.parts[0]["type_t"] == "struck_by"


def test_never_adds_a_type_and_skips_when_the_form_has_one():
    main = Fake(json.dumps({"tool": "update_report", "args": {"l": "lab"}}),
                json.dumps({"tool": "update_report", "args": {"t": "fire"}}), '{"tool":"submit_report"}')
    typ = Fake('{"t":"electrical"}')
    router = TypeRouter(main, typ, TYPES)
    assert json.loads(router.generate(_msg({})).text)["args"] == {"l": "lab"}             # main gave no type
    assert json.loads(router.generate(_msg({"t": "fire"})).text)["args"] == {"t": "fire"}  # form already typed
    assert json.loads(router.generate(_msg({})).text) == {"tool": "submit_report"}
    assert typ.calls == [] and router.calls == 0


def test_type_model_failure_or_junk_keeps_the_main_answer():
    for typ in (Fake("", status="timeout"), Fake('{"t":"flood"}'), Fake("not json")):
        main = Fake(json.dumps({"tool": "update_report", "args": {"t": "fire"}}))
        router = TypeRouter(main, typ, TYPES)
        assert json.loads(router.generate(_msg({})).text)["args"]["t"] == "fire"
        assert router.failures == 1 and router.overrides == 0


def test_compact_answers_keep_their_shape():
    main = Fake(json.dumps({"t": "gas_leak", "l": "lab"}))
    r = TypeRouter(main, Fake('{"t":"chemical"}'), TYPES).generate(_msg({}))
    assert json.loads(r.text) == {"t": "chemical", "l": "lab"}


def test_through_the_dialog(tmp_path):
    main = Fake(json.dumps({"tool": "update_report", "args": {"t": "fall_height", "l": "store room"}}))
    d = Dialog(TypeRouter(main, Fake('{"t":"struck_by"}'), TYPES), report_dir=tmp_path)
    r = d.turn("A box fell off the shelf in the store room onto a worker")
    assert r["form"]["incident_type"] == "struck_by"


def test_make_backend_wraps_only_when_type_model_is_set(monkeypatch):
    from app import server
    monkeypatch.setenv("LLM", "llamaserver")
    monkeypatch.delenv("TYPE_MODEL", raising=False)
    assert not isinstance(server.make_backend(), TypeRouter)
    monkeypatch.setenv("TYPE_MODEL", "gemma-3-4b-it-Q4_0-pure-embq8.gguf")
    b = server.make_backend()
    assert isinstance(b, TypeRouter) and b.type_backend.url == "http://127.0.0.1:8084"


# ---- speed: TYPE_PREFILL and TYPE_PARALLEL --------------------------------------------------------

class Slow:
    """A backend that takes `delay` seconds; returns prefix + text like LlamaServerBackend."""
    def __init__(self, text, delay, model="m"):
        self.text, self.delay, self.model_file, self.calls = text, delay, model, []

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None, prefix=None):
        import time
        self.calls.append({"grammar": grammar, "prefix": prefix, "json_schema": json_schema, "max_tokens": max_tokens})
        time.sleep(self.delay)
        return LLMReply(text=(prefix or "") + self.text, model=self.model_file, latency_ms=self.delay * 1000,
                        prefill_ms=10.0, decode_ms=self.delay * 900, prefill_tokens=5, decode_tokens=3)


def test_type_prefill_sends_the_start_and_a_names_only_grammar():
    from app.router import TYPE_PREFIX, type_gbnf
    from test_grammar import gbnf_to_regex
    main = Slow(json.dumps({"tool": "update_report", "args": {"t": "fall_height"}}), 0)
    typ = Slow('":"struck_by"}', 0, model="gemma")
    r = TypeRouter(main, typ, TYPES, prefill=True).generate(_msg({}))
    assert typ.calls[0]["prefix"] == TYPE_PREFIX and typ.calls[0]["json_schema"] is None
    assert json.loads(r.text)["args"]["t"] == "struck_by"
    rx = gbnf_to_regex(type_gbnf(TYPES))
    assert all(rx.fullmatch(f'":"{t}"}}') for t in TYPES)
    assert not rx.fullmatch('":"flood"}') and not rx.fullmatch('fire"}') and not rx.fullmatch('": "fire"}')


def test_parallel_runs_both_agents_at_once_and_counts_the_wall_time_once():
    main = Slow(json.dumps({"tool": "update_report", "args": {"t": "fall_height", "l": "lab"}}), 0.3)
    typ = Slow('{"t":"struck_by"}', 0.2, model="gemma")
    router = TypeRouter(main, typ, TYPES, parallel=True)
    r = router.generate(_msg({}))
    assert json.loads(r.text)["args"] == {"t": "struck_by", "l": "lab"}
    assert 280 <= r.latency_ms < 450                       # max(0.3, 0.2), not 0.5
    assert r.decode_tokens == 3 and r.parts[0]["parallel"] is True   # main model's own counts


def test_parallel_type_answer_is_unused_when_the_main_answer_has_no_type():
    main = Slow(json.dumps({"tool": "update_report", "args": {"l": "lab"}}), 0.05)
    typ = Slow('{"t":"fire"}', 0.01)
    router = TypeRouter(main, typ, TYPES, parallel=True)
    assert json.loads(router.generate(_msg({})).text)["args"] == {"l": "lab"}
    assert router.unused == 1 and router.calls == 0
    router.generate(_msg({"t": "fire"}))                   # form already typed: no type agent at all
    assert len(typ.calls) == 1


def test_speed_switches_off_by_default(monkeypatch):
    monkeypatch.delenv("TYPE_PREFILL", raising=False)
    monkeypatch.delenv("TYPE_PARALLEL", raising=False)
    r = TypeRouter(Fake(), Fake(), TYPES)
    assert (r.prefill, r.parallel) == (False, False)
    monkeypatch.setenv("TYPE_PREFILL", "on")
    monkeypatch.setenv("TYPE_PARALLEL", "1")
    r = TypeRouter(Fake(), Fake(), TYPES)
    assert (r.prefill, r.parallel) == (True, True)
