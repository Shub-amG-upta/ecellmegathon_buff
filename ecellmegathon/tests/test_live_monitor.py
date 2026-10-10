"""ui_dev/live_monitor.py: the proxy must pass every call through unchanged (it sits in front of the
eval), and its summaries must read the dialog's prompt and the server's timings correctly."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.prompt import user_message
from tests.test_voice import WAV
from ui_dev import live_monitor as lm

REPLY = {"choices": [{"message": {"content": '{"tool":"update_report","args":{"s":"medium"}}'}}],
         "timings": {"prompt_n": 45, "prompt_ms": 76.0, "predicted_n": 7, "predicted_ms": 267.0,
                     "cache_n": 639}}


def test_prompt_parts_reads_the_dialog_prompt():
    msg = user_message({"t": "water_leak", "l": "near Conference Room 2"}, "s", "it's medium I think")
    p = lm.prompt_parts({"messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": msg}]})
    assert p == {"form": '{"t":"water_leak","l":"near Conference Room 2"}', "asked": "s",
                 "user": "it's medium I think"}


def test_describe_llm_shows_tokens_times_and_reply():
    req = {"messages": [{"role": "user", "content": user_message({}, None, "water leak in Lab 4")}]}
    lines = lm.describe_llm(req, REPLY, 2300.0, "QIDK NPU (HTP0)")
    text = "\n".join(lines)
    assert 'user said: "water leak in Lab 4"' in text and "asked: none" in text
    assert "prefill 45 new tok (+639 cached) 76 ms" in text
    assert "decode 7 tok 267 ms (26.2 tok/s)" in text
    assert "outside the model 1957 ms" in text
    assert '"s":"medium"' in text


def test_describe_tts_and_stt():
    t = lm.describe_tts(b'{"text":"Where did it happen?"}', WAV,
                        {"X-TTS-Device": "cpu", "X-TTS-Model": "en_US-lessac-medium", "X-TTS-Cache": "hit"}, 4.0)
    assert "cpu · en_US-lessac-medium · cache hit · audio 0.5 s out · engine not reported" in "\n".join(t)
    s = lm.describe_stt(b"--b\r\n...\r\n\r\n" + WAV, b'{"text":" medium."}', 950.0)
    assert s[0].startswith("audio 0.5 s in") and 'heard: "medium."' in s[1]


class Target(BaseHTTPRequestHandler):
    seen: dict = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        Target.seen = {"path": self.path, "body": body, "ctype": self.headers.get("Content-Type")}
        out = json.dumps(REPLY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Extra", "kept")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "LOG", tmp_path / "live.log")
    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    px = ThreadingHTTPServer(("127.0.0.1", 0),
                             lm.make_proxy("llm", f"http://127.0.0.1:{target.server_address[1]}", "NPU"))
    threading.Thread(target=px.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{px.server_address[1]}", tmp_path / "live.log"
    px.shutdown()
    target.shutdown()


def test_proxy_passes_the_call_through_unchanged_and_logs_it(proxy):
    url, log = proxy
    body = json.dumps({"messages": [{"role": "user", "content": user_message({}, "s", "medium")}]}).encode()
    req = urllib.request.Request(f"{url}/v1/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        got, headers = json.loads(r.read()), dict(r.headers.items())
    assert got == REPLY and headers["X-Extra"] == "kept"
    assert Target.seen == {"path": "/v1/chat/completions", "body": body, "ctype": "application/json"}
    text = log.read_text(encoding="utf-8")
    assert "LLM #" in text and 'user said: "medium"' in text


def test_proxy_reports_a_dead_target_as_502(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "LOG", tmp_path / "live.log")
    dead = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    port = dead.server_address[1]
    dead.server_close()
    px = ThreadingHTTPServer(("127.0.0.1", 0), lm.make_proxy("llm", f"http://127.0.0.1:{port}", "NPU"))
    threading.Thread(target=px.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(
                f"http://127.0.0.1:{px.server_address[1]}/v1/chat/completions", data=b"{}", method="POST"),
                timeout=10)
        assert e.value.code == 502
    finally:
        px.shutdown()
    assert "FAILED" in (tmp_path / "live.log").read_text(encoding="utf-8")
