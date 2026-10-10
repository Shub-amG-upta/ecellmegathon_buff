"""ui_dev/record_server.py: file naming, the line list, and saving takes (record mode, BUILD_PLAN §4.5).
The names must be exactly what eval/wer.py reads back for the same turn."""
from __future__ import annotations

import importlib.util
import io
import json
import math
import threading
import wave
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from app.voice import check_wav
from tests.test_voice import make_wav
from ui_dev import record_server as rs


def tone(seconds: float = 1.0, amp: int = 8000) -> bytes:
    """A 16 kHz mono 16-bit take that passes the length and silence checks."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(int(amp * math.sin(2 * math.pi * 220 * i / 16000)).to_bytes(2, "little", signed=True)
                               for i in range(int(seconds * 16000))))
    return buf.getvalue()


WAV = tone()

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("wer", ROOT / "eval" / "wer.py")
wer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wer)


def test_name_is_what_wer_reads_for_that_turn():
    name = rs.recording_name("wit-02", 1, "sarthak-noisy")
    assert name == "wit-02_t1_sarthak-noisy.wav"
    m = wer.AUDIO_NAME.match(name)
    assert (m["case"], int(m["turn"]), m["speaker"]) == ("wit-02", 1, "sarthak-noisy")


@pytest.mark.parametrize("speaker", ["", "a b", "x_y", "../up", "name.wav", "Sarthak", "sar-thak", "x-noisy-noisy"])
def test_bad_speaker_names_are_refused(speaker):
    with pytest.raises(ValueError):
        rs.recording_name("ps-01", 1, speaker)


def test_noisy_speaker():
    assert rs.noisy_speaker("priya") == "priya-noisy"
    assert rs.noisy_speaker("priya-noisy") == "priya-noisy"
    assert rs.recording_name("ps-01", 1, "priya-noisy") == "ps-01_t1_priya-noisy.wav"


def test_clip_checks_match_the_terminal_recorder():
    assert rs.clip_problem(tone(1.0)) == ""
    assert "too short" in rs.clip_problem(tone(0.5))
    assert "silent" in rs.clip_problem(tone(1.0, amp=200))
    assert "expected 16000 Hz" in rs.clip_problem(make_wav(seconds=1.0, rate=44100))


def test_line_list_is_the_subset_and_every_line_names_a_turn_wer_scores(tmp_path):
    subset = rs.load_lines()
    lines = rs.lines_for("sarthak", tmp_path)
    assert [(ln["case"], ln["turn"], ln["say"]) for ln in lines] == \
           [(s["case"], s["turn"], s["say"]) for s in subset]
    assert not any(ln["done"] for ln in lines)
    refs = wer.load_references(ROOT / "eval" / "cases.jsonl")
    for ln in lines:
        m = wer.AUDIO_NAME.match(ln["file"])
        assert m and refs[(m["case"], int(m["turn"]))] == ln["say"]


def test_noisy_pass_lists_only_noisy_lines(tmp_path):
    lines = rs.lines_for("sarthak-noisy", tmp_path)
    assert len(lines) == sum(1 for s in rs.load_lines() if s["noisy"]) > 0
    assert all(ln["noisy"] and ln["file"].endswith("_sarthak-noisy.wav") for ln in lines)


def test_save_writes_a_valid_wav_under_the_wer_name_and_marks_it_done(tmp_path):
    p = rs.save_take("ps-01", 2, "sarthak", WAV, tmp_path)
    assert p.name == "ps-01_t2_sarthak.wav" and check_wav(p.read_bytes()) == ""
    done = {ln["file"]: ln["done"] for ln in rs.lines_for("sarthak", tmp_path)}
    assert done["ps-01_t2_sarthak.wav"] is True and sum(done.values()) == 1
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("case,turn,speaker,wav,why", [
    ("ps-01", 9, "sarthak", WAV, "not in"),                       # not a subset line
    ("el-01", 2, "sarthak", WAV, "not in"),                       # a real turn, but not in the subset
    ("ps-01", 1, "sarthak", make_wav(rate=44100), "audio rejected"),
    ("ps-01", 1, "sarthak", b"", "audio rejected"),
    ("ps-01", 1, "sarthak", tone(0.5), "too short"),
    ("ps-01", 1, "sarthak", tone(1.0, amp=200), "silent"),
    ("lw-03", 1, "sarthak-noisy", WAV, "not a noisy line"),
], ids=["no-such-turn", "not-in-subset", "44k", "empty", "short", "silent", "noisy-only"])
def test_bad_takes_are_refused_and_nothing_is_written(tmp_path, case, turn, speaker, wav, why):
    with pytest.raises(ValueError, match=why):
        rs.save_take(case, turn, speaker, wav, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.fixture
def server(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), rs.make_handler(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path
    srv.shutdown()


def _post(url, body):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "audio/wav"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_save_and_lines(server):
    url, audio = server
    code, body = _post(f"{url}/api/rec/save?case=ps-01&turn=1&speaker=priya", WAV)
    assert code == 200 and body["saved"] == "ps-01_t1_priya.wav" and (audio / body["saved"]).is_file()
    code, body = _post(f"{url}/api/rec/save?case=ps-01&turn=1&speaker=bad_name", WAV)
    assert code == 400
    with urllib.request.urlopen(f"{url}/api/rec/lines?speaker=priya", timeout=5) as r:
        lines = json.loads(r.read())["lines"]
    assert [ln["done"] for ln in lines if ln["case"] == "ps-01" and ln["turn"] == 1] == [True]


def test_serves_the_record_page_and_audio_js(server):
    url, _ = server
    for path in ("/record.html", "/audio.js", "/record.js"):
        with urllib.request.urlopen(f"{url}{path}", timeout=5) as r:
            assert r.status == 200
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"{url}/../app/voice.py", timeout=5)
