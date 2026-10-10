"""
Trim silence at both ends of a push-to-talk clip before speech-to-text.
Whisper's time grows with the audio length, and a push-to-talk clip carries
quiet before the first word and after the last. Only the ends are cut; never
anything inside the speech.

trim_silence(wav) -> (wav, info)   info = {"in_ms", "out_ms"} or {"skipped": reason}

A 20 ms window counts as speech when its RMS is at least max(MIN_RMS, REL x the
loudest window). PAD_MS of audio is kept before the first and after the last
speech window, so soft word starts and endings survive. Clips that aren't 16 kHz
mono 16-bit, or have no speech window, are returned unchanged.

Stdlib only.
"""
from __future__ import annotations

import array
import io
import math
import wave

WIN = 320                 # 20 ms at 16 kHz
# DESIGN CHOICE, check against recordings (eval/audio): speech threshold and padding.
MIN_RMS = 200             # absolute floor (16-bit scale); range 100-400
REL = 0.08                # relative to the loudest window; range 0.05-0.15
PAD_MS = 250              # kept on each side; range 150-400


def trim_silence(wav: bytes) -> tuple[bytes, dict]:
    try:
        with wave.open(io.BytesIO(wav)) as w:
            if (w.getnchannels(), w.getframerate(), w.getsampwidth()) != (1, 16000, 2):
                return wav, {"skipped": "not 16 kHz mono 16-bit"}
            pcm = array.array("h", w.readframes(w.getnframes()))
    except (wave.Error, EOFError) as e:
        return wav, {"skipped": f"not a WAV ({e})"}
    n = len(pcm)
    if n < WIN:
        return wav, {"skipped": "too short"}
    rms = [math.sqrt(sum(x * x for x in pcm[i:i + WIN]) / WIN) for i in range(0, n - WIN + 1, WIN)]
    thr = max(MIN_RMS, REL * max(rms))
    speech = [k for k, r in enumerate(rms) if r >= thr]
    if not speech:
        return wav, {"skipped": "no speech found"}
    pad = PAD_MS * 16
    start = max(0, speech[0] * WIN - pad)
    end = min(n, (speech[-1] + 1) * WIN + pad)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm[start:end].tobytes())
    return out.getvalue(), {"in_ms": round(n / 16), "out_ms": round((end - start) / 16)}
