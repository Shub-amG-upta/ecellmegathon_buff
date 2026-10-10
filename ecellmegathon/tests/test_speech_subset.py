"""eval/speech_subset.py: the recording list is deterministic, dev-only, and
matches the turns eval/wer.py reads (BUILD_PLAN §4.3)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

import speech_subset  # noqa: E402


def test_subset_is_deterministic_and_matches_the_committed_file():
    rows = speech_subset.build()
    committed = [json.loads(ln) for ln in (ROOT / "eval" / "speech_subset.jsonl").read_text().splitlines()]
    assert rows == committed and 30 <= len(rows) <= 45
    assert sum(r["noisy"] for r in rows) == speech_subset.NOISY


def test_every_line_is_a_dev_turn_with_the_same_text_and_1_based_turn_number():
    split = {}
    refs = {}
    for line in (ROOT / "eval" / "cases.jsonl").read_text().splitlines():
        c = json.loads(line)
        split[c["id"]] = c["split"]
        for n, t in enumerate(c["turns"], start=1):          # the numbering eval/wer.py uses
            refs[(c["id"], n)] = t["say"]
    for r in speech_subset.build():
        assert split[r["case"]] in ("dev", "demo")
        assert refs[(r["case"], r["turn"])] == r["say"]
    texts = [r["say"].lower() for r in speech_subset.build()]
    assert len(texts) == len(set(texts))
    assert {w for r in speech_subset.build() for w in r["why"]} >= {"demo", "number", "name", "accent",
                                                                    "flip", "short", "long"}


def test_recording_script_lists_exactly_the_subset():
    doc = (ROOT / "eval" / "RECORDING.md").read_text()
    rows = [ln for ln in doc.splitlines() if ln.startswith("| ") and "_t" in ln and "`" in ln]
    assert len(rows) == len(speech_subset.build())
    for r in speech_subset.build():
        assert f"`{r['case']}_t{r['turn']}`" in doc and r["say"].replace("|", "\\|") in doc
