"""'Hey Qualcomm' hands-free: the phrase in a transcript (app/wake.py), POST /api/wake with both detectors, the
phrase removed from a turn, voice.spot, and voice/kws_server.py with a stand-in spotter program."""
import json
import os
import stat
import sys
import threading
import urllib.request
from types import SimpleNamespace

import pytest

from app import voice, wake
from app.backends import LLMReply
from app.dialog import Dialog
from app.server import App, serve
from voice import kws_server

WAV = (b"RIFF" + (36 + 3200).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
       + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
       + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
       + b"data" + (3200).to_bytes(4, "little") + b"\x00\x00" * 1600)


@pytest.mark.parametrize("text,rest", [
    ("Hey Qualcomm.", ""), ("Hi, Qualcomm, there was a fire in the lab", "there was a fire in the lab"),
    ("hey qualcom", ""), ("Hey, Qual-comm!", ""), ("Hey call com", ""), ("Hello Qualcomm", ""),
    ("Okay, hey Qualcomm water leak in the pantry", "water leak in the pantry"), ("Hey Kwalcomm", ""),
])
def test_the_phrase_and_what_follows(text, rest):
    assert wake.find(text) == (True, rest)


@pytest.mark.parametrize("text", ["We called Qualcomm support", "there was a fire", "hey", "Hey Quan", "",
                                  "the fire started near the Qualcomm board"])
def test_not_a_wake(text):
    assert wake.find(text)[0] is False and wake.strip(text) == text.strip()


def test_engine_choice(monkeypatch):
    with_spot = SimpleNamespace(transcribe=None, spot=None)
    for k in ("WAKE", "KWS_URL"):
        monkeypatch.delenv(k, raising=False)
    assert wake.engine(None) == "off" and wake.engine(with_spot) == "stt"
    monkeypatch.setenv("KWS_URL", "http://127.0.0.1:8085")
    assert wake.engine(with_spot) == "kws" and wake.engine(SimpleNamespace(transcribe=None)) == "stt"
    monkeypatch.setenv("WAKE", "off")
    assert wake.engine(with_spot) == "off"
    monkeypatch.setenv("WAKE", "nope")
    with pytest.raises(ValueError):
        wake.engine(with_spot)


# ---- the server -------------------------------------------------------------------------------------------

class Scripted:
    lane, model_file = "laptop", "m.gguf"

    def __init__(self, *replies):
        self.replies = list(replies)

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None, prefix=None):
        return LLMReply(text=json.dumps(self.replies.pop(0)), backend="laptop", model="m.gguf")


def _stt(text):
    return SimpleNamespace(text=text, wall_ms=700.0, stt_ms=None, device="cpu", model="base.en", error="")


def _fake_voice(heard, spot=None):
    calls = []

    def transcribe(wav, prompt="", **kw):
        calls.append(prompt)
        return _stt(heard.pop(0))
    v = SimpleNamespace(transcribe=transcribe, speak=lambda t: SimpleNamespace(wav=b"", wall_ms=1.0, device="cpu",
                                                                                model="x", error="tts off"))
    if spot:
        v.spot = spot
    return v, calls



@pytest.fixture(autouse=True)
def _no_acks(monkeypatch):
    """The "Got it." clips are made by a background speak() at startup; these tests count speak() calls."""
    monkeypatch.setenv("ACK", "off")


@pytest.fixture
def run(tmp_path):
    servers = []

    def _run(voice_mod, *replies):
        app = App(Dialog(Scripted(*replies), report_dir=tmp_path), voice_mod=voice_mod)
        srv = serve(app, 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"
    yield _run
    for s in servers:
        s.shutdown()


def post(url, path, body, ctype="audio/wav"):
    req = urllib.request.Request(url + path, data=body, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get(url, path):
    with urllib.request.urlopen(url + path, timeout=5) as r:
        return json.loads(r.read())


def test_wake_with_whisper(run, monkeypatch):
    monkeypatch.delenv("KWS_URL", raising=False)
    monkeypatch.delenv("WAKE", raising=False)
    v, prompts = _fake_voice(["Hey Qualcomm.", "Hi Qualcomm, fire in the lab", "the fire is out"])
    url = run(v)
    assert get(url, "/api/config")["wake"] == {"engine": "stt", "phrase": "Hey Qualcomm"}
    r = post(url, "/api/wake", WAV)[1]
    assert r["wake"] is True and r["engine"] == "stt" and r["rest"] == "" and r["device"] == "cpu"
    r = post(url, "/api/wake", WAV)[1]
    assert r["wake"] is True and r["rest"] == "fire in the lab"
    assert post(url, "/api/wake", WAV)[1]["wake"] is False
    assert prompts == ["", "", ""]                       # no vocabulary hint: it could make Whisper "hear" it
    assert post(url, "/api/wake", b'{"x":1}', "application/json")[0] == 400


def test_wake_with_the_keyword_spotter(run, monkeypatch):
    monkeypatch.setenv("KWS_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("WAKE", raising=False)
    seen = []

    def spot(wav):
        seen.append(len(wav))
        return voice.KwsResult(detected=True, keyword="HEY QUALCOMM", wall_ms=180.0, device="cpu")
    v, _ = _fake_voice([], spot=spot)
    url = run(v)
    r = post(url, "/api/wake", WAV)[1]
    assert r == {"wake": True, "engine": "kws", "error": "", "keyword": "HEY QUALCOMM", "ms": 180.0, "device": "cpu"}
    assert seen


def test_no_speech_means_no_hands_free(run, monkeypatch):
    monkeypatch.delenv("WAKE", raising=False)
    url = run(None)
    assert get(url, "/api/config")["wake"]["engine"] == "off"
    r = post(url, "/api/wake", WAV)[1]
    assert r["wake"] is False and "off" in r["error"]


def test_a_spoken_turn_loses_the_wake_phrase(run, monkeypatch):
    monkeypatch.delenv("WAKE", raising=False)
    v, _ = _fake_voice(["Hey Qualcomm, there was a water leak in the pantry"])
    url = run(v, {"tool": "update_report", "args": {"t": "water_leak", "l": "pantry"}})
    r = post(url, "/api/turn", WAV)[1]
    assert r["transcript"] == "there was a water leak in the pantry" and r["wake_phrase_removed"] is True
    assert r["form"]["location"] == "pantry"


# ---- voice.spot and voice/kws_server.py ---------------------------------------------------------------------

def test_kws_output_parsing_and_files():
    out = ('clip.wav\n{"start_time":0.32, "keyword": "HEY QUALCOMM", "timestamps": [0.4], "tokens":["▁HE"]}\n')
    assert kws_server.detected(out) == "HEY QUALCOMM"
    assert kws_server.detected('{"keyword": "", "tokens": []}') == "" and kws_server.detected("") == ""
    names = ["encoder-epoch-12-avg-2-chunk-16-left-64.onnx", "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
             "decoder-epoch-12-avg-2-chunk-16-left-64.onnx", "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx",
             "tokens.txt", "keywords.txt", "bpe.model"]
    f = kws_server.model_files(names, "/m", join=lambda a, b: f"{a}/{b}", int8=True)
    assert f["encoder"].endswith(".int8.onnx") and f["decoder"] == "/m/decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
    assert f["joiner"].endswith(".int8.onnx") and f["keywords"] == "/m/keywords.txt"
    args = kws_server.kws_args(f, "/c.wav", 1, 0.25, 1.0)
    assert args[-1] == "/c.wav" and "--keywords-file=/m/keywords.txt" in args


@pytest.mark.skipif(sys.platform.startswith("win"), reason="stand-in program is a shell script")
def test_kws_server_end_to_end_with_a_stand_in_spotter(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    for n in ("encoder-x.onnx", "decoder-x.onnx", "joiner-x.onnx", "tokens.txt", "keywords.txt"):
        (model / n).write_text("x")
    prog = tmp_path / "spotter"
    # prints a detection when the clip is longer than 1000 bytes (stands in for "said the phrase")
    prog.write_text('#!/bin/sh\nfor a; do f="$a"; done\n'
                    'if [ "$(wc -c < "$f")" -gt 1000 ]; then echo \'{"keyword": "HEY QUALCOMM"}\'; '
                    'else echo \'{"keyword": ""}\'; fi\n')
    prog.chmod(prog.stat().st_mode | stat.S_IEXEC)
    runner = kws_server.LocalRunner(str(prog), str(model))
    assert runner.health() == []
    srv = kws_server.ThreadingHTTPServer(("127.0.0.1", 0), kws_server.make_handler(runner, threading.Lock()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("KWS_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    r = voice.spot(WAV)
    assert (r.detected, r.keyword, r.device, r.error) == (True, "HEY QUALCOMM", "laptop", "")
    short = WAV[:44 - 4] + (0).to_bytes(4, "little")
    assert voice.spot(short).detected is False
    assert voice.spot(b"nope").error
    srv.shutdown()


def test_spot_without_a_url_is_an_error_not_an_exception(monkeypatch):
    monkeypatch.delenv("KWS_URL", raising=False)
    r = voice.spot(WAV)
    assert r.detected is False and "KWS_URL" in r.error


def test_wake_eval_counts_detections_and_false_wakes(run, monkeypatch, tmp_path):
    monkeypatch.delenv("KWS_URL", raising=False)
    monkeypatch.delenv("WAKE", raising=False)
    v, _ = _fake_voice(["Hey Qualcomm", "hey qualcom fire in the lab", "um hello", "there was a water leak",
                        "Hey Qualcomm"])
    url = run(v)
    pos, neg = tmp_path / "pos", tmp_path / "neg"
    pos.mkdir(); neg.mkdir()
    for n in ("wake_1_ana.wav", "wake_2_ana.wav", "wake_3_ravi.wav"):
        (pos / n).write_bytes(WAV)
    for n in ("ps-01_t1_ana.wav", "ps-02_t1_ravi.wav"):
        (neg / n).write_bytes(WAV)
    sys.path.insert(0, str(kws_server.Path(__file__).resolve().parent.parent / "eval"))
    import wake_eval
    monkeypatch.setattr(wake_eval, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["x", "--label", "LAPTOP", "--url", url, "--pos", str(pos), "--neg", str(neg)])
    assert wake_eval.main() == 0
    saved = json.loads(next((tmp_path / "results").glob("wake_LAPTOP_*.json")).read_text())["summary"]
    assert saved["detected"] == "2/3" and saved["detected_by_speaker"] == {"ana": "2/2", "ravi": "0/1"}
    assert saved["false_wakes"] == "1/2" and saved["engine"] == "stt"
