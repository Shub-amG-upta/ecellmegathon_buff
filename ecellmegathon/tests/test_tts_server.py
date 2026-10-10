"""voice/tts_server.py: command building for laptop and board, and the HTTP
handler with a fake runner (no sherpa-onnx, no board needed)."""
from __future__ import annotations

import json
import shlex
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from voice import tts_server as ts
from tests.test_voice import WAV


def test_voice_files_on_board_are_posix():
    f = ts.voice_files("/data/local/tmp/tts/vits-piper-en_US-lessac-medium", board=True)
    assert f == {"model": "/data/local/tmp/tts/vits-piper-en_US-lessac-medium/en_US-lessac-medium.onnx",
                 "tokens": "/data/local/tmp/tts/vits-piper-en_US-lessac-medium/tokens.txt",
                 "data_dir": "/data/local/tmp/tts/vits-piper-en_US-lessac-medium/espeak-ng-data"}


def test_explicit_onnx_name_wins():
    f = ts.voice_files("/x/vits-piper-foo", onnx="bar.onnx", board=True)
    assert f["model"] == "/x/vits-piper-foo/bar.onnx"


def test_tts_args_speed_maps_to_length_scale():
    f = ts.voice_files("/v/vits-piper-a", board=True)
    args = ts.tts_args(f, "/o.wav", "hi", threads=3, speed=1.25)
    assert "--vits-length-scale=0.800" in args and "--num-threads=3" in args
    assert args[-1] == "hi" and "--output-filename=/o.wav" in args


def test_board_command_quotes_hostile_text():
    r = ts.AdbRunner()
    text = "it's at 10 AM; rm -rf / $(reboot) `id` \"x\""
    cmd = r.command(text)
    # The text reaches the engine as ONE argument, byte for byte.
    tail = cmd.split("./bin/sherpa-onnx-offline-tts ", 1)[1]
    assert shlex.split(tail)[-1] == text
    assert cmd.startswith("cd /data/local/tmp/tts && rm -f /data/local/tmp/tts/out.wav && "
                          "LD_LIBRARY_PATH=/data/local/tmp/tts/lib ")
    assert (r.device, r.model) == ("cpu", "en_US-lessac-medium")


@pytest.mark.parametrize("log,ms", [
    ("Elapsed seconds: 0.412 s\nAudio duration: 2.1 s", 412.0),
    ("Elapsed seconds: 3 s", 3000.0),
    ("no timing here", None),
])
def test_engine_ms_is_read_not_guessed(log, ms):
    assert ts.engine_ms(log) == ms


class FakeRunner:
    device, model = "laptop", "en_US-lessac-medium"

    def __init__(self, synth=None, problems=None):
        self._synth = synth or ts.Synth(wav=WAV, engine_ms=88.0)
        self._problems = problems or []
        self.texts: list[str] = []

    def health(self):
        return self._problems

    def synth(self, text):
        self.texts.append(text)
        return self._synth


@pytest.fixture
def serve():
    servers = []

    def start(runner):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), ts.make_handler(runner, threading.Lock()))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"
    yield start
    for s in servers:
        s.shutdown()


def post(url, body: bytes):
    req = urllib.request.Request(f"{url}/tts", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers.items())


def test_tts_ok_returns_wav_and_labels(serve):
    runner = FakeRunner()
    code, body, h = post(serve(runner), json.dumps({"text": "  Report   submitted. "}).encode())
    assert code == 200 and body == WAV
    assert runner.texts == ["Report submitted."]
    assert (h["X-TTS-Device"], h["X-TTS-Model"], h["X-TTS-Ms"]) == \
        ("laptop", "en_US-lessac-medium", "88.0")


def test_no_engine_time_means_no_header(serve):
    code, _, h = post(serve(FakeRunner(ts.Synth(wav=WAV))), b'{"text":"hi"}')
    assert code == 200 and "X-TTS-Ms" not in h


@pytest.mark.parametrize("body,code", [
    (b"not json", 400),
    (b'{"text": "   "}', 400),
    (json.dumps({"text": "x" * (ts.MAX_TEXT_CHARS + 1)}).encode(), 413),
], ids=["not-json", "blank", "too-long"])
def test_bad_requests(serve, body, code):
    runner = FakeRunner()
    got, _, _ = post(serve(runner), body)
    assert got == code and runner.texts == []


def test_engine_failure_is_500_with_reason(serve):
    code, body, _ = post(serve(FakeRunner(ts.Synth(error="exit 1: no model"))), b'{"text":"hi"}')
    assert code == 500 and json.loads(body)["error"] == "exit 1: no model"


def test_health_reports_missing_files(serve):
    url = serve(FakeRunner(problems=["missing: tokens.txt"]))
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{url}/health", timeout=5)
    assert e.value.code == 503
    assert json.loads(e.value.read())["problems"] == ["missing: tokens.txt"]


def test_cache_hit_skips_the_engine_and_says_so(serve):
    runner = FakeRunner()
    cache = ts.Cache(4)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ts.make_handler(runner, threading.Lock(), cache))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        _, b1, h1 = post(url, b'{"text":"Where did it happen?"}')
        _, b2, h2 = post(url, b'{"text":"Where  did it happen?"}')   # same after whitespace cleanup
    finally:
        srv.shutdown()
    assert runner.texts == ["Where did it happen?"]                  # engine ran once
    assert b1 == b2 == WAV
    assert (h1["X-TTS-Cache"], h2["X-TTS-Cache"]) == ("miss", "hit")
    assert "X-TTS-Ms" in h1 and "X-TTS-Ms" not in h2                # no engine time on a hit


def test_no_cache_header_forces_a_fresh_synthesis():
    runner = FakeRunner()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ts.make_handler(runner, threading.Lock(), ts.Cache(4)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/tts"
    try:
        post(url[:-4], b'{"text":"hi"}')
        req = urllib.request.Request(url, data=b'{"text":"hi"}', method="POST",
                                     headers={"Content-Type": "application/json", "X-TTS-No-Cache": "1"})
        with urllib.request.urlopen(req, timeout=5) as r:
            h = dict(r.headers.items())
    finally:
        srv.shutdown()
    assert runner.texts == ["hi", "hi"] and h["X-TTS-Cache"] == "miss"


def test_engine_errors_are_not_cached(serve):
    runner = FakeRunner(ts.Synth(error="boom"))
    cache = ts.Cache(4)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ts.make_handler(runner, threading.Lock(), cache))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        post(url, b'{"text":"hi"}')
        post(url, b'{"text":"hi"}')
    finally:
        srv.shutdown()
    assert runner.texts == ["hi", "hi"] and cache.items == {}


def test_cache_evicts_oldest_and_zero_disables():
    c = ts.Cache(2)
    for t in ("a", "b", "a", "c"):          # "a" refreshed, so "b" is the oldest
        c.put(t, t.encode())
    assert list(c.items) == ["a", "c"]
    off = ts.Cache(0)
    off.put("x", b"x")
    assert off.get("x") is None


def test_schema_phrases_are_the_ask_strings():
    from pathlib import Path
    schema = Path(__file__).resolve().parent.parent / "schema" / "incident_form.json"
    phrases = ts.schema_phrases(str(schema))
    assert "How serious is it: low, medium, high or critical?" in phrases
    assert "Where did it happen?" in phrases
    assert len(phrases) == 8               # description and follow_up have no question


def test_health_ok(serve):
    with urllib.request.urlopen(f"{serve(FakeRunner())}/health", timeout=5) as r:
        assert json.loads(r.read())["status"] == "ok"
