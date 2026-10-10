"""voice/tts_check.py: the phrase list covers every template the task names, with the app's own wording."""
from app.dialog import GREETING, REVIEW_TAIL
from voice import tts_check


def test_phrase_list_covers_the_required_templates():
    texts = [t for _, t in tts_check.phrases()]
    joined = " ".join(texts)
    for q in ("What kind of incident was it?", "Where did it happen?", "When did it happen?",
              "How serious is it: low, medium, high or critical?"):
        assert q in texts
    for hard in ("Conference Room 2", "Lab 4", "10:45 AM today", "Mr Rao", "Struck by an object",
                 "Caught in machinery"):
        assert hard in joined
    assert ("Fire is usually at least high severity; you said low. Say a new severity to change it."
            in texts)
    assert "Sorry, I didn't catch that." in texts and "Report IR-0001 submitted." in texts
    assert GREETING in texts and all(t.endswith(REVIEW_TAIL) for k, t in tts_check.phrases() if k == "read-back")


def test_checklist_has_one_row_per_phrase():
    rows = [{"n": 1, "kind": "prompt", "text": "Hi.", "file": "01.wav", "audio_s": 0.8,
             "fresh_ms": 2100.0, "app_ms": 3.0}]
    md = tts_check.checklist("LAPTOP", rows, "x")
    assert "| 1 | prompt | Hi. | 01.wav | 0.8 | 2,100 | 3 | – |  |  |" in md and "LAPTOP" in md


def test_to_16k_mono_gives_a_wav_the_stt_accepts():
    from app.voice import check_wav
    from tests.test_voice import make_wav
    out = tts_check.to_16k_mono(make_wav(seconds=0.5, rate=22050))
    assert check_wav(out) == "" and abs(tts_check.audio_seconds(out) - 0.5) < 0.01
