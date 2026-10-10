"""eval/run_eval.py on eval/nlu/cases.jsonl: the scorer gives the gold answers 100%
in both modes, scores wrong answers lower, and keeps the test split out by default."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from run_eval import Oracle, field_match, free_running, load_cases, summarise, teacher_forced  # noqa: E402

ALL = load_cases(split="all")


def test_gold_answers_score_100_in_both_modes():
    tf, fr = teacher_forced(ALL, Oracle(ALL)), free_running(ALL, Oracle(ALL))
    s = summarise(tf, fr, "ORACLE", Oracle(ALL), "all")
    t, f = s["teacher_forced"], s["free_running"]
    assert t["turns"] == 250 and t["tool_accuracy"] == 1.0 and t["field_accuracy"] == 1.0
    assert t["turns_with_extra"] == 0 and t["dropped_by_gate"] == 0   # the gate keeps every gold value
    assert f["dialogs"] == 65 and f["final_field_accuracy"] == 1.0 and f["dialogs_exact"] == 65


def test_wrong_answers_score_lower():
    class Wrong(Oracle):
        def generate(self, *a, **kw):
            r = super().generate(*a, **kw)
            call = json.loads(r.text)
            args = call.get("args", {})
            if args.get("t") == "fire":
                args["t"] = "electrical"                      # wrong enum
            if "s" in args:
                args["w"] = ["Mr Nobody"]                     # invented witness → dropped by the gate
            if "l" in args:
                args["l"] = args["l"] + " and the whole building"   # padded text → no longer matches
            r.text = json.dumps(call)
            return r
    tf = teacher_forced(ALL, Wrong(ALL))
    s = summarise(tf, [], "ORACLE", Wrong(ALL), "all")["teacher_forced"]
    assert s["field_accuracy"] < 1.0 and s["dropped_by_gate"] > 0
    assert s["by_field"]["t"] != "64/64" and s["by_field"]["l"] != "66/66"


def test_matching_rules():
    assert field_match("l", "near Conference Room 2", "near Conference Room 2")
    assert field_match("l", "Conference Room 2", "near Conference Room 2", ["Conference Room 2"])
    assert not field_match("l", "Conference Room 3", "near Conference Room 2")
    assert field_match("a", ["area isolated"], ["isolated the area"], ["area isolated"])
    assert field_match("w", ["Karthik", "Priya"], ["Priya", "Karthik"])
    assert not field_match("w", ["Priya"], ["Priya", "Karthik"])                 # one missing
    assert not field_match("w", ["Priya", "Karthik", "Ravi"], ["Priya", "Karthik"])  # one invented
    assert field_match("t", "fire", "electrical", ["fire"])                    # accepted alternative
    assert not field_match("s", None, "low")


def test_test_split_is_locked_by_default():
    dev = load_cases()
    assert {c["split"] for c in dev} == {"dev", "demo"}
    assert not any(c["split"] == "test" for c in dev)
    assert len(load_cases(split="test")) == 21


# ---- --audio: recordings scored typed vs spoken -----------------------------------
import re  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app.backends import LLMReply  # noqa: E402
from run_eval import audio_eval, find_audio  # noqa: E402


@dataclass
class _Stt:
    text: str
    wall_ms: float = 500.0
    stt_ms: float | None = None
    device: str = "laptop"
    model: str = "fake"
    error: str = ""


# the fake "WAV" holds its own transcript, so a test controls exactly what STT "heard"
FAKE_VOICE = SimpleNamespace(transcribe=lambda wav, prompt="": _Stt(wav.decode()))


class _Lenient(Oracle):
    """Gold for text it knows; anything else (a garbled transcript) gets no_op."""
    def generate(self, prompt, **kw):
        user = prompt.rsplit("\nUser: ", 1)[1]
        if user not in self.by_text:
            return LLMReply(text='{"tool":"no_op"}', backend="oracle", model="oracle")
        return super().generate(prompt, **kw)



def _record(tmp_path, speaker, garble=False):
    subset = [json.loads(ln) for ln in (ROOT / "eval" / "speech_subset.jsonl").read_text().splitlines()]
    for r in subset:
        text = r["say"]
        if garble:   # what a bad STT does: numbers and names come out wrong
            text = re.sub(r"\d", "7", text).replace("Mahesh", "Mukesh").replace("Priya", "Prima")
        (tmp_path / f"{r['case']}_t{r['turn']}_{speaker}.wav").write_bytes(text.encode())
    return len(subset)


def test_audio_files_are_matched_to_turns_and_other_splits_ignored(tmp_path):
    n = _record(tmp_path, "shubham")
    (tmp_path / "wit-07_t1_shubham.wav").write_bytes(b"test split: must be ignored")
    (tmp_path / "notes.wav").write_bytes(b"not a turn")
    found = find_audio(tmp_path, load_cases())                  # dev split
    assert list(found) == ["shubham"] and len(found["shubham"]) == n


def test_perfect_stt_scores_like_typed_and_bad_stt_shows_its_damage(tmp_path):
    _record(tmp_path, "clear")
    _record(tmp_path, "noisy", garble=True)
    dev = load_cases()
    res = audio_eval(dev, _Lenient(dev), tmp_path, FAKE_VOICE)
    clear, noisy = res["clear"], res["noisy"]
    assert clear["survival"]["rate"] == 1.0
    assert clear["spoken"] == clear["typed"] and clear["typed"]["field"] == 1.0
    assert noisy["survival"]["rate"] < 1.0                     # STT damage, measured without the LLM
    assert noisy["spoken"]["field"] < noisy["typed"]["field"]
    assert any(x["value"] == "The guard Mahesh" for x in noisy["survival"]["lost"])
