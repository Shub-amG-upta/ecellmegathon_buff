"""LlamaServerBackend against a fake llama-server (stdlib HTTP server in a thread).
Ported from FieldMind's tests/test_llamaserver_backend.py. The canned timings
use distinct values per field so a swapped or rescaled field fails."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.backends import LlamaServerBackend, LLMReply

CANNED = {
    "model": "fake-model-Q4_0.gguf",
    "choices": [{"message": {"role": "assistant", "content": '{"tool":"no_op"}'}}],
    "timings": {"prompt_n": 1234, "prompt_ms": 1357.5, "prompt_per_second": 909.0,
                "predicted_n": 37, "predicted_ms": 2661.9, "predicted_per_second": 13.9},
}


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    last_body: dict = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _Handler.last_body = json.loads(self.rfile.read(n).decode())
        mode = _Handler.mode
        if mode == "slow":
            time.sleep(1.5)
        if mode == "http500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        reply = {k: v for k, v in CANNED.items()
                 if not (mode == "no_timings" and k == "timings")}
        if mode == "partial_timings":
            reply["timings"] = {"prompt_n": 1234}
        body = b"not json{" if mode == "malformed" else json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _reset_mode():
    _Handler.mode = "ok"
    yield
    _Handler.mode = "ok"


def _backend(url, **kw):
    return LlamaServerBackend(url=url, lane="npu", model_file="cfg-model.gguf", **kw)


def test_every_field_comes_from_server_timings(server):
    r = _backend(server).generate("hello", max_tokens=60)
    assert isinstance(r, LLMReply) and r.status == "ok"
    assert r.text == '{"tool":"no_op"}'
    assert (r.prefill_tokens, r.decode_tokens) == (1234, 37)
    assert r.prefill_ms == pytest.approx(1357.5)
    assert r.decode_ms == pytest.approx(2661.9)
    assert r.backend == "npu" and r.model == "fake-model-Q4_0.gguf"
    assert r.latency_ms > 0


def test_request_carries_config_and_constraints(server):
    _backend(server, temperature=0.0, seed=7).generate("hi", max_tokens=60)
    b = _Handler.last_body
    assert b["max_tokens"] == 60 and b["seed"] == 7 and b["temperature"] == 0.0
    assert b["cache_prompt"] is False
    assert b["messages"] == [{"role": "user", "content": "hi"}]
    assert "grammar" not in b and "json_schema" not in b and "chat_template_kwargs" not in b

    _backend(server).generate("hi", grammar='root ::= "x"', system="sys")
    assert _Handler.last_body["grammar"] == 'root ::= "x"'
    assert _Handler.last_body["messages"][0] == {"role": "system", "content": "sys"}

    schema = {"type": "object", "properties": {"t": {"enum": ["a", "b"]}}}
    _backend(server, thinking=False).generate("hi", json_schema=schema)
    assert _Handler.last_body["json_schema"] == schema
    assert _Handler.last_body["chat_template_kwargs"] == {"enable_thinking": False}

    with pytest.raises(ValueError):
        _backend(server).generate("hi", grammar="g", json_schema=schema)


def test_missing_timings_are_none_not_zero_or_estimated(server):
    _Handler.mode = "no_timings"
    r = _backend(server).generate("a long prompt " * 50)
    assert r.status == "ok"
    assert (r.prefill_tokens, r.decode_tokens, r.prefill_ms, r.decode_ms) == (None,) * 4


def test_partially_missing_timings_keep_what_was_sent(server):
    _Handler.mode = "partial_timings"
    r = _backend(server).generate("x")
    assert (r.prefill_tokens, r.decode_tokens, r.prefill_ms) == (1234, None, None)


def test_timeout(server):
    _Handler.mode = "slow"
    r = _backend(server, timeout_s=0.3).generate("x")
    assert r.status == "timeout" and r.text == ""


def test_http_error_and_malformed(server):
    _Handler.mode = "http500"
    r = _backend(server).generate("x")
    assert r.status == "error" and "500" in r.error and r.prefill_tokens is None
    _Handler.mode = "malformed"
    r = _backend(server).generate("x")
    assert r.status == "error" and "malformed" in r.error


def test_connection_refused_is_error():
    r = LlamaServerBackend(url="http://127.0.0.1:9", timeout_s=2).generate("x")
    assert r.status == "error"
