"""Answer pre-fill (PREFILL=on): the fixed start of an update answer is written into the prompt and read in one
batch; the model writes only the fields. Plus SPEC from the environment."""
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.backends import LLMReply, LlamaServerBackend  # noqa: E402
from app.dialog import Dialog  # noqa: E402
from app.form import load_schema  # noqa: E402
from app.grammar import UPDATE_PREFIX, incident_gbnf  # noqa: E402
from test_grammar import gbnf_to_regex  # noqa: E402

TEMPLATE = "<|begin_of_text|>SYS[{s}]USER[{u}]ASSISTANT:"


class _Server(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        _Server.seen.append((self.path, body))
        if self.path == "/apply-template":
            msgs = {m["role"]: m["content"] for m in body["messages"]}
            out = {"prompt": TEMPLATE.format(s=msgs.get("system", ""), u=msgs["user"])}
        else:                                         # /completion
            out = {"content": '":{"t":"fire","l":"lab"}}', "model": "m.gguf",
                   "timings": {"prompt_n": 50, "prompt_ms": 60.0, "predicted_n": 9, "predicted_ms": 430.0}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    _Server.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_backend_prefill_uses_the_template_once_and_returns_prefix_plus_rest(server):
    b = LlamaServerBackend(url=server, lane="npu", cache_prompt=True)
    r1 = b.generate("Form: {}\nAsked: none\nUser: fire in the lab", system="SYSTEM", grammar="root ::= x",
                    prefix=UPDATE_PREFIX)
    r2 = b.generate("Form: {}\nAsked: none\nUser: smoke", system="SYSTEM", grammar="root ::= x", prefix=UPDATE_PREFIX)
    paths = [p for p, _ in _Server.seen]
    assert paths == ["/apply-template", "/completion", "/completion"]        # template rendered once per system
    sent = _Server.seen[1][1]
    assert sent["prompt"] == "SYS[SYSTEM]USER[Form: {}\nAsked: none\nUser: fire in the lab]ASSISTANT:" + UPDATE_PREFIX
    assert not sent["prompt"].startswith("<|begin_of_text|>")                # the server adds BOS itself
    assert sent["grammar"] == "root ::= x" and sent["cache_prompt"] is True and sent["n_predict"] == 128
    assert json.loads(r1.text) == {"tool": "update_report", "args": {"t": "fire", "l": "lab"}}
    assert (r1.decode_tokens, r1.decode_ms, r1.prefill_tokens) == (9, 430.0, 50)
    assert r2.status == "ok"


def test_grammar_after_the_prefix():
    rx = gbnf_to_regex(incident_gbnf(load_schema(), after_prefix=True))
    for ok in [':{"t":"fire","l":"lab"}}', ":{}}", ':{"w":["Ravi","Anil"]}}']:
        assert rx.fullmatch('"' + ok), ok
    for bad in ['"t":"fire"}}', '":{"t":"flood"}}', '":{"t":"fire"}', '": {"t":"fire"}}', "}}"]:
        assert not rx.fullmatch(bad), bad


class Fake:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None, prefix=None):
        self.calls.append({"prefix": prefix, "grammar": grammar, "json_schema": json_schema})
        t = self.texts.pop(0)
        return LLMReply(text=(prefix or "") + t, backend="npu", model="m")


def test_dialog_prefills_updates_but_not_submit_or_cancel_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFILL", "on")
    b = Fake('":{"t":"fire","l":"store room"}}', '{"tool":"submit_report"}')
    d = Dialog(b, report_dir=tmp_path)
    r = d.turn("there is a fire in the store room")
    assert b.calls[0]["prefix"] == UPDATE_PREFIX and b.calls[0]["grammar"].startswith('root ::= "\\":{" ( member')
    assert r["tool_call"] == {"tool": "update_report", "args": {"t": "fire", "l": "store room"}}
    d.turn("okay please submit the whole thing")                              # mentions submit: no pre-fill
    assert b.calls[1]["prefix"] is None and b.calls[1]["json_schema"] is not None


def test_an_empty_prefilled_answer_is_nothing_for_the_report(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFILL", "on")
    d = Dialog(Fake('":{}}'), report_dir=tmp_path)
    r = d.turn("hello, can you hear me")
    assert r["tool_call"] == {"tool": "no_op"} and r["form"]["incident_type"] is None
    assert Dialog._parse(UPDATE_PREFIX + '":{}}') == ({"tool": "no_op"}, "")


def test_prefill_off_by_default_and_never_with_compact_answers(tmp_path, monkeypatch):
    monkeypatch.delenv("PREFILL", raising=False)
    assert not Dialog(Fake(), report_dir=tmp_path).prefill
    monkeypatch.setenv("PREFILL", "on")
    assert not Dialog(Fake(), report_dir=tmp_path, answer="compact").prefill


def test_spec_from_the_environment_for_the_npu_lane(monkeypatch):
    from board import board
    name = "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf"
    sha = board.CANDIDATES[name]

    def run(*args, serial="", timeout=60):
        out = f"{sha}  x\n" if "sha256sum" in args[-1] else ("--spec-ngram-simple-size-n N" if "--help" in args[-1] else "")
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(board, "_adb", run)
    monkeypatch.setenv("SPEC", "1")
    assert board.start_lane("npu", name)["spec"] is True
    monkeypatch.setenv("SPEC", "0")
    assert board.start_lane("npu", name)["spec"] is False


def test_prefixes_end_on_a_token_boundary():
    """Board v8 run 1: a prefix ending in `"args":{` split Llama 3's token `":{"` and the model wrote `}}` on all
    64 turns. Llama 3 tokens (from the model's GGUF vocab): `{"tool":"update_report","args":{"t":"fire"}}` =
    {" | tool | ":" | update | _report | "," | args | ":{" | t | ":" | fire | "}} ; so a prefix may end after
    `args` or `t`, never after punctuation that the next token would join."""
    from app.router import TYPE_PREFIX
    for prefix in (UPDATE_PREFIX, TYPE_PREFIX):
        assert prefix[-1].isalpha(), prefix
    assert UPDATE_PREFIX == '{"tool":"update_report","args' and TYPE_PREFIX == '{"t'
