"""
Extraction eval on eval/nlu/cases.jsonl (65 dialogs, 250 turns; rules in eval/nlu/LABELING.md).

  .venv/bin/python eval/run_eval.py --label LAPTOP                     # LLM=llamaserver in env
  .venv/bin/python eval/run_eval.py --label BOARD --split dev
  .venv/bin/python eval/run_eval.py --label ORACLE --backend oracle   # scorer self-check: must be 100%
  .venv/bin/python eval/run_eval.py --label LAPTOP --audio eval/audio  # recordings: typed vs spoken

Two modes (both by default; --mode picks one):
  teacher-forced  each turn starts from its gold `form_before` and `asked`: one LLM call per turn, scored
                  against that turn's gold. Measures extraction and field mapping without errors cascading.
  free-running    each dialog runs through the real dialog loop from an empty form; the final form is
                  scored against `gold_final`. Measures the conversation.

--label is required and goes into every output: a number without its label
(BOARD / LAPTOP / MOCK / ORACLE) never leaves this script. The test split is
locked: it runs only with --split test, and only after the decision rules are
written down (PLAN.md, M4). Run each configuration twice: temperature-0 replies
drift between server launches.

--audio DIR scores recordings named <case>_t<turn>_<speaker>.wav (BUILD_PLAN §4.5): each clip goes
through app/voice.py, then the same teacher-forced path, next to the typed text of the same turns.
Per speaker: how many gold values survive STT (no LLM involved), and tool / field accuracy typed -> spoken.

Scoring (LABELING.md)
  tool            the right tool for the turn
  field           per gold field: enums exact or in accept; text by content words against gold or any
                  accept span (gold words found ≥ 0.8, predicted words in it ≥ 0.5); lists item by item
  extra           a value the gold doesn't have and no accept span covers (after the gate)
  dropped         values the gate removed
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import fastpath, gate, postfix  # noqa: E402
from app.audio import trim_silence  # noqa: E402
from app.backends import LLMReply  # noqa: E402
from app.dialog import Dialog  # noqa: E402
from app.gate import _word_in, content_words  # noqa: E402
from app.prompt import user_message  # noqa: E402

CASES = ROOT / "eval" / "nlu" / "cases.jsonl"
TEXT_RECALL, TEXT_PRECISION = 0.8, 0.5     # DESIGN CHOICE: text-field match, see docstring
ENUMS = ("t", "s", "h")           # h: hazard_status, FORM=15 only


def load_cases(path: Path = CASES, split: str = "dev") -> list[dict]:
    cases = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    if split == "all":
        return cases
    return [c for c in cases if c["split"] in ({"dev", "demo"} if split == "dev" else {split})]


def fit_to_form(cases: list[dict], keys: set[str]) -> list[dict]:
    """Score a set labelled for more fields than the loaded form has (eval/nlu15 with FORM=10): gold keys
    outside the form are dropped, and a turn left with no gold args becomes no_op, as it was labelled in the
    10-field set. A set that fits the form is returned unchanged."""
    def cut(d):
        return {k: v for k, v in (d or {}).items() if k in keys}
    if all(set(t["gold"]["args"]) <= keys for c in cases for t in c["turns"]):
        return cases
    out = []
    for c in cases:
        turns = []
        for t in c["turns"]:
            g = cut(t["gold"]["args"])
            tool = "no_op" if t["gold"]["tool"] == "update_report" and not g else t["gold"]["tool"]
            turns.append({**t, "gold": {"tool": tool, "args": g}, "form_before": cut(t["form_before"])})
        out.append({**c, "turns": turns, "gold_final": cut(c["gold_final"]), "accept": cut(c.get("accept"))})
    return out


# ---- matching -----------------------------------------------------------------
def text_match(pred: str, gold: str) -> bool:
    g, p = content_words(gold), content_words(pred)
    if not g or not p:
        return not g and not p
    rec = sum(_word_in(w, set(p)) for w in g) / len(g)
    prec = sum(_word_in(w, set(g)) for w in p) / len(p)
    return rec >= TEXT_RECALL and prec >= TEXT_PRECISION


def _ok_item(pred: str, gold_items: list[str], accept: list[str]) -> bool:
    return any(text_match(pred, g) for g in gold_items + accept)


def field_match(key: str, pred, gold, accept: list | None = None) -> bool:
    """One gold field. Lists: every gold item found (directly or via an accept
    span for it), and every predicted item is gold or accepted."""
    accept = accept or []
    if pred is None:
        return False
    if key in ENUMS:
        return pred == gold or pred in accept
    if isinstance(gold, list):
        pred = pred if isinstance(pred, list) else [pred]

        def found(g):              # said directly, or in an accepted wording of the same item
            return any(text_match(p, g) or any(text_match(p, a) and _covers(a, g) for a in accept)
                       for p in pred)
        return all(found(g) for g in gold) and all(_ok_item(p, gold, accept) for p in pred)
    return isinstance(pred, str) and (text_match(pred, gold) or any(text_match(pred, a) for a in accept))


def _covers(span: str, gold: str) -> bool:
    """An accept span stands in for a gold item when they share a content word
    (accept spans are alternative wordings of the same thing)."""
    return bool(set(content_words(span)) & set(content_words(gold)))


def extras(pred: dict, gold: dict, accept: dict) -> dict:
    """Predicted values that neither the gold nor an accept span covers."""
    out = {}
    for k, v in pred.items():
        if k in gold:
            if isinstance(v, list):
                bad = [x for x in v if not _ok_item(x, gold[k], accept.get(k, []))]
                if bad:
                    out[k] = bad
            continue
        if k in ENUMS:
            out[k] = v
        else:
            items = v if isinstance(v, list) else [v]
            bad = [x for x in items if not any(text_match(x, a) for a in accept.get(k, []))]
            if bad:
                out[k] = bad if isinstance(v, list) else bad[0]
    return out


def score_args(pred: dict, gold: dict, accept: dict) -> dict:
    return {"fields": {k: {"gold": v, "pred": pred.get(k),
                           "ok": field_match(k, pred.get(k), v, accept.get(k))}
                       for k, v in gold.items()},
            "extra": extras(pred, gold, accept)}


def effective_tool(call: dict, kept: dict) -> str | None:
    """The action the app takes, as app/dialog.py does: an update with nothing left after the gate and the
    post-model fixes is a no_op ("Sorry, I didn't get anything…"). The model's own choice is kept as raw_tool."""
    tool = call.get("tool")
    return "no_op" if tool == "update_report" and not kept else tool


def gated(call: dict, transcript: str, text: str = "", form_has_severity: bool = True,
          form_before: dict | None = None) -> tuple[dict, list]:
    """The gate, the severity fallback and the v4 post-model fixes, exactly as app/dialog.py applies them."""
    kept, dropped = gate.check(dict(call.get("args") or {}), transcript)
    kept = gate.fill_severity(kept, text, form_has_severity)
    return postfix.apply(kept, text, form_before or {}), dropped


# ---- backends -----------------------------------------------------------------
class Oracle:
    """Answers every call with the gold tool call of the turn in its prompt.
    Scorer self-check only: every metric must come out perfect."""
    lane, model_file = "oracle", "oracle"

    def __init__(self, cases):
        self.by_text = {t["user"]: t["gold"] for c in cases for t in c["turns"]}

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None, prefix=None):
        user = prompt.rsplit("\nUser: ", 1)[1]
        g = self.by_text[user]
        call = {"tool": g["tool"], **({"args": g["args"]} if g["args"] else {})}
        text = json.dumps(call, separators=(",", ":"), ensure_ascii=False)
        if prefix is not None and not text.startswith(prefix):
            text = prefix + "}}"            # a pre-filled answer can only be an update; nothing = no_op
        return LLMReply(text=text, backend="oracle", model="oracle")


# ---- pacing and heat (thermal) ---------------------------------------------------
class Throttled:
    """Wraps the LLM backend: waits `pace_s` before each call (a paced run, like a real conversation),
    and when the board's NPU is at or above `hot` °C, waits until it is back at or below `cool` °C
    (or `max_wait_s`) before the call. The board is read through board.board.chip_temperature at most
    every `every_s` seconds. Every pause and reading is recorded in the results (`throttle`).
    EVAL_PACE / EVAL_HEAT_PAUSE="90:80" set it from the environment (so run_bench.py passes it on)."""

    def __init__(self, inner, pace_s: float = 0.0, hot: float | None = None, cool: float | None = None,
                 every_s: float = 10.0, max_wait_s: float = 600.0, read_temp=None, sleep=time.sleep):
        self.inner, self.pace_s, self.hot, self.cool = inner, pace_s, hot, cool
        self.every_s, self.max_wait_s, self.sleep = every_s, max_wait_s, sleep
        self.read_temp = read_temp or _board_temp
        self.lane, self.model_file = getattr(inner, "lane", "?"), getattr(inner, "model_file", "?")
        self.calls = self.pauses = 0
        self.paused_s = 0.0
        self.samples: list[dict] = []
        self._last_read, self._last_npu = -1e9, None

    def _npu(self, force: bool = False):
        now = time.monotonic()
        if force or now - self._last_read >= self.every_s:
            t = self.read_temp() or {}
            self._last_read, self._last_npu = now, t.get("npu_max_c")
            self.samples.append({"t": time.strftime("%H:%M:%S"), "npu_c": t.get("npu_max_c"),
                                 "cpu_c": t.get("cpu_max_c"), "status": t.get("thermal_status_name")})
        return self._last_npu

    def generate(self, prompt, **kw):
        if self.pace_s:
            self.sleep(self.pace_s)
        if self.hot is not None and (npu := self._npu()) is not None and npu >= self.hot:
            self.pauses += 1
            t0 = time.monotonic()
            while (npu := self._npu(force=True)) is not None and npu > self.cool \
                    and time.monotonic() - t0 < self.max_wait_s:
                self.sleep(min(10.0, self.every_s))
            self.paused_s += time.monotonic() - t0
        self.calls += 1
        return self.inner.generate(prompt, **kw)

    def summary(self) -> dict:
        npus = [x["npu_c"] for x in self.samples if x["npu_c"] is not None]
        return {"pace_s": self.pace_s, "heat_pause": [self.hot, self.cool] if self.hot is not None else None,
                "llm_calls": self.calls, "pauses": self.pauses, "paused_s": round(self.paused_s, 1),
                "npu_peak_c": max(npus) if npus else None, "samples": self.samples}


def _board_temp() -> dict:
    try:
        sys.path.insert(0, str(ROOT))
        from board.board import chip_temperature
        return chip_temperature()
    except Exception:                                   # noqa: BLE001 (no board, no adb)
        return {}


def throttle_from(pace: str | None, heat: str | None, backend):
    """A Throttled backend if pacing or a heat pause is asked for (flags first, then EVAL_PACE /
    EVAL_HEAT_PAUSE), else the backend itself."""
    pace = pace if pace is not None else os.environ.get("EVAL_PACE")
    heat = heat if heat is not None else os.environ.get("EVAL_HEAT_PAUSE")
    if not pace and not heat:
        return backend
    hot = cool = None
    if heat:
        hot, cool = (float(x) for x in heat.split(":"))
        if cool >= hot:
            raise SystemExit("--heat-pause HOT:COOL needs COOL below HOT, e.g. 90:80")
    return Throttled(backend, pace_s=float(pace or 0), hot=hot, cool=cool)


# ---- the two modes ------------------------------------------------------------
def teacher_forced(cases, backend, said: dict | None = None, only: set | None = None,
                   fast: bool = True) -> list[dict]:
    """One LLM call per turn from its gold form_before. `said` replaces what the
    user said for some turns ({(case, i): text}, e.g. STT transcripts); `only`
    limits the turns scored. Earlier turns stay as typed (teacher forcing)."""
    out = []
    said = said or {}
    with tempfile.TemporaryDirectory() as tmp:
        d = Dialog(backend, report_dir=Path(tmp))
        for c in cases:
            heard = []
            for t in c["turns"]:
                text = said.get((c["id"], t["i"]), t["user"])
                heard.append(text)
                if only is not None and (c["id"], t["i"]) not in only:
                    continue
                d.reset()
                d.form.apply(t["form_before"])
                # complete = every mandatory LLM field filled (the description is the user's words,
                # which the live dialog always has; this replay doesn't, so it's not counted here)
                state = "review" if not [f for f in d.form.missing() if f.get("key")] else "collecting"
                quick = fastpath.answer(text, t["asked"], state, d.type_values) if fast else None
                if quick is not None:          # answered by code, as the live dialog would
                    reply = LLMReply(text=json.dumps(quick), backend="code", model="fastpath",
                                     prefill_tokens=None, decode_tokens=None)
                else:
                    reply = d.call_llm(user_message(d.form.compact(), t["asked"], text), text)
                call, err = d._parse(reply.text) if reply.status == "ok" else ({"tool": None}, reply.error)
                kept, dropped = gated(call, " ".join(heard), text, bool(t["form_before"].get("s")),
                                      t["form_before"])
                gold = t["gold"]
                tool = effective_tool(call, kept)
                out.append({"case": c["id"], "turn": t["i"], "user": text, "asked": t["asked"],
                            "gold_tool": gold["tool"], "pred_tool": tool, "raw_tool": call.get("tool"),
                            **score_args(kept if tool == "update_report" else {},
                                         gold["args"], c.get("accept", {})),
                            "raw": call, "dropped": dropped, "error": err, "fast_path": quick is not None,
                            "llm": {"prefill_ms": reply.prefill_ms, "decode_ms": reply.decode_ms,
                                    "wall_ms": reply.latency_ms, "decode_tokens": reply.decode_tokens,
                                    "prefill_tokens": reply.prefill_tokens, "parts": reply.parts}})
    return out


def free_running(cases, backend, fast: bool = True) -> list[dict]:
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for c in cases:
            now = datetime.fromisoformat(c["ref_time"])
            d = Dialog(backend, report_dir=Path(tmp), clock=lambda now=now: now, use_fast_path=fast)
            for t in c["turns"]:
                d.turn(t["user"])
            final = d.form.compact()
            res = score_args(final, c["gold_final"], c.get("accept", {}))
            want = c["final_tool"]
            ended = {"submit_report": "submitted", "cancel_report": "cancelled"}.get(want)
            out.append({"case": c["id"], **res, "final_state": d.state, "want_state": ended,
                        "state_ok": d.state == ended if ended else True,
                        "missing": [f["name"] for f in d.form.missing()]})
    return out


# ---- audio: the same turns, spoken ---------------------------------------------
AUDIO_NAME = re.compile(r"^(?P<case>[a-z]+-\d+)_t(?P<turn>\d+)_(?P<speaker>[A-Za-z0-9_-]+)\.wav$")


def find_audio(audio_dir: Path, cases: list[dict]) -> dict:
    """{speaker: {(case, turn index): path}} for files named <case>_t<turn from 1>_<speaker>.wav
    (BUILD_PLAN §4.5) whose turn is in `cases`. Files for other splits are ignored."""
    known = {(c["id"], t["i"]) for c in cases for t in c["turns"]}
    out: dict = {}
    for f in sorted(Path(audio_dir).glob("*.wav")):
        m = AUDIO_NAME.match(f.name)
        if m and (m["case"], int(m["turn"]) - 1) in known:
            out.setdefault(m["speaker"], {})[(m["case"], int(m["turn"]) - 1)] = f
    return out


def survival(cases: list[dict], said: dict) -> dict:
    """Model-free: of the gold text values in these turns (location, time, people,
    equipment, actions, witnesses), how many are still in the transcript? This is
    the damage STT does before the LLM sees anything."""
    total = kept = 0
    lost = []
    for c in cases:
        for t in c["turns"]:
            text = said.get((c["id"], t["i"]))
            if text is None:
                continue
            for k, v in t["gold"]["args"].items():
                if k in ENUMS:
                    continue
                for item in (v if isinstance(v, list) else [v]):
                    total += 1
                    if gate.support(item, text) >= gate.SUPPORT_THRESHOLD:
                        kept += 1
                    else:
                        lost.append({"case": c["id"], "turn": t["i"], "field": k, "value": item, "heard": text})
    return {"values": total, "survived": kept,
            "rate": round(kept / total, 3) if total else None, "lost": lost}


def _acc(rows: list[dict]) -> dict:
    fields = [f["ok"] for r in rows for f in r["fields"].values()]
    return {"turns": len(rows),
            "tool": round(sum(r["gold_tool"] == r["pred_tool"] for r in rows) / len(rows), 3) if rows else None,
            "field": round(sum(fields) / len(fields), 3) if fields else None}


def audio_eval(cases, backend, audio_dir: Path, voice_mod, vocab: str = "", trim: bool = True) -> dict:
    """Per speaker: transcribe each clip, score the transcripts through the same
    teacher-forced path, and set them next to the typed text of the same turns."""
    by_speaker = find_audio(audio_dir, cases)
    if not by_speaker:
        raise SystemExit(f"no recordings for these cases in {audio_dir} "
                         f"(names: <case>_t<turn>_<speaker>.wav, BUILD_PLAN §4.5)")
    all_keys = {k for files in by_speaker.values() for k in files}
    typed = {(r["case"], r["turn"]): r for r in teacher_forced(cases, backend, only=all_keys)}
    speakers = {}
    for spk, files in sorted(by_speaker.items()):
        said, clips = {}, []
        for key, path in sorted(files.items()):
            wav = path.read_bytes()
            trimmed = trim_silence(wav)[0] if trim else wav       # as app/server.py does (STT_TRIM)
            r = voice_mod.transcribe(trimmed, prompt=vocab)
            said[key] = r.text
            clips.append({"case": key[0], "turn": key[1], "file": path.name, "heard": r.text,
                          "stt_wall_ms": r.wall_ms, "stt_ms": r.stt_ms, "device": r.device,
                          "model": r.model, "error": r.error})
        spoken = teacher_forced(cases, backend, said=said, only=set(files))
        speakers[spk] = {
            "clips": len(files), "stt_errors": sum(bool(c["error"]) for c in clips),
            "survival": survival(cases, said),
            "spoken": _acc(spoken), "typed": _acc([typed[k] for k in sorted(files)]),
            "stt_wall_ms": _stat([c["stt_wall_ms"] for c in clips]),
            "stt_model": next((c["model"] for c in clips if c["model"]), None),
            "stt_device": next((c["device"] for c in clips if c["device"]), None),
            "clip_results": clips, "turns": spoken}
    return speakers


def print_audio(res: dict) -> None:
    print(f"\n[{res['label']}] audio · split {res['split']} · LLM {res['model']} on {res['lane']} · {res['laptop_time']}")
    print(f"  {'speaker':<16}{'clips':>6}{'survived':>10}{'tool typed -> spoken':>22}"
          f"{'field typed -> spoken':>23}{'STT median ms':>15}")
    for spk, r in res["audio"].items():
        ty, sp = r["typed"], r["spoken"]
        tool = f"{ty['tool']} -> {sp['tool']}"
        field = f"{ty['field']} -> {sp['field']}"
        med = (r["stt_wall_ms"] or {}).get("median")
        print(f"  {spk:<16}{r['clips']:>6}{str(r['survival']['rate']):>10}{tool:>22}{field:>23}{str(med):>15}")
        for x in r["survival"]["lost"][:5]:
            print(f"     lost {x['case']}#{x['turn']} {x['field']}={x['value']!r} heard {x['heard']!r}")


# ---- summary ------------------------------------------------------------------
def _stat(vals):
    v = [x for x in vals if x is not None]
    return {"mean": round(statistics.mean(v), 1), "median": round(statistics.median(v), 1),
            "n": len(v)} if v else None


def _field_table(rows):
    by = {}
    for r in rows:
        for k, f in r["fields"].items():
            by.setdefault(k, []).append(f["ok"])
    return {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by.items())}


def summarise(tf, fr, label, backend, split) -> dict:
    res = {"label": label, "split": split, "laptop_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "model": getattr(backend, "model_file", "?"), "lane": getattr(backend, "lane", "?")}
    if tf:
        fields = [f["ok"] for r in tf for f in r["fields"].values()]
        res["teacher_forced"] = {
            "turns": len(tf),
            "tool_accuracy": round(sum(r["gold_tool"] == r["pred_tool"] for r in tf) / len(tf), 3),
            "field_accuracy": round(sum(fields) / len(fields), 3) if fields else None,
            "by_field": _field_table(tf),
            "turns_with_extra": sum(bool(r["extra"]) for r in tf),
            "dropped_by_gate": sum(len(r["dropped"]) for r in tf),
            "errors": sum(bool(r["error"]) for r in tf),
            "answered_by_code": sum(r["fast_path"] for r in tf),
            "llm_calls": sum(not r["fast_path"] for r in tf),
            "latency_ms": {k: _stat([r["llm"][k] for r in tf if not r["fast_path"]])
                           for k in ("prefill_ms", "decode_ms", "wall_ms")},
            "tokens": {k: _stat([r["llm"][k] for r in tf if not r["fast_path"]])
                       for k in ("prefill_tokens", "decode_tokens")},
        }
    if fr:
        fields = [f["ok"] for r in fr for f in r["fields"].values()]
        res["free_running"] = {
            "dialogs": len(fr),
            "final_field_accuracy": round(sum(fields) / len(fields), 3) if fields else None,
            "by_field": _field_table(fr),
            "dialogs_exact": sum(all(f["ok"] for f in r["fields"].values()) and not r["extra"]
                                 and r["state_ok"] for r in fr),
            "ended_right": f"{sum(r['state_ok'] for r in fr)}/{len(fr)}",
            "dialogs_with_extra": sum(bool(r["extra"]) for r in fr),
        }
    res["turns"] = tf
    res["dialogs"] = fr
    return res


def print_summary(res: dict) -> None:
    print(f"\n[{res['label']}] split {res['split']} · model {res['model']} on {res['lane']} · {res['laptop_time']}")
    if "teacher_forced" in res:
        t = res["teacher_forced"]
        print(f"  teacher-forced  {t['turns']} turns · tool {t['tool_accuracy']} · field {t['field_accuracy']}"
              f" · turns with extra {t['turns_with_extra']} · dropped by gate {t['dropped_by_gate']}"
              f" · errors {t['errors']}")
        print(f"                  answered by code (fast path) {t['answered_by_code']} · LLM calls {t['llm_calls']}")
        print(f"                  by field {t['by_field']}")
        for k, v in t["latency_ms"].items():
            print(f"                  {k:<11} {v}")
        print(f"                  tokens {t['tokens']}")
        bad = [r for r in res["turns"] if r["gold_tool"] != r["pred_tool"]
               or not all(f["ok"] for f in r["fields"].values()) or r["extra"]]
        for r in bad[:20]:
            wrong = {k: f["pred"] for k, f in r["fields"].items() if not f["ok"]}
            print(f"   ✗ {r['case']}#{r['turn']} tool {r['pred_tool']} (want {r['gold_tool']}) "
                  f"wrong {wrong} extra {r['extra']}")
    if "free_running" in res:
        f = res["free_running"]
        print(f"  free-running    {f['dialogs']} dialogs · final field {f['final_field_accuracy']}"
              f" · exact {f['dialogs_exact']} · ended right {f['ended_right']}"
              f" · with extra {f['dialogs_with_extra']}")
        print(f"                  by field {f['by_field']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["BOARD", "LAPTOP", "MOCK", "ORACLE"])
    ap.add_argument("--backend", choices=["env", "oracle"], default="env",
                    help="env: LLM / LLM_URL from the environment (see app/server.py)")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev",
                    help="dev includes the demo case; test is locked until the M4 rules exist")
    ap.add_argument("--mode", choices=["both", "teacher", "free"], default="both")
    ap.add_argument("--audio", metavar="DIR", help="score recordings in DIR (BUILD_PLAN §4.5) instead "
                    "of typed text; STT through app/voice.py (STT_URL)")
    ap.add_argument("--no-vocab", action="store_true", help="--audio: don't pass data/vocab.txt to STT")
    ap.add_argument("--no-fast-path", action="store_true", help="send every turn to the LLM (for comparison)")
    ap.add_argument("--no-trim", action="store_true", help="--audio: don't cut silence before STT")
    ap.add_argument("--pace", help="seconds to wait before each LLM call (a paced run; env EVAL_PACE)")
    ap.add_argument("--heat-pause", help="HOT:COOL °C, e.g. 90:80: pause when the NPU reaches HOT until it "
                    "is back to COOL (reads the board over adb; env EVAL_HEAT_PAUSE)")
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--out", default=str(ROOT / "results"))
    a = ap.parse_args()
    from app.form import form_version, keyed_fields, load_schema
    cases = fit_to_form(load_cases(Path(a.cases), a.split), {f["key"] for f in keyed_fields(load_schema())})
    if a.backend == "oracle":
        backend = Oracle(cases)
    else:
        from app.server import make_backend
        backend = make_backend()
        if os.environ.get("LLM", "mock") == "mock" and a.label != "MOCK":
            raise SystemExit("LLM=mock: use --label MOCK")
    backend = throttle_from(a.pace, a.heat_pause, backend)
    if a.audio:
        from app import voice
        vocab_file = ROOT / "data" / "vocab.txt"
        vocab = "" if a.no_vocab or not vocab_file.exists() else vocab_file.read_text()
        res = summarise([], [], a.label, backend, a.split)
        res["audio"] = audio_eval(cases, backend, Path(a.audio), voice, vocab, trim=not a.no_trim)
        res["trim"] = not a.no_trim
        res["vocab_prompt"] = bool(vocab)
        print_audio(res)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"eval_audio_{a.label}_{a.split}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(res, indent=1, ensure_ascii=False))
        print(f"  saved {path}")
        return 0
    fast = not a.no_fast_path
    tf = teacher_forced(cases, backend, fast=fast) if a.mode in ("both", "teacher") else []
    fr = free_running(cases, backend, fast=fast) if a.mode in ("both", "free") else []
    res = summarise(tf, fr, a.label, backend, a.split)
    res["fast_path"] = fast
    res["postfix"] = postfix.enabled()
    res["form"] = form_version()
    res["cases"] = str(a.cases)
    from app.prompt import prompt_version
    res["prompt"] = prompt_version()
    res["answer"] = Dialog(backend).answer
    res["prefill"] = Dialog(backend).prefill
    b = backend                                           # the v7 type router, possibly inside Throttled
    while b is not None:
        if hasattr(b, "overrides") and hasattr(b, "type_backend"):
            res["router"] = b.summary()
            r = res["router"]
            print(f"  type router: {r['type_model']} · {r['calls']} calls · type changed {r['changed']} · "
                  f"failures {r['failures']}")
            break
        b = getattr(b, "inner", None)
    if isinstance(backend, Throttled):
        res["throttle"] = backend.summary()
        t = res["throttle"]
        print(f"  throttle: pace {t['pace_s']} s · heat pause {t['heat_pause']} · {t['pauses']} pauses, "
              f"{t['paused_s']} s paused · NPU peak {t['npu_peak_c']} °C")
    res["constraint"] = Dialog(backend).constraint
    print_summary(res)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"eval_{a.label}_{a.split}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"  saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
