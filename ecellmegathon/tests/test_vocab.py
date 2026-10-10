"""data/vocab.txt (Whisper's prompt) may only hold words from the dev/demo split: tuning STT on the locked
test split would make its numbers meaningless (BUILD_PLAN §4.5)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _text(splits):
    cases = [json.loads(ln) for ln in (ROOT / "eval" / "nlu" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    return " ".join(t["user"] for c in cases if c["split"] in splits for t in c["turns"]).lower()


def test_every_vocab_entry_is_said_in_the_dev_split():
    dev = _text({"dev", "demo"})
    entries = [v.strip() for v in (ROOT / "data" / "vocab.txt").read_text(encoding="utf-8").splitlines() if v.strip()]
    assert entries
    assert [v for v in entries if v.lower() not in dev] == []


def test_vocab_fits_whisper_prompt():
    # whisper.cpp keeps at most n_text_ctx/2 = 224 prompt tokens; ~3.5 characters per token for this text.
    text = " ".join((ROOT / "data" / "vocab.txt").read_text(encoding="utf-8").split())
    assert len(text) / 3.5 < 200
