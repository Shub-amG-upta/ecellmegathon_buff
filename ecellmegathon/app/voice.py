"""
Voice I/O: speech-to-text and text-to-speech clients (Track B, BUILD_PLAN §4.1).

Track A's dialog calls exactly two functions:

        transcribe(wav, prompt="") -> SttResult
        speak(text)                -> TtsResult
        spot(wav)                  -> KwsResult   ("Hey Qualcomm", app/wake.py; only when KWS_URL is set)

Neither ever raises. On any network, engine or input problem they return an
empty result with `error` set, and the dialog carries on (it asks the user to
repeat, or shows text without audio).

Where the engines live is configuration, not code:

  STT_URL   default http://127.0.0.1:8082   whisper.cpp `whisper-server`
                                            (POST /inference, multipart `file`)
  TTS_URL   default http://127.0.0.1:8083   voice/tts_server.py
                                            (POST /tts {"text": ...} -> audio/wav)
  KWS_URL   no default                      voice/kws_server.py, the wake-word spotter
                                            (POST /kws audio/wav -> {"detected", "keyword"})

The laptop and the QIDK expose the same API, so moving from one to the other
only changes the URLs (board/voice_up.sh forwards the board's ports to these).
Use 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and every
request then waits ~2 s before falling back to IPv4 (measured: 2,050 ms vs 10 ms).

whisper-server does not report which device or model it runs, so STT_DEVICE
and STT_MODEL label it (board/voice_up.sh prints the values to export). The
TTS server reports both in response headers.

Timings: `wall_ms` is measured here, end to end. `stt_ms` / the TTS engine time
are only filled when the engine reports them; otherwise None, never a guess.

Stdlib only, so the core can move onto the device.
"""

from __future__ import annotations

import io
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
import wave
from dataclasses import dataclass


@dataclass
class SttResult:
    text: str                 # "" when nothing was heard; never None
    wall_ms: float            # measured by the client, end to end
    stt_ms: float | None      # server-side time if the engine reports it, else None (never a guess)
    device: str               # "cpu" | "npu" | "laptop"
    model: str                # e.g. "ggml-base.en.bin"
    error: str = ""           # non-empty on failure; text is then ""


@dataclass
class TtsResult:
    wav: bytes                # complete WAV file; b"" on failure
    wall_ms: float
    device: str
    model: str
    error: str = ""


@dataclass
class KwsResult:
    detected: bool            # the keyword was heard in the clip
    keyword: str              # which one ("" when none)
    wall_ms: float
    device: str               # "cpu" (board) | "laptop"
    error: str = ""


# The audio format the contract fixes for STT input.
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes, i.e. 16-bit PCM


def _cfg(name: str, default: str) -> str:
    """Read configuration at call time, so a test or voice_up.sh can change it."""
    return os.environ.get(name, "").strip() or default


def _timeout(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def check_wav(wav: bytes) -> str:
    """"" when `wav` is a 16 kHz mono 16-bit PCM WAV with at least one frame,
    else a short reason. Catches UI encoding bugs before they reach the engine."""
    if not wav:
        return "empty audio"
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            fmt = (w.getframerate(), w.getnchannels(), w.getsampwidth())
            frames = w.getnframes()
    except (wave.Error, EOFError) as e:
        return f"not a PCM WAV file ({e})"
    if fmt != (SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH):
        return (f"expected 16000 Hz mono 16-bit, got {fmt[0]} Hz, {fmt[1]} channel(s), "
                f"{8 * fmt[2]}-bit")
    if frames == 0:
        return "audio has no samples"
    return ""


# Whisper writes non-speech as bracketed tags: "[BLANK_AUDIO]", "[MUSIC]",
# "(wind blowing)", "[ Silence ]". None of it is something the user said.
_NON_SPEECH = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def clean_transcript(text: str) -> str:
    """Drop non-speech tags and collapse whitespace. Silence becomes ""."""
    return " ".join(_NON_SPEECH.sub(" ", text or "").split())


def _multipart(fields: dict[str, str], file_field: str, filename: str,
               file_bytes: bytes, file_type: str) -> tuple[bytes, str]:
    """A multipart/form-data body (urllib has no encoder). Returns (body, content type)."""
    boundary = "----voice" + uuid.uuid4().hex
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}\r\n"
                  f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(value.encode("utf-8") + b"\r\n")
    out.write(f"--{boundary}\r\n"
              f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
              f"Content-Type: {file_type}\r\n\r\n".encode())
    out.write(file_bytes + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def _post(req: urllib.request.Request, timeout_s: float) -> tuple[bytes, dict, str]:
    """(body, headers, error). Never raises; error is "" on success."""
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.read(), dict(resp.headers.items()), ""
    except urllib.error.HTTPError as e:
        try:
            detail = e.read()[:200].decode("utf-8", "replace")
        except Exception:
            detail = ""
        return b"", {}, f"HTTP {e.code}: {detail}".strip()
    except (TimeoutError, socket.timeout):
        return b"", {}, f"timeout after {timeout_s:g}s"
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            return b"", {}, f"timeout after {timeout_s:g}s"
        return b"", {}, f"connection: {e.reason}"
    except (OSError, ValueError) as e:
        return b"", {}, f"connection: {e}"


def _header_ms(headers: dict, name: str) -> float | None:
    for k, v in headers.items():
        if k.lower() == name.lower():
            try:
                return float(v)
            except ValueError:
                return None
    return None


def _header(headers: dict, name: str, default: str) -> str:
    for k, v in headers.items():
        if k.lower() == name.lower() and v.strip():
            return v.strip()
    return default


def transcribe(wav: bytes, prompt: str = "", language: str | None = None,
               translate: bool | None = None) -> SttResult:
    """Speech to text. wav: 16 kHz mono 16-bit PCM WAV. prompt: vocabulary hint
    (the contents of data/vocab.txt). language ("en", "hi", "auto") and translate (into English) are
    whisper-server's per-request settings; None sends nothing, so the server's launch flags apply
    (Hindi / Hinglish: eval/hinglish/README.md). Never raises."""
    t0 = time.perf_counter()
    url = _cfg("STT_URL", "http://127.0.0.1:8082").rstrip("/")
    device = _cfg("STT_DEVICE", "laptop")
    model = _cfg("STT_MODEL", "ggml-base.en.bin")

    def result(text: str = "", error: str = "", stt_ms: float | None = None) -> SttResult:
        return SttResult(text=text, wall_ms=round((time.perf_counter() - t0) * 1000, 1),
                         stt_ms=stt_ms, device=device, model=model, error=error)

    if not isinstance(wav, (bytes, bytearray)):
        return result(error=f"audio must be bytes, got {type(wav).__name__}")
    bad = check_wav(bytes(wav))
    if bad:
        return result(error=bad)

    fields = {"response_format": "json", "temperature": "0.0"}
    if prompt and prompt.strip():
        fields["prompt"] = prompt.strip()
    if language:
        fields["language"] = language
    if translate is not None:
        fields["translate"] = "true" if translate else "false"
    body, ctype = _multipart(fields, "file", "audio.wav", bytes(wav), "audio/wav")
    req = urllib.request.Request(f"{url}/inference", data=body, method="POST",
                                 headers={"Content-Type": ctype})
    raw, headers, err = _post(req, _timeout("STT_TIMEOUT_S", 60.0))
    if err:
        return result(error=f"stt: {err}")
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return result(error=f"stt: reply is not JSON: {raw[:120]!r}")
    if not isinstance(data, dict):
        return result(error=f"stt: unexpected reply: {raw[:120]!r}")
    if data.get("error"):
        return result(error=f"stt: {data['error']}")
    if not isinstance(data.get("text"), str):
        return result(error=f"stt: reply has no text: {raw[:120]!r}")
    # Only an engine-reported time counts; whisper-server sends none today.
    stt_ms = data.get("stt_ms")
    stt_ms = float(stt_ms) if isinstance(stt_ms, (int, float)) else _header_ms(headers, "X-STT-Ms")
    return result(text=clean_transcript(data["text"]), stt_ms=stt_ms)


def speak(text: str) -> TtsResult:
    """Text to speech: a complete WAV file. Never raises; on failure wav is b""
    and the dialog continues without audio."""
    t0 = time.perf_counter()
    url = _cfg("TTS_URL", "http://127.0.0.1:8083").rstrip("/")
    device, model = _cfg("TTS_DEVICE", "unknown"), _cfg("TTS_MODEL", "unknown")

    def result(wav: bytes = b"", error: str = "", headers: dict | None = None) -> TtsResult:
        h = headers or {}
        return TtsResult(wav=wav, wall_ms=round((time.perf_counter() - t0) * 1000, 1),
                         device=_header(h, "X-TTS-Device", device),
                         model=_header(h, "X-TTS-Model", model), error=error)

    if not isinstance(text, str) or not text.strip():
        return result(error="nothing to say")
    req = urllib.request.Request(f"{url}/tts", method="POST",
                                 data=json.dumps({"text": text.strip()}).encode(),
                                 headers={"Content-Type": "application/json"})
    raw, headers, err = _post(req, _timeout("TTS_TIMEOUT_S", 60.0))
    if err:
        return result(error=f"tts: {err}")
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return result(error=f"tts: reply is not a WAV file: {raw[:80]!r}", headers=headers)
    return result(wav=raw, headers=headers)


def spot(wav: bytes) -> KwsResult:
    """Wake-word check of a short clip (16 kHz mono WAV) with the keyword spotter at KWS_URL. Never raises; on
    failure detected is False and error is set."""
    t0 = time.perf_counter()
    url = _cfg("KWS_URL", "").rstrip("/")

    def result(detected: bool = False, keyword: str = "", error: str = "", headers: dict | None = None) -> KwsResult:
        return KwsResult(detected=detected, keyword=keyword, wall_ms=round((time.perf_counter() - t0) * 1000, 1),
                         device=_header(headers or {}, "X-KWS-Device", _cfg("KWS_DEVICE", "unknown")), error=error)

    if not url:
        return result(error="kws: KWS_URL is not set")
    if not isinstance(wav, (bytes, bytearray)):
        return result(error=f"audio must be bytes, got {type(wav).__name__}")
    bad = check_wav(bytes(wav))
    if bad:
        return result(error=bad)
    req = urllib.request.Request(f"{url}/kws", data=bytes(wav), method="POST", headers={"Content-Type": "audio/wav"})
    raw, headers, err = _post(req, _timeout("KWS_TIMEOUT_S", 10.0))
    if err:
        return result(error=f"kws: {err}")
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return result(error=f"kws: reply is not JSON: {raw[:120]!r}", headers=headers)
    if not isinstance(data, dict) or data.get("error"):
        return result(error=f"kws: {data.get('error') if isinstance(data, dict) else data}", headers=headers)
    return result(detected=bool(data.get("detected")), keyword=str(data.get("keyword") or ""), headers=headers)
