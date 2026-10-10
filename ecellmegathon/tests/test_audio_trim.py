"""app/audio.py: silence is cut at the ends only, with padding; odd input passes through."""
import io
import math
import wave

from app.audio import PAD_MS, trim_silence


def _wav(segments, rate=16000, channels=1):
    """segments: list of (seconds, amplitude); a 300 Hz tone, 0 = silence."""
    frames = bytearray()
    for sec, amp in segments:
        for i in range(int(sec * rate)):
            v = int(amp * math.sin(2 * math.pi * 300 * i / rate))
            frames += v.to_bytes(2, "little", signed=True) * channels
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return out.getvalue()


def _ms(wav):
    with wave.open(io.BytesIO(wav)) as w:
        return 1000 * w.getnframes() / w.getframerate()


def test_quiet_ends_are_cut_and_padding_kept():
    wav = _wav([(1.0, 0), (1.0, 8000), (1.5, 0)])
    out, info = trim_silence(wav)
    assert info == {"in_ms": 3500, "out_ms": round(_ms(out))}
    assert abs(_ms(out) - (1000 + 2 * PAD_MS)) <= 40


def test_a_soft_word_inside_the_speech_is_never_cut():
    wav = _wav([(0.5, 0), (0.4, 8000), (0.6, 900), (0.4, 8000), (0.5, 0)])   # loud, soft, loud
    out, _ = trim_silence(wav)
    assert _ms(out) >= 1400                                                  # all three spoken parts


def test_input_that_cannot_be_trimmed_is_returned_unchanged():
    silent = _wav([(1.0, 0)])
    assert trim_silence(silent) == (silent, {"skipped": "no speech found"})
    stereo = _wav([(0.5, 8000)], channels=2)
    assert trim_silence(stereo)[0] == stereo
    assert trim_silence(b"not a wav")[0] == b"not a wav"
    starts_loud = _wav([(1.0, 8000), (1.0, 0)])
    out, info = trim_silence(starts_loud)
    assert info["out_ms"] <= 1000 + PAD_MS + 20
