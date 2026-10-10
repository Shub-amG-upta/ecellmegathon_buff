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

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None):
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
    assert r["timings_ms"]["stt"] == 800.0 and r["timings_ms"]["tts"] == 300.0
    assert r["devices"] == {"stt": "cpu", "llm": "laptop", "tts": "cpu"}
    code, wav = call(url, r["audio_url"])
    assert code == 200 and wav == b"RIFF....WAVEfake"


def test_edit_reset_state_and_bad_requests(run):
    url = run([{"tool": "update_report", "args": {"t": "water_leak", "s": "low", "l": "pantry",
                                                   "d": "just now"}}])
    call(url, "/api/turn", {"text": "water leak in the pantry just now, low severity"})
    code, r = call(url, "/api/edit", {"field": "location", "value": "kitchen"})
    assert r["form"]["location"] == "kitchen"
    assert call(url, "/api/state")[1]["turn_id"] == r["turn_id"]
    assert call(url, "/api/edit", {"field": "severity", "value": "extreme"})[0] == 400
    assert call(url, "/api/reset", {})[1]["form"]["location"] is None


def test_static_files_fallback_page_and_no_path_escape(run):
    url = run([])
    code, page = call(url, "/")
    assert code == 200 and b"/api/turn" in page
    assert call(url, "/../app/server.py")[0] == 404
    assert call(url, "/%2e%2e/app/server.py")[0] == 404


def test_proof_off_the_board_says_so_with_nulls(run):
    url = run([])
    r = call(url, "/api/proof")[1]
    assert r["llm"]["offloaded"] is None and "not on the board's NPU" in r["llm"]["error"]
