"""LlamaServerBackend against a fake llama-server (stdlib HTTP server in a thread).

Asserts on the returned LLMReply, never on the mapping expression: the canned
timings use distinct values per field so a swapped or rescaled field fails.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fieldmind.runtime.llm_backend import LlamaServerBackend, LLMReply, make_backend

CANNED = {
    "model": "fake-model-Q4_0.gguf",
    "choices": [{"message": {"role": "assistant", "content": '{"hypotheses": []}'}}],
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
        if mode == "partial_timings":       # server sends prompt_n only
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


def _backend(url, **kw):
    return LlamaServerBackend(url=url, lane="npu", model_file="cfg-model.gguf", **kw)


def test_every_field_comes_from_server_timings(server):
    _Handler.mode = "ok"
    r = _backend(server).generate("hello", role="diagnostician", max_tokens=60)
    assert isinstance(r, LLMReply) and r.status == "ok"
    assert r.text == '{"hypotheses": []}'
    assert r.prefill_tokens == 1234
    assert r.decode_tokens == 37
    assert r.ttft_ms == pytest.approx(1357.5)        # ms, server-side prefill time
    assert r.decode_ms == pytest.approx(2661.9)      # ms, server-side decode time
    assert r.backend == "npu"
    assert r.model == "fake-model-Q4_0.gguf"          # server-reported wins
    assert r.latency_ms > 0


def test_request_carries_config(server):
    _Handler.mode = "ok"
    _backend(server, temperature=0.0, seed=7).generate("hi", max_tokens=60)
    b = _Handler.last_body
    assert b["max_tokens"] == 60 and b["seed"] == 7 and b["temperature"] == 0.0
    assert b["cache_prompt"] is False and "response_format" not in b
    assert b["messages"] == [{"role": "user", "content": "hi"}]
    _backend(server, json_mode=True).generate("hi")
    assert _Handler.last_body["response_format"] == {"type": "json_object"}


def test_missing_timings_are_none_not_zero_or_estimated(server):
    _Handler.mode = "no_timings"
    r = _backend(server).generate("a long prompt " * 50)
    assert r.status == "ok"
    assert (r.prefill_tokens, r.decode_tokens, r.ttft_ms, r.decode_ms) == (None,) * 4
    _Handler.mode = "ok"


def test_partially_missing_timings_keep_what_was_sent(server):
    _Handler.mode = "partial_timings"
    r = _backend(server).generate("x")
    assert (r.prefill_tokens, r.decode_tokens, r.ttft_ms) == (1234, None, None)
    _Handler.mode = "ok"


def test_failed_call_reports_no_counts(server):
    _Handler.mode = "http500"
    r = _backend(server).generate("x")
    assert (r.prefill_tokens, r.decode_tokens, r.ttft_ms) == (None, None, None)
    _Handler.mode = "ok"


def test_timeout(server):
    _Handler.mode = "slow"
    r = _backend(server, timeout_s=0.3).generate("x")
    assert r.status == "timeout" and r.text == ""
    _Handler.mode = "ok"


def test_http_error_and_malformed(server):
    _Handler.mode = "http500"
    r = _backend(server).generate("x")
    assert r.status == "error" and "500" in r.error
    _Handler.mode = "malformed"
    r = _backend(server).generate("x")
    assert r.status == "error" and "malformed" in r.error
    _Handler.mode = "ok"


def test_connection_refused_is_error():
    r = LlamaServerBackend(url="http://127.0.0.1:9", timeout_s=2).generate("x")
    assert r.status == "error"


def test_registered_and_built_from_config(server):
    _Handler.mode = "ok"
    b = make_backend({"backend": "llamaserver",
                      "llamaserver": {"url": server, "lane": "cpu", "model_file": "m"}})
    assert isinstance(b, LlamaServerBackend)
    assert b.generate("x").backend == "cpu"
