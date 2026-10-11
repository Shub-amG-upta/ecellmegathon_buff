"""app/server.py over real HTTP, with a scripted LLM and a fake voice module
that has Track B's signatures (BUILD_PLAN §4.1)."""
import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.backends import LLMReply
from app.dialog import Dialog
from app.server import App, serve


class Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.lane, self.model_file = "laptop", "m.gguf"

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None, prefix=None):
        return LLMReply(text=json.dumps(self.replies.pop(0)), backend="laptop", model="m.gguf",
                        prefill_ms=100.0, decode_ms=200.0, latency_ms=320.0)


@dataclass
class Stt:
    text: str
    wall_ms: float
    stt_ms: float | None
    device: str
    model: str
    error: str = ""


@dataclass
class Tts:
    wav: bytes
    wall_ms: float
    device: str
    model: str
    error: str = ""


FAKE_VOICE = SimpleNamespace(
    transcribe=lambda wav, prompt="": Stt("water leak in the pantry", 800.0, 700.0, "cpu", "base.en"),
    speak=lambda text: Tts(b"RIFF....WAVEfake", 300.0, "cpu", "piper"))


@pytest.fixture
def run(tmp_path):
    servers = []

    def _run(replies, voice_mod=None):
        app = App(Dialog(Scripted(*replies), report_dir=tmp_path), voice_mod=voice_mod)
        srv = serve(app, 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"
    yield _run
    for s in servers:
        s.shutdown()


def call(url, path, body=None, ctype="application/json"):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url + path, data=data, headers={"Content-Type": ctype},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            raw, code, ct = r.read(), r.status, r.headers["Content-Type"]
    except urllib.error.HTTPError as e:
        raw, code, ct = e.read(), e.code, e.headers["Content-Type"]
    return code, (json.loads(raw) if ct.startswith("application/json") else raw)


def test_text_turns_to_a_saved_report_without_voice(run):
    url = run([{"tool": "update_report", "args": {"t": "water_leak", "s": "low", "l": "pantry",
                                                   "d": "just now"}},
               {"tool": "submit_report"}])
    code, r = call(url, "/api/turn", {"text": "water leak in the pantry just now, low severity"})
    assert code == 200 and r["state"] == "review" and r["audio_url"] is None
    assert r["timings_ms"]["stt"] is None and r["timings_ms"]["llm_decode"] == 200.0
    code, r = call(url, "/api/turn", {"text": "submit"})
    assert r["report_id"] == "IR-0001"
    listed = call(url, "/api/reports")[1]
    assert [(x["id"], x["incident_type"], x["location"]) for x in listed] == \
        [("IR-0001", "water_leak", "pantry")]
    assert call(url, "/api/reports/IR-0001")[1]["form"]["severity"] == "low"
    assert call(url, "/api/reports/IR-9999")[0] == 404


def test_wav_turn_without_voice_is_a_clear_error(run):
    url = run([])
    code, r = call(url, "/api/turn", b"RIFF....", ctype="audio/wav")
    assert code == 200 and "speech-to-text is not available" in r["error"]


def test_wav_turn_with_voice_fills_timings_devices_and_audio(run):
    url = run([{"tool": "update_report", "args": {"t": "water_leak", "l": "pantry"}}],
              voice_mod=FAKE_VOICE)
    code, r = call(url, "/api/turn", b"RIFF....", ctype="audio/wav")
    assert r["transcript"] == "water leak in the pantry"
    assert r["timings_ms"]["stt"] == 800.0
    assert r["timings_ms"]["tts"] is None                     # speech is made after the reply
    assert r["devices"]["stt"] == "cpu" and r["devices"]["llm"] == "laptop"
    req = urllib.request.Request(url + r["audio_url"])
    with urllib.request.urlopen(req, timeout=5) as resp:     # waits until the speech is ready
        assert resp.read() == b"RIFF....WAVEfake" and resp.headers["X-TTS-Ms"] == "300.0"
    state = call(url, "/api/state")[1]
    assert state["timings_ms"]["tts"] == 300.0 and state["devices"]["tts"] == "cpu"


def test_slow_speech_does_not_delay_the_form(run):
    import time as _t

    def slow_speak(text):
        _t.sleep(1.0)
        return FAKE_VOICE.speak(text)
    voice = SimpleNamespace(transcribe=FAKE_VOICE.transcribe, speak=slow_speak)
    url = run([{"tool": "update_report", "args": {"t": "water_leak", "l": "pantry"}}], voice_mod=voice)
    t0 = _t.perf_counter()
    code, r = call(url, "/api/turn", {"text": "water leak in the pantry"})
    assert code == 200 and _t.perf_counter() - t0 < 0.5 and r["audio_url"]
    code, wav = call(url, r["audio_url"])                     # arrives about 1 s later
    assert code == 200 and wav == b"RIFF....WAVEfake"


def test_failed_speech_gives_404_audio_and_an_error_in_state(run):
    voice = SimpleNamespace(transcribe=FAKE_VOICE.transcribe,
                            speak=lambda text: Tts(b"", 5.0, "cpu", "piper", "tts: engine missing"))
    url = run([{"tool": "update_report", "args": {"t": "water_leak", "l": "pantry"}}], voice_mod=voice)
    code, r = call(url, "/api/turn", {"text": "water leak in the pantry"})
    assert call(url, r["audio_url"])[0] == 404
    state = call(url, "/api/state")[1]
    assert state["audio_url"] is None and "engine missing" in state["error"]


def test_edit_reset_state_and_bad_requests(run):
    url = run([{"tool": "update_report", "args": {"t": "water_leak", "s": "low", "l": "pantry",
                                                   "d": "just now"}}])
    call(url, "/api/turn", {"text": "water leak in the pantry just now, low severity"})
    code, r = call(url, "/api/edit", {"field": "location", "value": "kitchen"})
    assert r["form"]["location"] == "kitchen"
    assert call(url, "/api/state")[1]["turn_id"] == r["turn_id"]
    assert call(url, "/api/edit", {"field": "severity", "value": "extreme"})[0] == 400
    assert call(url, "/api/reset", {})[1]["form"]["location"] is None


def test_static_ui_files_and_no_path_escape(run):
    url = run([])
    code, page = call(url, "/")
    assert code == 200 and b"<html" in page.lower()        # Track B's ui/index.html (or the dev page)
    code, js = call(url, "/app.js")
    assert code == 200 and b"/api/turn" in js
    assert call(url, "/../app/server.py")[0] == 404
    assert call(url, "/%2e%2e/app/server.py")[0] == 404


def test_proof_off_the_board_says_so_with_nulls(run):
    url = run([])
    r = call(url, "/api/proof")[1]
    assert r["llm"]["offloaded"] is None and "not on the board's NPU" in r["llm"]["error"]


def test_silence_is_trimmed_before_stt_unless_switched_off(run, monkeypatch):
    import io
    import math
    import wave
    frames = bytearray()
    for sec, amp in ((1.0, 0), (0.8, 8000), (1.0, 0)):
        for i in range(int(sec * 16000)):
            frames += int(amp * math.sin(2 * math.pi * 300 * i / 16000)).to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(bytes(frames))
    clip, heard = buf.getvalue(), []

    def transcribe(wav, prompt=""):
        heard.append(len(wav))
        return FAKE_VOICE.transcribe(wav, prompt)
    voice = SimpleNamespace(transcribe=transcribe, speak=FAKE_VOICE.speak)
    url = run([{"tool": "update_report", "args": {"t": "water_leak", "l": "pantry"}}] * 2, voice_mod=voice)
    r = call(url, "/api/turn", clip, ctype="audio/wav")[1]
    assert r["audio_trim"]["in_ms"] == 2800 and r["audio_trim"]["out_ms"] < 1500
    assert heard[-1] < len(clip)
    monkeypatch.setenv("STT_TRIM", "off")
    call(url, "/api/turn", clip, ctype="audio/wav")
    assert heard[-1] == len(clip)


def test_tts_split_serves_the_first_sentence_before_the_rest(run, monkeypatch):
    import io
    import time as _t
    import wave
    from app.server import split_sentences

    def tiny_wav(n):
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b"\x01\x00" * n)
        return b.getvalue()
    spoken = []

    def speak(text):
        _t.sleep(0.4)                                     # each sentence takes a while to make
        spoken.append(text)
        return Tts(tiny_wav(len(text)), 400.0, "cpu", "piper")
    monkeypatch.setenv("TTS_SPLIT", "on")
    voice = SimpleNamespace(transcribe=FAKE_VOICE.transcribe, speak=speak)
    url = run([{"tool": "update_report", "args": {"t": "water_leak", "s": "low", "l": "pantry", "d": "just now"}}],
              voice_mod=voice)
    t0 = _t.perf_counter()
    r = call(url, "/api/turn", {"text": "water leak in the pantry just now, low severity"})[1]
    n = len(split_sentences(r["say"]))
    assert r["state"] == "review" and n >= 3 and len(r["audio_parts"]) == n
    code, first = call(url, r["audio_parts"][0])
    t_first = _t.perf_counter() - t0
    code, whole = call(url, r["audio_url"])
    t_whole = _t.perf_counter() - t0
    assert t_first < 1.0 < t_whole                         # first sentence long before the whole reply
    with wave.open(io.BytesIO(whole)) as w:
        assert w.getnframes() == sum(len(s) for s in spoken)   # the parts, joined in order
    assert spoken == split_sentences(r["say"])
    state = call(url, "/api/state")[1]
    assert state["timings_ms"]["tts_first"] < state["timings_ms"]["tts"]


def test_sentences_split_after_full_stops_only():
    from app.server import split_sentences
    assert split_sentences("Here's what I have. Fire, low severity. When: 10:45 AM today. Say submit.") == \
        ["Here's what I have.", "Fire, low severity.", "When: 10:45 AM today.", "Say submit."]
    assert split_sentences("Report IR-0001 submitted.") == ["Report IR-0001 submitted."]
