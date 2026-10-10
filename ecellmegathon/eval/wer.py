"""
Word error rate and latency of the STT engine on our own recordings (Track B, judging criterion E3).

Reads the turns in eval/cases.jsonl (Track A writes them; the reference text is each turn's `say`)
and the recordings in eval/audio/<case_id>_t<turn>_<speaker>.wav (16 kHz mono 16-bit; turn
numbers start at 1). Sends each file through app.voice.transcribe, exactly as the dialog does.

  .venv/Scripts/python eval/wer.py --label LAPTOP --subset
  .venv/Scripts/python eval/wer.py --label BOARD --subset --vocab data/vocab.txt --repeat 2
  .venv/Scripts/python eval/wer.py --label LAPTOP --subset --sweep tiny.en,base.en,small.en \
        --whisper-bin <whisper-server> --models-dir <folder with ggml-*.bin> --vocab data/vocab.txt

--label is required (BOARD or LAPTOP): a number without one is not reported. The STT engine and
model come from STT_URL / STT_DEVICE / STT_MODEL, as for the app. Results go to
results/wer_<label>_<model>_<laptop time>.json (results/ is gitignored).

--subset   only the lines of eval/speech_subset.jsonl (BUILD_PLAN §4.5), with their `why` tags
           and noisy flag. Speakers ending in `-noisy` are the noisy pass.
--sweep    LAPTOP only: starts a local whisper-server per model (port 8092), runs each model
           without and then with the --vocab prompt, and prints one results table. The decision
           rule for this table is in eval/STT_DECISION.md, written before any run.
--note     free text stored with the results (e.g. "SYNTHETIC audio, not human speech").

Normalisation before scoring (the same for reference and hypothesis): lowercase, clock times
joined ("11:30", "11.30" -> "1130", as Whisper often writes them), ordinals to numbers ("2nd",
"second" -> "2"; so the time unit "second" also becomes "2"), "a.m."/"p.m." -> "am"/"pm", digits
split from letters ("10am" -> "10 am"), number words zero..twenty and thirty..ninety -> digits
("ten" -> "10"), punctuation dropped except apostrophes inside words.
Compound numbers are not joined ("twenty five" -> "20 5" vs "25"): that counts as an error.

Key-word error rate: the fraction of reference key words that the alignment substitutes or
deletes. Key words are numbers and times (digits, am/pm, quarter, half, o'clock), names (capitalised
words in the turn's gold persons/witnesses), and meaning-flip words (not, no, actually, sorry, wait,
instead, correction). These break extraction even when overall WER looks fine.

Stdlib only.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import voice  # noqa: E402

AUDIO_NAME = re.compile(r"^(?P<case>[A-Za-z0-9-]+)_t(?P<turn>\d+)_(?P<speaker>[A-Za-z0-9-]+)\.wav$")
SUBSET = ROOT / "eval" / "speech_subset.jsonl"

_NUM_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_NUM_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
                   "eighty": "80", "ninety": "90"})
TIME_WORDS = {"am", "pm", "quarter", "half", "o'clock", "oclock"}
FLIP_WORDS = {"not", "no", "actually", "sorry", "wait", "instead", "correction"}
_NOT_NAMES = {"the", "our", "my", "a", "an", "mr", "mrs", "ms", "dr", "sir", "madam", "i"}
KEY_KINDS = ("number", "name", "flip")


_ORDINALS = {w: str(i) for i, w in enumerate(
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth".split())}


def normalise(text: str) -> list[str]:
    t = text.lower()
    t = re.sub(r"\b(\d{1,2})[:.](\d{2})(?!\d)", r"\1\2", t)    # 11:30, 11.30 and 1130 are one time
    t = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1", t)          # 2nd floor == second floor
    t = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", t)
    t = re.sub(r"(\d)([a-z])", r"\1 \2", t)
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)
    t = re.sub(r"(?<![a-z0-9])'|'(?![a-z0-9])", " ", t)
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return [_NUM_WORDS.get(w, _ORDINALS.get(w, w)) for w in t.split()]


def align(ref: list[str], hyp: list[str]) -> list[tuple[str, int | None, int | None]]:
    """Minimum-edit word alignment: a list of (op, ref index, hyp index), op one of
    ok / sub / del / ins."""
    rows, cols = len(ref) + 1, len(hyp) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(1, rows):
        cost[i][0] = i
    for j in range(1, cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            same = ref[i - 1] == hyp[j - 1]
            cost[i][j] = min(cost[i - 1][j - 1] + (0 if same else 1),
                             cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    ops, i, j = [], len(ref), len(hyp)
    while i or j:
        if i and j and cost[i][j] == cost[i - 1][j - 1] + (0 if ref[i - 1] == hyp[j - 1] else 1):
            ops.append(("ok" if ref[i - 1] == hyp[j - 1] else "sub", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and cost[i][j] == cost[i - 1][j] + 1:
            ops.append(("del", i - 1, None))
            i -= 1
        else:
            ops.append(("ins", None, j - 1))
            j -= 1
    return ops[::-1]


def edit_counts(ref: list[str], hyp: list[str]) -> dict:
    """Substitutions, deletions and insertions of the minimum-edit alignment."""
    ops = [op for op, _, _ in align(ref, hyp)]
    return {"sub": ops.count("sub"), "del": ops.count("del"), "ins": ops.count("ins"),
            "ref_words": len(ref)}


def wer(counts: dict) -> float | None:
    n = counts["ref_words"]
    return None if n == 0 else (counts["sub"] + counts["del"] + counts["ins"]) / n


def names_in(gold: dict) -> set[str]:
    """Lowercased name words from a turn's gold persons (p) and witnesses (w)."""
    out = set()
    for item in list(gold.get("p", [])) + list(gold.get("w", [])):
        for w in re.findall(r"\b[A-Z][a-z]+\b", item):
            if w.lower() not in _NOT_NAMES:
                out.add(w.lower())
    return out


def key_kinds(word: str, names: set[str]) -> list[str]:
    kinds = []
    if word.isdigit() or word in TIME_WORDS:
        kinds.append("number")
    if word in names:
        kinds.append("name")
    if word in FLIP_WORDS:
        kinds.append("flip")
    return kinds


def keyword_errors(ref: list[str], hyp: list[str], names: set[str]) -> dict:
    """Per key-word kind: how many reference key words, and how many were substituted or deleted."""
    out = {k: {"n": 0, "err": 0} for k in KEY_KINDS}
    for op, ri, _ in align(ref, hyp):
        if ri is None:
            continue
        for k in key_kinds(ref[ri], names):
            out[k]["n"] += 1
            out[k]["err"] += op in ("sub", "del")
    return out


def load_turns(cases_file: Path) -> dict[tuple[str, int], dict]:
    """(case, turn from 1) -> {"say", "gold"}."""
    turns = {}
    for line in cases_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        for n, turn in enumerate(case.get("turns", []), start=1):
            if turn.get("say"):
                turns[(case["id"], n)] = {"say": turn["say"], "gold": turn.get("gold") or {}}
    return turns


def load_references(cases_file: Path) -> dict[tuple[str, int], str]:
    return {k: v["say"] for k, v in load_turns(cases_file).items()}


def load_subset(path: Path = SUBSET) -> dict[tuple[str, int], dict]:
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return {(r["case"], int(r["turn"])): r for r in rows}


def wav_seconds(audio: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


def summary(sel: list[dict]) -> dict:
    ok = [r for r in sel if not r["error"]]
    tot = {k: sum(r[k] for r in ok) for k in ("sub", "del", "ins", "ref_words")}
    walls = [r["wall_ms"] for r in ok]
    per8 = [r["wall_ms"] / r["seconds"] * 8 for r in ok if r.get("seconds")]
    kw = {k: {"n": sum(r["keywords"][k]["n"] for r in ok),
              "err": sum(r["keywords"][k]["err"] for r in ok)} for k in KEY_KINDS}
    kw_all = {"n": sum(v["n"] for v in kw.values()), "err": sum(v["err"] for v in kw.values())}
    rate = lambda v: None if not v["n"] else round(v["err"] / v["n"], 4)  # noqa: E731
    return {"files": len({r["file"] for r in sel}), "calls": len(sel),
            "errors": len(sel) - len(ok), **tot,
            "wer": None if not tot["ref_words"] else round(wer(tot), 4),
            "keyword_error": rate(kw_all), "keywords": {k: {**v, "rate": rate(v)} for k, v in kw.items()},
            "wall_ms_median": round(statistics.median(walls), 1) if walls else None,
            "wall_ms_p90": pct(walls, 0.9),
            "ms_per_8s_median": round(statistics.median(per8), 1) if per8 else None}


def run(cases_file: Path, audio_dir: Path, label: str, vocab: str, repeat: int,
        subset: dict | None = None, note: str = "") -> dict:
    turns = load_turns(cases_file)
    rows, skipped = [], []
    for wav_path in sorted(audio_dir.glob("*.wav")):
        m = AUDIO_NAME.match(wav_path.name)
        if not m:
            skipped.append(f"{wav_path.name}: name is not <case>_t<turn>_<speaker>.wav")
            continue
        key = (m["case"], int(m["turn"]))
        if key not in turns:
            skipped.append(f"{wav_path.name}: no turn {key[1]} in case {key[0]}")
            continue
        if subset is not None and key not in subset:
            skipped.append(f"{wav_path.name}: not in the speech subset")
            continue
        audio = wav_path.read_bytes()
        ref_words, names = normalise(turns[key]["say"]), names_in(turns[key]["gold"])
        meta = subset.get(key, {}) if subset is not None else {}
        for attempt in range(1, repeat + 1):
            r = voice.transcribe(audio, prompt=vocab)
            hyp_words = normalise(r.text)
            c = edit_counts(ref_words, hyp_words)
            rows.append({"file": wav_path.name, "case": key[0], "turn": key[1],
                         "speaker": m["speaker"], "noisy": m["speaker"].endswith("-noisy"),
                         "why": meta.get("why", []), "attempt": attempt, "ref": turns[key]["say"],
                         "hyp": r.text, **c, "wer": wer(c),
                         "keywords": keyword_errors(ref_words, hyp_words, names),
                         "seconds": wav_seconds(audio), "wall_ms": r.wall_ms,
                         "stt_ms": r.stt_ms, "device": r.device, "model": r.model, "error": r.error})

    speakers = sorted({r["speaker"] for r in rows})
    whys = sorted({w for r in rows for w in r["why"]})
    return {"label": label, "note": note, "laptop_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "stt_url": voice._cfg("STT_URL", "http://127.0.0.1:8082"),
            "model": rows[0]["model"] if rows else voice._cfg("STT_MODEL", "ggml-base.en.bin"),
            "device": rows[0]["device"] if rows else voice._cfg("STT_DEVICE", "laptop"),
            "vocab_prompt": bool(vocab), "repeat": repeat, "subset": subset is not None,
            "overall": summary(rows),
            "by_condition": {c: summary([r for r in rows if r["noisy"] == (c == "noisy")])
                             for c in ("quiet", "noisy")},
            "by_speaker": {s: summary([r for r in rows if r["speaker"] == s]) for s in speakers},
            "by_why": {w: summary([r for r in rows if w in r["why"]]) for w in whys},
            "skipped": skipped, "rows": rows}


# ---- the model sweep (LAPTOP) ---------------------------------------------------
SWEEP_PORT = 8092


def _health(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
            return json.loads(r.read().decode()).get("status") == "ok"
    except Exception:
        return False


def sweep(models: list[str], whisper_bin: str, models_dir: Path, cases: Path, audio: Path,
          vocab: str, repeat: int, subset: dict | None, note: str, threads: int = 6) -> list[dict]:
    """Each model, without and with the vocab prompt, on a local whisper-server. LAPTOP only."""
    results = []
    url = f"http://127.0.0.1:{SWEEP_PORT}"
    for m in models:
        model_file = models_dir / f"ggml-{m}.bin"
        if not model_file.is_file():
            raise SystemExit(f"missing model: {model_file}")
        proc = subprocess.Popen([whisper_bin, "-m", str(model_file), "--host", "127.0.0.1",
                                 "--port", str(SWEEP_PORT), "-t", str(threads)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            t0 = time.time()
            while not _health(url):
                if proc.poll() is not None or time.time() - t0 > 120:
                    raise SystemExit(f"whisper-server did not start for {m}")
                time.sleep(0.5)
            os.environ.update({"STT_URL": url, "STT_DEVICE": "laptop", "STT_MODEL": model_file.name})
            for prompt in ([""] + ([vocab] if vocab else [])):
                res = run(cases, audio, "LAPTOP", prompt, repeat, subset, note)
                results.append(res)
                o = res["overall"]
                print(f"  {model_file.name:22s} vocab={'on ' if prompt else 'off'} WER {o['wer']} "
                      f"key-word err {o['keyword_error']} ({o['files']} files)", flush=True)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    return results


def fmt(v, pct_=False) -> str:
    if v is None:
        return "–"
    return f"{100 * v:.1f}%" if pct_ else f"{v:,.0f}"


def table(results: list[dict]) -> str:
    """One markdown table, one row per (model, vocab) run."""
    head = ("| model | vocab | files | WER | WER quiet | WER noisy | key-word err | numbers | names | flips "
            "| STT ms median | ms per 8 s clip |")
    lines = [head, "|" + "---|" * 12]
    for r in results:
        o, q, n = r["overall"], r["by_condition"]["quiet"], r["by_condition"]["noisy"]
        k = o["keywords"]
        lines.append(f"| {Path(r['model']).stem.removeprefix('ggml-')} | {'on' if r['vocab_prompt'] else 'off'} "
                     f"| {o['files']} | {fmt(o['wer'], 1)} | {fmt(q['wer'], 1)} | {fmt(n['wer'], 1)} "
                     f"| {fmt(o['keyword_error'], 1)} | {fmt(k['number']['rate'], 1)} "
                     f"| {fmt(k['name']['rate'], 1)} | {fmt(k['flip']['rate'], 1)} "
                     f"| {fmt(o['wall_ms_median'])} | {fmt(o['ms_per_8s_median'])} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="STT word error rate on eval/audio")
    ap.add_argument("--label", required=True, choices=["BOARD", "LAPTOP"])
    ap.add_argument("--cases", default=str(ROOT / "eval" / "cases.jsonl"))
    ap.add_argument("--audio", default=str(ROOT / "eval" / "audio"))
    ap.add_argument("--vocab", default="", help="file passed to Whisper as the prompt")
    ap.add_argument("--repeat", type=int, default=1, help="calls per file")
    ap.add_argument("--subset", action="store_true", help="only eval/speech_subset.jsonl lines")
    ap.add_argument("--sweep", default="", help="LAPTOP: comma-separated models, e.g. tiny.en,base.en")
    ap.add_argument("--whisper-bin", default="", help="--sweep: path to whisper-server")
    ap.add_argument("--models-dir", default="", help="--sweep: folder holding ggml-<model>.bin")
    ap.add_argument("--note", default="", help="stored with the results")
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args(argv)
    cases, audio = Path(a.cases), Path(a.audio)
    if not cases.is_file():
        print(f"no cases file: {cases}", file=sys.stderr)
        return 2
    if not audio.is_dir():
        print(f"no audio folder: {audio}", file=sys.stderr)
        return 2
    vocab = Path(a.vocab).read_text(encoding="utf-8") if a.vocab else ""
    subset = load_subset() if a.subset else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    if a.sweep:
        if a.label != "LAPTOP":
            print("--sweep starts local servers: LAPTOP only", file=sys.stderr)
            return 2
        if not (a.whisper_bin and a.models_dir):
            print("--sweep needs --whisper-bin and --models-dir", file=sys.stderr)
            return 2
        results = sweep([m.strip() for m in a.sweep.split(",") if m.strip()], a.whisper_bin,
                        Path(a.models_dir), cases, audio, vocab, a.repeat, subset, a.note)
        path = out / f"wer_sweep_LAPTOP_{stamp}.json"
        path.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"\n[LAPTOP] {time.strftime('%Y-%m-%d %H:%M %z')}"
              + (f"  NOTE: {a.note}" if a.note else ""))
        print(table(results))
        print(f"saved {path}")
        return 0

    res = run(cases, audio, a.label, vocab, a.repeat, subset, a.note)
    path = out / f"wer_{a.label}_{Path(res['model']).stem}_{stamp}.json"
    path.write_text(json.dumps(res, indent=1), encoding="utf-8")
    o = res["overall"]
    print(f"[{a.label}] {res['model']} on {res['device']}"
          + (f"  NOTE: {a.note}" if a.note else ""))
    print(f"  WER {o['wer']} over {o['ref_words']} words, key-word error {o['keyword_error']}, "
          f"{o['files']} files, {o['errors']} errors; wall median {o['wall_ms_median']} ms, "
          f"p90 {o['wall_ms_p90']} ms, {o['ms_per_8s_median']} ms per 8 s clip")
    for group in ("by_condition", "by_speaker", "by_why"):
        for s, v in res[group].items():
            if v["calls"]:
                print(f"  {group[3:]:9s} {s:16s} WER {v['wer']}  key-word {v['keyword_error']}  "
                      f"({v['files']} files)")
    for s in res["skipped"]:
        print(f"  skipped {s}")
    print(table([res]))
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
