"""Hindi spoken replies: app/say_hi.py (the sentences), voice/tts_server.py (the Hindi voice) and app/server.py
(X-Reply-Language: hi)."""
from __future__ import annotations

import json
import re
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import dialog as dialog_mod
from app.backends import LLMReply
from app.dialog import Dialog
from app.say_hi import SEVERITY, TYPE, to_hindi
from app.server import App, serve
from voice import tts_server as ts

ROOT = Path(__file__).resolve().parent.parent
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def _asks():
    out = set()
    for f in ("incident_form.json", "incident_form_15.json"):
        p = ROOT / "schema" / f
        if p.exists():
            out |= {x["ask"] for x in json.loads(p.read_text(encoding="utf-8"))["fields"] if x.get("ask")}
    return sorted(out)


@pytest.mark.parametrize("sentence", _asks() + [dialog_mod.GREETING, dialog_mod.REVIEW_TAIL,
                                                "Sorry, I didn't catch that.", "Okay, I've discarded this report.",
                                                "Sorry, I didn't get anything for the report from that.",
                                                "Sorry, I had trouble with that. Could you say it again?",
                                                "Updated.", "Could you describe what happened?"])
def test_every_fixed_sentence_has_a_hindi_version(sentence):
    hi = to_hindi(sentence)
    assert hi != sentence and DEVANAGARI.search(hi) and not re.search(r"[A-Za-z]{3,}", hi)


def test_every_incident_type_said_by_the_dialog_has_hindi():
    assert set(dialog_mod._TYPE_SAID.values()) <= set(TYPE)
    assert set(SEVERITY) == {"low", "medium", "high", "critical"}


def test_readback_keeps_the_users_values_and_says_the_rest_in_hindi():
    say = ("Here's what I have. Water leak, medium severity. Where: near Conference Room 2. "
           "When: around 10:00 AM today. Actions taken: isolated the area. Say submit, or tell me what to change.")
    hi = to_hindi(say)
    assert "near Conference Room 2" in hi and "isolated the area" in hi            # the user's words, as said
    assert "पानी का रिसाव" in hi and "मीडियम" in hi and "लगभग 10:00 ए एम आज" in hi
    for english in ("Here's what I have", "severity", "Where:", "When:", "Actions taken", "Say submit"):
        assert english not in hi


def test_floor_warning_missing_field_and_submit():
    hi = to_hindi("Fire is usually at least high severity; you said medium. Say a new severity to change it.")
    assert hi.startswith("आग") and "हाई" in hi and "मीडियम" in hi and "severity" not in hi
    assert to_hindi("I still need the location. Where did it happen?") == "मुझे अभी जगह चाहिए। यह कहाँ हुआ?"
    assert to_hindi("Report IR-0012 submitted.") == "रिपोर्ट आई आर 12 सबमिट हो गई है।"


def test_unknown_text_is_spoken_as_is():
    assert to_hindi("Something the templates never say.") == "Something the templates never say."
    assert to_hindi("") == ""


# ---- TTS server: a second voice, chosen per request ------------------------------------------------------------
class Runner:
    def __init__(self, model, problems=None):
        self.device, self.model, self.texts, self._problems = "cpu", model, [], problems or []

    def health(self):
        return self._problems

    def synth(self, text):
        self.texts.append(text)
        return ts.Synth(wav=b"RIFF....WAVE" + self.model.encode(), engine_ms=10.0)


def _tts(runner, voices, cache=None):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ts.make_handler(runner, threading.Lock(), cache, voices))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _post_tts(url, obj):
    req = urllib.request.Request(f"{url}/tts", data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read(), r.headers["X-TTS-Model"]


def test_tts_server_speaks_hindi_with_the_hindi_voice_and_lists_it():
    en, hi = Runner("en_US-lessac-medium"), Runner("hi_IN-priyamvada-medium")
    srv, url = _tts(en, {"hi": hi}, ts.Cache(8))
    try:
        assert _post_tts(url, {"text": "Where did it happen?"})[1] == "en_US-lessac-medium"
        wav, model = _post_tts(url, {"text": "यह कहाँ हुआ?", "voice": "hi"})
        assert model == "hi_IN-priyamvada-medium" and wav.endswith(b"priyamvada-medium")
        _post_tts(url, {"text": "same", "voice": "hi"}); _post_tts(url, {"text": "same"})
        assert hi.texts == ["यह कहाँ हुआ?", "same"] and en.texts == ["Where did it happen?", "same"]   # cache per voice
        assert _post_tts(url, {"text": "x", "voice": "fr"})[1] == "en_US-lessac-medium"         # unknown: default
        with urllib.request.urlopen(f"{url}/health", timeout=5) as r:
            assert json.loads(r.read())["voices"] == ["en", "hi"]
    finally:
        srv.shutdown()


def test_tts_server_without_a_hindi_voice_lists_only_english():
    srv, url = _tts(Runner("en"), {})
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=5) as r:
            assert json.loads(r.read())["voices"] == ["en"]
    finally:
        srv.shutdown()


# ---- app server: X-Reply-Language: hi --------------------------------------------------------------------------
class Scripted:
    lane, model_file = "mock", "mock"

    def generate(self, prompt, **kw):
        return LLMReply(text='{"tool":"no_op"}', backend="mock", model="mock")


def _voice(spoken, voices):
    def speak(text, voice=None):
        spoken.append((voice, text))
        return SimpleNamespace(wav=b"RIFF....WAVEx", wall_ms=5.0, device="cpu", model="piper", error="")
    return SimpleNamespace(speak=speak, tts_voices=lambda: voices,
                           transcribe=lambda wav, prompt="", **kw: None)


def _app(tmp_path, voice_mod):
    srv = serve(App(Dialog(Scripted(), report_dir=tmp_path), voice_mod=voice_mod), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _post(url, path, obj, reply=None):
    h = {"Content-Type": "application/json", **({"X-Reply-Language": reply} if reply else {})}
    req = urllib.request.Request(url + path, data=json.dumps(obj).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        res = json.loads(r.read())
    if res.get("audio_url"):                              # wait for the background TTS
        with urllib.request.urlopen(url + res["audio_url"], timeout=10) as r:
            r.read()
    return res


def test_hindi_reply_is_spoken_in_hindi_with_the_hindi_voice_text_stays_english(tmp_path):
    spoken = []
    srv, url = _app(tmp_path, _voice(spoken, ["en", "hi"]))
    try:
        cfg = json.loads(urllib.request.urlopen(url + "/api/config", timeout=5).read())
        assert cfg["reply_languages"] == ["en", "hi"]
        r = _post(url, "/api/reset", {}, reply="hi")
        assert r["say"] == dialog_mod.GREETING and r["reply_language"] == "hi"         # the chat text: English
        assert spoken[-1] == ("hi", "क्या हुआ? अपने शब्दों में बताइए।")
        _post(url, "/api/reset", {})                                                      # no header: English
        assert spoken[-1] == (None, dialog_mod.GREETING)
    finally:
        srv.shutdown()


def test_without_a_hindi_voice_replies_stay_english(tmp_path):
    spoken = []
    srv, url = _app(tmp_path, _voice(spoken, ["en"]))
    try:
        cfg = json.loads(urllib.request.urlopen(url + "/api/config", timeout=5).read())
        assert cfg["reply_languages"] == ["en"]
        r = _post(url, "/api/reset", {}, reply="hi")
        assert "reply_language" not in r and spoken[-1] == (None, dialog_mod.GREETING)
    finally:
        srv.shutdown()
