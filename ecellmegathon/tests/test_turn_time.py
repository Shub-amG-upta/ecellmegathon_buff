"""eval/turn_time.py against the real app server with a fake voice: dialogs play in order, stages are split."""
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from app.backends import LLMReply
from app.dialog import Dialog
from app.server import App, serve

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import turn_time  # noqa: E402

WAV = (b"RIFF" + (36 + 3200).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
       + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little")
       + (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
       + b"data" + (3200).to_bytes(4, "little") + b"\x00\x01" * 1600)


class Scripted:
    lane, model_file = "npu", "m.gguf"

    def generate(self, prompt, **kw):
        return LLMReply(text=json.dumps({"tool": "update_report", "args": {"t": "water_leak", "l": "pantry",
                                                                           "d": "just now"}}),
                        backend="npu", model="m.gguf", latency_ms=1700.0, prefill_ms=100.0, decode_ms=1500.0)


def test_turn_time_plays_dialogs_in_order_and_splits_the_stages(tmp_path, monkeypatch):
    monkeypatch.setenv("ACK", "off")
    heard = iter(["water leak in the pantry just now", "medium", "submit"])
    voice = SimpleNamespace(
        transcribe=lambda wav, prompt="", **k: SimpleNamespace(text=next(heard), wall_ms=900.0, stt_ms=None,
                                                               device="cpu", model="base.en", error=""),
        speak=lambda t: SimpleNamespace(wav=b"RIFF....WAVEfake", wall_ms=400.0, device="cpu", model="piper",
                                        error=""))
    srv = serve(App(Dialog(Scripted(), report_dir=tmp_path / "rep"), voice_mod=voice), 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    clips = tmp_path / "clips"
    clips.mkdir()
    for i in (1, 2, 3):
        (clips / f"ps-01_t{i}_ana.wav").write_bytes(WAV)
    (clips / "notes.txt").write_text("ignored")
    monkeypatch.setattr(turn_time, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["x", "--label", "LAPTOP", "--url", f"http://127.0.0.1:{srv.server_address[1]}",
                                      "--audio", str(clips)])
    assert turn_time.main() == 0
    saved = json.loads(next((tmp_path / "results").glob("turn_time_LAPTOP_*.json")).read_text())
    kinds = [(r["turn"], r["kind"], r["transcript"]) for r in saved["turns"]]
    assert kinds == [(1, "first description", "water leak in the pantry just now"), (2, "fast path", "medium"),
                     (3, "fast path", "submit")]
    first = saved["summary"]["by_kind"]["first description"]
    assert first["stt_ms"]["median"] == 900.0 and first["llm_ms"]["median"] == 1700.0
    assert first["time_to_sound_ms"]["median"] >= first["reply_ms"]["median"]
    srv.shutdown()
