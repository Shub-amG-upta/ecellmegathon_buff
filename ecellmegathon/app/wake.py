"""
"Hey Qualcomm": hands-free start (2026-10-11).

The page keeps the microphone open and, with plain loudness checks (no model), cuts short bursts of speech. Each
burst goes to POST /api/wake, which asks one of two detectors **on the board** whether it starts with the wake
phrase:

  kws   sherpa-onnx's keyword spotter (voice/kws_server.py, a ~3 MB model, KWS_URL): built for exactly this
  stt   the Whisper we already run: transcribe the burst, then look for the phrase in the text (this module).
        Needs no new model; heavier per burst (a whole STT call)

  WAKE   auto | kws | stt | off     auto (default): kws when KWS_URL is set, else stt

After the phrase the page records until the user pauses and sends that as a normal turn; a turn's transcript
starting with the phrase ("Hey Qualcomm, there was a fire in the lab") has it removed before the dialog sees it.
Measured before use: eval/DECISION_wake.md. Stdlib only.
"""
from __future__ import annotations

import os
import re

PHRASE = "Hey Qualcomm"

# How Whisper writes the phrase: "Hey Qualcomm", "Hi, Qualcomm.", "Hey Qualcom", "Hey, Qual-comm", "hey call com",
# "Hey Kwalcomm". Only at the start of the text (after at most one filler word), so "we called Qualcomm support"
# in the middle of a report is never a wake.
_GREETING = r"(?:hey|hi|hello|hay|hei|he|a|okay|ok)"
_NAME = r"(?:qual[\s-]*com+|qualm?com+|kwal[\s-]*com+|qual[\s-]*cam+|call[\s-]*com+|cool[\s-]*com+)"
_WAKE = re.compile(r"^\W*(?:(?:uh|um|so|okay|ok)\W+)?" + _GREETING + r"\W+" + _NAME + r"\b\W*", re.I)


def find(text: str) -> tuple[bool, str]:
    """(the text starts with the wake phrase, the words after it)."""
    m = _WAKE.match(text or "")
    if not m:
        return False, (text or "").strip()
    return True, text[m.end():].strip()


def strip(text: str) -> str:
    """A turn's transcript without a leading wake phrase ("Hey Qualcomm, fire in the lab" -> "fire in the lab")."""
    return find(text)[1]


def engine(voice_mod=None) -> str:
    """The detector to use: kws, stt or off (no speech-to-text at all means off)."""
    want = os.environ.get("WAKE", "auto")
    if want not in ("auto", "kws", "stt", "off"):
        raise ValueError(f"WAKE must be auto, kws, stt or off, not {want!r}")
    if want == "off" or voice_mod is None:
        return "off"
    if want == "auto":
        return "kws" if os.environ.get("KWS_URL") and hasattr(voice_mod, "spot") else "stt"
    if want == "kws" and not hasattr(voice_mod, "spot"):
        return "off"
    return want
