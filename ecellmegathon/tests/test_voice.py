"""app/voice.py against fake STT/TTS servers (stdlib HTTP servers in threads).

The fake whisper-server parses the multipart body with the stdlib email parser,
so a malformed body or a missing field fails here, not on the board."""
from __future__ import annotations

import email.parser
import email.policy
import io
import json
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import voice


def make_wav(seconds: float = 0.5, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate) * channels * (width // 2 or 1))
    return buf.getvalue()


WAV = make_wav()


class Fake(BaseHTTPRequestHandler):
    mode = "ok"
    reply_text = " There was a water leak. [BLANK_AUDIO]"
    last: dict = {}

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        mode = Fake.mode
        if mode == "slow":
            time.sleep(1.5)
        if self.path == "/inference":
            msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            parts = {p.get_param("name", header="content-disposition"): p
                     for p in msg.iter_parts()}
            Fake.last = {"path": self.path,
                         "fields": {k: p.get_content() for k, p in parts.items() if k != "file"},
                         "file": parts["file"].get_payload(decode=True) if "file" in parts else None,
                         "file_type": parts["file"].get_content_type() if "file" in parts else None}
            if mode == "http500":
                return self._send(500, b'{"error":"boom"}', "application/json")
            if mode == "error_json":
                return self._send(200, b'{"error":"failed to read audio"}', "application/json")
            if mode == "not_json":
                return self._send(200, b"<html>", "text/html")
            if mode == "timed":
                return self._send(200, json.dumps({"text": "hi", "stt_ms": 123.4}).encode(),
                                  "application/json")
            return self._send(200, json.dumps({"text": Fake.reply_text}).encode(), "application/json")
        if self.path == "/tts":
            Fake.last = {"path": self.path, "json": json.loads(body)}
            if mode == "not_wav":
                return self._send(200, b"oops", "text/plain")
            if mode == "http500":
                return self._send(500, b'{"error":"engine died"}', "application/json")
            return self._send(200, WAV, "audio/wav",
                              {"X-TTS-Device": "cpu", "X-TTS-Model": "en_US-lessac-medium",
                               "X-TTS-Ms": "212.5"})
        self._send(404, b"", "text/plain")


@pytest.fixture
def server(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    for k in ("STT_DEVICE", "STT_MODEL", "TTS_DEVICE", "TTS_MODEL", "STT_TIMEOUT_S", "TTS_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STT_URL", url)
    monkeypatch.setenv("TTS_URL", url)
    Fake.mode, Fake.last = "ok", {}
    Fake.reply_text = " There was a water leak. [BLANK_AUDIO]"
    yield url
    srv.shutdown()


@pytest.fixture
def dead_url(monkeypatch):
    # A port that was just free: nothing listens there.
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    port = srv.server_address[1]
    srv.server_close()
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("STT_URL", url)
    monkeypatch.setenv("TTS_URL", url)
    monkeypatch.setenv("STT_TIMEOUT_S", "3")
    monkeypatch.setenv("TTS_TIMEOUT_S", "3")
    return url


# ---- STT ---------------------------------------------------------------------

def test_transcribe_sends_whisper_server_fields_and_cleans_text(server):
    r = voice.transcribe(WAV, prompt="Conference Room 2, pantry")
    assert r.error == ""
    assert r.text == "There was a water leak."
    assert Fake.last["path"] == "/inference"
    assert Fake.last["file"] == WAV and Fake.last["file_type"] == "audio/wav"
    assert Fake.last["fields"] == {"response_format": "json", "temperature": "0.0",
                                   "prompt": "Conference Room 2, pantry"}
    assert r.wall_ms > 0
    assert r.stt_ms is None            # whisper-server reports none: never a guess
    assert (r.device, r.model) == ("laptop", "ggml-base.en.bin")


def test_language_and_translate_are_sent_only_when_given(server):
    voice.transcribe(WAV, language="hi", translate=True)
    assert Fake.last["fields"]["language"] == "hi" and Fake.last["fields"]["translate"] == "true"
    voice.transcribe(WAV, language="en", translate=False)
    assert Fake.last["fields"]["language"] == "en" and Fake.last["fields"]["translate"] == "false"
    voice.transcribe(WAV)
    assert "language" not in Fake.last["fields"] and "translate" not in Fake.last["fields"]


def test_no_prompt_field_when_prompt_empty(server):
    voice.transcribe(WAV, prompt="  ")
    assert "prompt" not in Fake.last["fields"]


def test_labels_come_from_env(server, monkeypatch):
    monkeypatch.setenv("STT_DEVICE", "cpu")
    monkeypatch.setenv("STT_MODEL", "ggml-small.en.bin")
    r = voice.transcribe(WAV)
    assert (r.device, r.model) == ("cpu", "ggml-small.en.bin")


def test_engine_reported_time_is_kept(server):
    Fake.mode = "timed"
    assert voice.transcribe(WAV).stt_ms == 123.4


def test_silence_becomes_empty_text(server):
    Fake.reply_text = " [BLANK_AUDIO]\n (wind blowing) "
    r = voice.transcribe(WAV)
    assert r.text == "" and r.error == ""


@pytest.mark.parametrize("raw,clean", [
    ("  hello   world ", "hello world"),
    ("[ Silence ]", ""),
    ("it's medium [MUSIC] I think", "it's medium I think"),
    ("", ""),
])
def test_clean_transcript(raw, clean):
    assert voice.clean_transcript(raw) == clean


@pytest.mark.parametrize("wav,why", [
    (b"", "empty audio"),
    (b"RIFFnotawav", "not a PCM WAV"),
    (make_wav(rate=44100), "expected 16000 Hz mono 16-bit"),
    (make_wav(channels=2), "expected 16000 Hz mono 16-bit"),
    (make_wav(seconds=0), "no samples"),
], ids=["empty", "garbage", "44k", "stereo", "zero-frames"])
def test_bad_audio_is_an_error_without_a_request(server, wav, why):
    r = voice.transcribe(wav)
    assert r.text == "" and why in r.error
    assert Fake.last == {}             # rejected before the network


def test_non_bytes_audio_is_an_error(server):
    r = voice.transcribe("not bytes")  # type: ignore[arg-type]
    assert r.text == "" and "bytes" in r.error


@pytest.mark.parametrize("mode,why", [
    ("http500", "HTTP 500"),
    ("error_json", "failed to read audio"),
    ("not_json", "not JSON"),
])
def test_server_failures_are_errors_not_exceptions(server, mode, why):
    Fake.mode = mode
    r = voice.transcribe(WAV)
    assert r.text == "" and why in r.error and r.error.startswith("stt:")


def test_stt_timeout_is_an_error(server, monkeypatch):
    monkeypatch.setenv("STT_TIMEOUT_S", "0.5")
    Fake.mode = "slow"
    r = voice.transcribe(WAV)
    assert r.text == "" and "timeout" in r.error


def test_stt_server_down_is_an_error(dead_url):
    r = voice.transcribe(WAV)
    assert r.text == "" and r.error.startswith("stt:")


# ---- TTS ---------------------------------------------------------------------

def test_speak_returns_wav_and_server_labels(server):
    r = voice.speak("  Report IR-0001 submitted. ")
    assert r.error == "" and r.wav == WAV
    assert Fake.last == {"path": "/tts", "json": {"text": "Report IR-0001 submitted."}}
    assert (r.device, r.model) == ("cpu", "en_US-lessac-medium")
    assert r.wall_ms > 0


def test_speak_empty_text_makes_no_request(server):
    r = voice.speak("   ")
    assert r.wav == b"" and r.error == "nothing to say" and Fake.last == {}


@pytest.mark.parametrize("mode,why", [("not_wav", "not a WAV"), ("http500", "HTTP 500")])
def test_speak_failures_are_errors(server, mode, why):
    Fake.mode = mode
    r = voice.speak("hello")
    assert r.wav == b"" and why in r.error and r.error.startswith("tts:")


def test_tts_server_down_is_an_error(dead_url):
    r = voice.speak("hello")
    assert r.wav == b"" and r.error.startswith("tts:")
