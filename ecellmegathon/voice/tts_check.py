"""
TTS check (BUILD_PLAN §7 "TTS check"): speak a fixed phrase list, save each WAV, time it, and write a
listening sheet for the humans to mark each phrase clear or unclear. No dataset.

  .venv/Scripts/python -m voice.tts_check --label LAPTOP
  .venv/Scripts/python -m voice.tts_check --label BOARD        (TTS_URL pointing at the board's TTS)

Every phrase goes through app.voice.speak() (the path the app uses) and the saved WAV is that reply.
Timing comes from a second request sent straight to the TTS server with `X-TTS-No-Cache: 1`, so a
phrase that is already cached (the four questions are warmed at startup) is still timed as a fresh
synthesis. Both numbers are reported: `fresh ms` is what a new sentence costs, `app ms` what the app saw.

The read-backs use the same wording as app/dialog.py (imported, not copied).
Output: results/tts_check_<label>_<laptop time>/ with NN.wav, timings.json and CHECKLIST.md.

Stdlib only.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import voice  # noqa: E402
from app.dialog import _TYPE_SAID, GREETING, REVIEW_TAIL  # noqa: E402
from app.form import load_schema  # noqa: E402


def phrases() -> list[tuple[str, str]]:
    """(kind, text) for every template worth hearing once, with hard values."""
    schema = load_schema()
    by_key = {f["key"]: f for f in schema["fields"] if f.get("key")}
    out = [("question", by_key[k]["ask"]) for k in schema["ask_order"]]
    out += [
        ("read-back", f"Here's what I have. {_TYPE_SAID['struck_by']}, high severity. Where: Lab 4. "
                      f"When: 10:45 AM today. People involved: Mr Rao. {REVIEW_TAIL}"),
        ("read-back", f"Here's what I have. {_TYPE_SAID['caught_in']}, critical severity. "
                      f"Where: Conference Room 2. When: around 3:30 PM yesterday. "
                      f"Equipment: conveyor chain. {REVIEW_TAIL}"),
        ("read-back", f"Here's what I have. {_TYPE_SAID['fire']}, low severity. Where: UPS room. "
                      f"When: 10:45 AM today. Fire is usually at least high severity; you said low. "
                      f"Say a new severity to change it. {REVIEW_TAIL}"),
        ("warning", "Fire is usually at least high severity; you said low. Say a new severity to change it."),
        ("prompt", GREETING),
        ("prompt", "Sorry, I didn't catch that."),
        ("prompt", "Report IR-0001 submitted."),
    ]
    return out


def audio_seconds(wav: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            return round(w.getnframes() / w.getframerate(), 2)
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


def fresh_timing(text: str, timeout: float = 60) -> dict:
    """One synthesis that bypasses the TTS server's cache: wall ms, the engine's own ms (if reported)."""
    url = voice._cfg("TTS_URL", "http://localhost:8083").rstrip("/") + "/tts"
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-TTS-No-Cache": "1"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            h = {k.lower(): v for k, v in r.headers.items()}
    except Exception as e:  # noqa: BLE001
        return {"fresh_ms": None, "engine_ms": None, "error": str(e)[:200]}
    eng = h.get("x-tts-ms")
    return {"fresh_ms": round((time.perf_counter() - t0) * 1000, 1),
            "engine_ms": float(eng) if eng else None, "error": ""}


def to_16k_mono(wav: bytes) -> bytes:
    """Linear resample of a 16-bit PCM WAV to 16 kHz mono, for the Whisper proxy check."""
    with wave.open(io.BytesIO(wav), "rb") as w:
        rate, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    s = memoryview(raw).cast("h")
    mono = [sum(s[i * ch:(i + 1) * ch]) / ch for i in range(n)]
    m = int(n * 16000 / rate)
    out = bytearray()
    for j in range(m):
        x = j * rate / 16000
        i = min(int(x), n - 1)
        b = mono[min(i + 1, n - 1)]
        out += int(max(-32768, min(32767, mono[i] + (b - mono[i]) * (x - i)))).to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(bytes(out))
    return buf.getvalue()


def checklist(label: str, rows: list[dict], stamp: str) -> str:
    lines = [f"# TTS listening check ({label}, {stamp})", "",
             "Listen to each WAV once at normal volume. Mark **clear** or **unclear**, and note any word that",
             "was wrong, mispronounced or hard to catch (e.g. \"Lab 4\" heard as \"lap for\").", "",
             "`Whisper heard` is an automatic proxy (the WAV sent back through STT), not a replacement for",
             "listening: a mismatch there is worth a careful listen.", "",
             "| # | kind | phrase | file | audio s | fresh ms | app ms | Whisper heard | clear? | note |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        f = lambda v: "–" if v is None else f"{v:,.0f}"  # noqa: E731
        lines.append(f"| {r['n']} | {r['kind']} | {r['text']} | {r['file'] or '(failed)'} | "
                     f"{r['audio_s'] if r['audio_s'] is not None else '–'} | {f(r['fresh_ms'])} | "
                     f"{f(r['app_ms'])} | {r.get('heard') or '–'} |  |  |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="speak the fixed phrase list and time it")
    ap.add_argument("--label", required=True, choices=["LAPTOP", "BOARD"])
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--whisper-proxy", action="store_true",
                    help="also transcribe each WAV with the STT at STT_URL (an automatic proxy check)")
    a = ap.parse_args(argv)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(a.out) / f"tts_check_{a.label}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for n, (kind, text) in enumerate(phrases(), start=1):
        t = voice.speak(text)
        fresh = fresh_timing(text)
        name = f"{n:02d}.wav" if t.wav else None
        if t.wav:
            (out / name).write_bytes(t.wav)
        rows.append({"n": n, "kind": kind, "text": text, "file": name, "audio_s": audio_seconds(t.wav),
                     "app_ms": t.wall_ms if t.wav else None, **fresh, "device": t.device,
                     "model": t.model, "speak_error": t.error})
        if a.whisper_proxy and t.wav:
            stt = voice.transcribe(to_16k_mono(t.wav))
            rows[-1]["heard"] = stt.text or f"(stt error: {stt.error})"
        print(f"{n:2d} {kind:9s} fresh {fresh['fresh_ms']} ms  engine {fresh['engine_ms']} ms  "
              f"audio {rows[-1]['audio_s']} s  {text[:60]}", flush=True)
    meta = {"label": a.label, "laptop_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tts_url": voice._cfg("TTS_URL", "http://localhost:8083"),
            "device": rows[0]["device"] if rows else None, "model": rows[0]["model"] if rows else None}
    (out / "timings.json").write_text(json.dumps({**meta, "rows": rows}, indent=1), encoding="utf-8")
    (out / "CHECKLIST.md").write_text(checklist(a.label, rows, stamp), encoding="utf-8")
    ok = [r for r in rows if r["fresh_ms"] is not None and r["audio_s"]]
    if ok:
        per = sorted(r["fresh_ms"] / r["audio_s"] for r in ok)
        print(f"[{a.label}] {meta['model']} on {meta['device']}: {len(ok)}/{len(rows)} phrases, fresh synthesis "
              f"median {sorted(r['fresh_ms'] for r in ok)[len(ok) // 2]:.0f} ms, "
              f"median {per[len(per) // 2]:.0f} ms per second of audio")
    print(f"saved {out}")
    return 0 if len(ok) == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
