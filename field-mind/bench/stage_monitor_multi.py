#!/usr/bin/env python3
"""
=============================================================================
 STAGE MONITOR  --  live view of a benchmark run (multi-agent or single agent)
=============================================================================

Shown full-screen by scripts/benchmark.sh (bench/benchmark.py) in the same
terminal as the run. On its own, it views a tick file another run is writing:

  python bench/stage_monitor_multi.py                    # newest run in results/benchmarks
  python bench/stage_monitor_multi.py --dir results/benchmarks/<test> --episode ep_A01_fcv_seize
  python bench/stage_monitor_multi.py --once             # print one frame and exit

It changes nothing in the run: it reads the tick file the run appends to on
every tick (run_demo.py --live-ticks) and asks each lane's llama-server whether
it is working right now (GET /slots, about once a second; --no-poll: never).

It shows, live:

  * the compute units and what each is doing now: the tick code on the LAPTOP,
    the NPU lane and (multi-agent) the CPU lane on the board;
  * every tick: state, triage, the time of the tick's code path, one cell per
    model job with its run time and its INPUT tokens (the prompt tokens the
    server prefilled, repair call included) and whether its answer was used;
  * for every tick that publishes a ranking: whether the published rank-1
    cause is right (exact case, same look-alike group, or wrong), whether
    belief alone (the code-only ranking) was right, and the shown confidence
    of the published rank-1;
  * running totals: published group top-1 and belief-alone group top-1 so far,
    computed by bench/evaluator.t2_root_cause itself (so they are the numbers
    the summary will report), input and answer tokens, unusable answers.

WHAT IS LIVE AND WHAT IS NOT. A lane's busy / idle comes from the board and is
live. WHICH job it is working on comes from the tick file. In real time the
file says a job was sent at its tick and, at the tick its answer is folded, how
long it ran; "running" names the oldest job sent to that lane with no answer
yet. In lockstep (back to back) a tick's line reaches the file only when all
its model answers are in: cells then show each call's real time (envelope
latency_ms; the scheduler's job times are a simulated clock in that mode).
Single agent: the diagnostician and verifier run one after the other on the
NPU lane; the "code" column is the tick's time minus its model calls.
Laptop times of the tick code are not board times.
=============================================================================
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import statistics
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BENCH_DIR = ROOT / "results/benchmarks"        # bench/benchmark.py: one folder per test
COLS = {"multi": ("diag_water", "diag_heat", "verifier", "text_reader"),
        "single": ("diagnostician", "verifier")}
LABEL = {"diag_water": "water diagnosis", "diag_heat": "heat diagnosis",
         "verifier": "verifier", "text_reader": "note reader",
         "diagnostician": "diagnosis"}
HEAD = {"diag_water": "water·NPU", "diag_heat": "heat·NPU", "verifier": "verify·CPU",
        "text_reader": "notes·CPU", "diagnostician": "diagnose·NPU"}
HEAD_SINGLE = {"verifier": "verify·NPU"}
CELL_W = 14
HARD_LIMIT_MS = 200          # CITED: CLAUDE.md, the hard path's limit per tick
TICK_S_AGENT = 30.0          # CITED: configs/base.yaml agent.tick_period_s (evaluator uses 30)
ANSI = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "yellow": "\033[33m",
        "green": "\033[32m", "magenta": "\033[35m", "red": "\033[31m", "cyan": "\033[36m"}


def _c(text: str, *styles: str) -> str:
    return "".join(ANSI[s] for s in styles) + text + ANSI["reset"] if styles else text


def _clock(s: float) -> str:
    s = int(max(0, s))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _tok(env: dict) -> tuple[int | None, int | None]:
    """(input, answer) tokens of one envelope: the server's own counts, every
    call of the job (repair included). None when the server did not report."""
    t = env.get("tokens") or {}
    return t.get("prefill"), t.get("decode")


# ===========================================================================
#  Ground truth -> per-tick accuracy
# ===========================================================================
def load_truth(episode_dir: Path) -> dict | None:
    """What a tick's ranking is scored against, read the way the evaluator
    reads it (bench/evaluator.t2_root_cause, data/kb/case_groups.json)."""
    gt_path = Path(episode_dir) / "ground_truth.json"
    if not gt_path.exists():
        return None
    from bench.evaluator import _groups
    gt = json.loads(gt_path.read_text())
    target = gt.get("root_cause_id")
    out = {"gt": gt, "target": target, "family": gt.get("family"),
           "onset": gt.get("fault_onset_t") or 0, "scored": target not in (None, "NONE")}
    if out["scored"]:
        gmap, held, held_ids = _groups()
        out["heldout"] = target in held_ids
        out["gmap"] = gmap
        out["tgroup"] = held.get(target) if out["heldout"] else gmap.get(target, target)
    return out


def _mark(case_ref, truth) -> str:
    """✓ the true case · ≈ its look-alike group · ✗ wrong."""
    if case_ref is None:
        return "✗"
    if case_ref == truth["target"]:
        return "✓"
    tg = truth.get("tgroup")
    return "≈" if tg is not None and truth["gmap"].get(case_ref) == tg else "✗"


def tick_accuracy(d: dict, truth: dict | None) -> dict:
    """Published and belief-alone rank-1 marks and the shown confidence of the
    published rank-1, on the ticks the evaluator scores (fault onset onward,
    a ranking published)."""
    hyp = d.get("hypotheses") or []
    conf = (hyp[0].get("confidence_shown", hyp[0].get("confidence")) if hyp else None)
    out = {"pub": "", "bel": "", "conf": conf, "scored": False}
    if not hyp or truth is None:
        return out
    if not truth["scored"]:
        return out
    if d["tick"] * TICK_S_AGENT < truth["onset"]:
        out["pub"] = out["bel"] = "pre"
        return out
    brk = d.get("belief_ranking") or []
    out.update(scored=True, pub=_mark(hyp[0].get("case_ref"), truth),
               bel=_mark(brk[0].get("case_ref") if brk else None, truth))
    return out


def running_scores(records: list[dict], truth: dict | None) -> dict | None:
    """The evaluator's own T2 / T5 on the ticks so far."""
    if truth is None or not records:
        return None
    from bench.evaluator import t2_root_cause, t5_false_positives
    run = {"ground_truth": truth["gt"], "assessments": records}
    try:
        return {"t2": t2_root_cause(run), "t5": t5_false_positives(run)}
    except Exception:                       # a monitor never stops a run
        return None


# ===========================================================================
#  The tick file -> rows and jobs
# ===========================================================================
def arch_of(records: list[dict]) -> str:
    return "multi" if records and "multi" in records[0] else "single"


def is_lockstep(records: list[dict]) -> bool:
    """Real-time ticks always record what they submitted; lockstep ticks never do."""
    return bool(records) and "submitted" not in (records[0].get("multi") or {})


def build(records: list[dict], truth: dict | None = None) -> dict:
    """Rows (one per tick) and jobs (one per model call) from the tick records.
    A job's verdict is read from the gate's own record of its answer:
      ok      the answer was well-formed and used
      empty   well-formed, but it ranked no case the prompt showed
      bad     not the asked format, or the call failed (the tick's code-only
              ranking stands)
      stale   arrived too late, dropped
    and for the note reader: ok (note-fact accepted) or bad (rejected)."""
    arch = arch_of(records)
    rows, jobs, verdict, tokens = [], {}, {}, {}
    for d in records:
        m = d.get("multi") or {}
        hyp = d.get("hypotheses") or []
        envs = d.get("envelopes") or []
        p0 = m.get("p0_ms")
        if arch == "single" and d.get("tick_latency_ms") is not None:
            # the tick's own time minus its model calls (the agent's code path)
            p0 = max(0.0, d["tick_latency_ms"] - sum(float(e.get("latency_ms") or 0)
                                                    for e in envs))
        row = {"tick": d["tick"], "plant": str(d.get("timestamp", ""))[11:19],
               "state": d.get("state", ""), "triage": d.get("triage", ""),
               "p0_ms": p0, "cause": hyp[0]["cause"] if hyp else "",
               "reused": set(), **{"acc": tick_accuracy(d, truth)}}
        rows.append(row)
        if arch == "single":
            jobs.update(_single_jobs(d))
            continue
        _collect_tokens(d, m, tokens)
        if "submitted" not in m:           # lockstep: every job of the tick is done
            jobs.update(_lockstep_jobs(d, m))
            for c in m.get("compact", []):
                if c.get("agent") == "merge":
                    row["reused"] = set(c.get("reused") or [])
            continue
        for j in m.get("submitted", []):
            jobs[j["job"]] = {"id": j["job"], "agent": j["agent"], "side": j.get("side"),
                              "tick": d["tick"], "sent_s": j.get("wall_s"), "lane": None,
                              "start_s": None, "finish_s": None, "verdict": None,
                              "in_tok": None, "out_tok": None}
        for r in m.get("results", []):
            j = jobs.setdefault(r["job_id"], {
                "id": r["job_id"], "agent": r["agent"], "side": None,
                "tick": r["evidence_tick"], "sent_s": None, "verdict": None,
                "in_tok": None, "out_tok": None})
            j.update(lane=r.get("lane"), start_s=r.get("start_s"), finish_s=r.get("finish_s"),
                     queue_ms=r.get("queue_wait_ms"))
            if r.get("stale"):
                j["verdict"] = "stale"
        for t in m.get("text", []):
            j = jobs.get((t.get("result") or {}).get("job_id"))
            if j is not None:
                j["verdict"] = "ok" if (t.get("notefact") or {}).get("status") == "ok" else "bad"
        for c in m.get("compact", []):
            if c.get("agent") == "merge":
                row["reused"] = set(c.get("reused") or [])
            elif c.get("agent") == "diagnostician":
                v = "bad" if "answer" not in c else "empty" if c.get("empty_ranking") else "ok"
                verdict[("diag_" + str(c.get("side")), c["evidence_tick"])] = v
            elif c.get("agent") == "verifier":
                verdict[("verifier", c["evidence_tick"])] = "ok" if "answer" in c else "bad"
    for j in jobs.values():
        if j["verdict"] is None:
            j["verdict"] = verdict.get((j["agent"], j["tick"]))
        if j.get("in_tok") is None:
            key = j["id"] if j["agent"] == "text_reader" else (j["agent"], j["tick"])
            j["in_tok"], j["out_tok"] = tokens.get(key, (None, None))
    return {"arch": arch, "rows": rows, "jobs": list(jobs.values())}


def _collect_tokens(d: dict, m: dict, tokens: dict) -> None:
    """Real time: an answer's tokens are in the envelope folded at a LATER tick
    than its job was sent. Keyed so build() can attach them to the job:
    text reader by job id, verifier by evidence tick, a side's diagnosis by
    (side, evidence tick) -- envelopes and the gate's diagnosis records are
    written in the same order (gate.fold_sides), which gives the side."""
    envs = d.get("envelopes") or []
    recs = [c for c in m.get("compact", []) if c.get("agent") == "diagnostician"]
    for env, c in zip([e for e in envs if e.get("agent") == "diagnostician"], recs):
        tokens[("diag_" + str(c.get("side")), c.get("evidence_tick"))] = _tok(env)
    for env in envs:
        if env.get("agent") == "verifier":
            tokens[("verifier", env.get("tick"))] = _tok(env)
    for t in m.get("text", []):
        jid = (t.get("result") or {}).get("job_id")
        if jid:
            tokens[jid] = _tok(t.get("envelope") or {})


def _job(tick, agent, side, env, verdict, n) -> tuple[str, dict]:
    run = float(env.get("latency_ms") or 0.0) / 1000
    i, o = _tok(env)
    jid = f"t{tick}-{agent}-{n}"
    return jid, {"id": jid, "agent": agent, "side": side, "tick": tick, "sent_s": None,
                 "lane": env.get("backend"), "start_s": 0.0, "finish_s": run,
                 "queue_ms": None, "verdict": verdict, "in_tok": i, "out_tok": o}


def _lockstep_jobs(d: dict, m: dict) -> dict:
    """A lockstep tick's model calls, with their REAL times: the envelopes of
    the tick (latency_ms), paired in order with the gate's record of each
    answer. The job times in `multi.results` are simulated and not used."""
    out, tick = {}, d["tick"]

    def add(agent, side, env, verdict):
        k, v = _job(tick, agent, side, env, verdict, len(out))
        out[k] = v

    envs = d.get("envelopes") or []
    for kind in ("diagnostician", "verifier"):
        recs = [c for c in m.get("compact", []) if c.get("agent") == kind]
        for env, c in zip([e for e in envs if e.get("agent") == kind], recs):
            used = "answer" in c and env.get("status") == "ok"
            if kind == "verifier":
                add("verifier", None, env, "ok" if used else "bad")
            else:
                add("diag_" + str(c.get("side")), c.get("side"), env,
                    "bad" if not used else "empty" if c.get("empty_ranking") else "ok")
    for t in m.get("text", []):
        add("text_reader", None, t.get("envelope") or {},
            "ok" if (t.get("notefact") or {}).get("status") == "ok" else "bad")
    return out


def _single_jobs(d: dict) -> dict:
    """Single agent: one diagnostician and (sometimes) one verifier call per
    tick, both on the NPU lane, each used when its status is ok."""
    out = {}
    for env in d.get("envelopes") or []:
        agent = env.get("agent")
        if agent in ("diagnostician", "verifier"):
            k, v = _job(d["tick"], agent, None, env,
                        "ok" if env.get("status") == "ok" else "bad", len(out))
            out[k] = v
    return out


def lane_of(job: dict, placement: dict) -> str | None:
    return job.get("lane") or placement.get(job["agent"])


def lane_now(lane: str, jobs: list[dict], placement: dict, busy: bool | None,
             now_s: float, running_tick: int | None = None) -> tuple[str, bool]:
    """What a lane is doing: (text, working). `busy` is the board's own answer
    (True / False), or None when it was not asked or did not answer."""
    out = sorted((j for j in jobs if j.get("finish_s") is None
                  and lane_of(j, placement) == lane and j.get("sent_s") is not None),
                 key=lambda j: j["sent_s"])
    if not out:
        if busy and running_tick is not None:    # lockstep: the tick is not in the file yet
            return f"tick {running_tick:<4} working (the job is named when the tick completes)", True
        return ("working (job not in the tick file yet)", True) if busy else ("idle", False)
    j = out[0]
    what = f"tick {j['tick']:<4} {LABEL.get(j['agent'], j['agent'])}"
    more = f"  (+{len(out) - 1} waiting)" if len(out) > 1 else ""
    if busy is False:
        return f"idle · {what}: answered, folded at the next tick", False
    ago = f"sent {max(0.0, now_s - j['sent_s']):.1f} s ago"
    if busy is None:
        return f"{what}  {ago}, answer not folded yet (lane not asked){more}", True
    return f"{what}  running, {ago}{more}", True


MARK = {"ok": "✓", "empty": "∅", "bad": "✗", "stale": "~", None: ""}


def cell(row: dict, agent: str, jobs: list[dict], now_s: float,
         lane_busy: bool | None = None) -> str:
    """`lane_busy`: the board's answer for this agent's lane (None: not asked).
    A finished job reads `<run time>s <input tokens>t<mark>`."""
    mine = [j for j in jobs if j["agent"] == agent and j["tick"] == row["tick"]]
    if not mine:
        if agent.startswith("diag_") and agent[5:] in row["reused"]:
            return "reuse"
        return "—" if row["triage"] == "QUIET" else "·"
    running = [j for j in mine if j.get("finish_s") is None]
    if running and lane_busy is False:
        return "answered"                  # the lane is idle: folded at the next tick
    if running:
        sent = min((j["sent_s"] for j in running if j.get("sent_s") is not None), default=now_s)
        return f"▶{max(0.0, now_s - sent):.0f}s"
    run = max(j["finish_s"] - j["start_s"] for j in mine)
    toks = [j.get("in_tok") for j in mine]
    tok = f" {sum(t for t in toks if t)}t" if any(toks) else ""
    return f"{run:.1f}s{tok}" + "".join(MARK.get(j["verdict"], "") for j in mine)


def _acc_cells(row: dict) -> tuple[str, str, str]:
    a = row["acc"]
    conf = "" if a["conf"] is None else f"{a['conf']:.2f}"
    return a["pub"], a["bel"], conf


# ===========================================================================
#  The screen
# ===========================================================================
def header(arch: str) -> str:
    heads = [HEAD_SINGLE.get(a, HEAD[a]) if arch == "single" else HEAD[a] for a in COLS[arch]]
    return (f"{'tick':>5}  {'plant':<8}  {'state':<13} {'triage':<11} {'code':>6}  "
            + " ".join(f"{h:>{CELL_W}}" for h in heads)
            + f"  {'pub':>3} {'bel':>3} {'conf':>4}  top cause")


def _score_line(model: dict, scores: dict | None, truth: dict | None) -> str | None:
    if scores is None or truth is None:
        return None
    t2, t5 = scores["t2"], scores["t5"]
    if t5.get("applicable"):
        return (f"no-fault episode · ticks not NORMAL so far {t5['fp_ticks']} "
                f"({t5['fp_per_hour']}/h; target <= 2/h)")
    if not t2.get("applicable"):
        return "no library case for this episode's fault: rank-1 is not scored"
    if not t2["n_scored"]:
        return (f"true cause {t2['target']} (group {t2['true_group']}) · nothing scored yet "
                f"(scoring starts at fault onset, plant time {truth['onset']:.0f} s)")
    confs = [r["acc"]["conf"] for r in model["rows"] if r["acc"]["scored"]
             and r["acc"]["conf"] is not None]
    held = " (held-out case: exact top-1 not applicable)" if t2["heldout"] else \
        f" · exact top-1 {t2['top1']:.2f}"
    return (f"true cause {t2['target']} · scored ticks {t2['n_scored']} · published group top-1 "
            f"{t2['group_top1']:.2f} · belief alone {t2['belief_group']:.2f}{held}"
            + (f" · mean shown confidence {statistics.mean(confs):.2f}" if confs else ""))


def render(model: dict, meta: dict, slots: dict, now_s: float,
           size: tuple[int, int], colour: bool = True, scores: dict | None = None,
           truth: dict | None = None, top: list[str] | None = None) -> list[str]:
    """`top`: lines a runner puts above the monitor (test, episode i of n)."""
    cols, lines = size
    c = _c if colour else (lambda text, *styles: text)
    arch = model.get("arch", "multi")
    agents = COLS[arch]
    rows, jobs, placement = model["rows"], model["jobs"], meta["placement"]
    done = [j for j in jobs if j.get("finish_s") is not None]
    out = [c(line[:cols - 1], "cyan") for line in (top or [])]
    who = "multi-agent" if arch == "multi" else "single agent"
    out.append(c(f"FieldMind stage monitor, {who}   {meta['episode']}   "
                 f"{'FINISHED' if meta.get('finished') else 'elapsed ' + _clock(now_s)}", "bold"))
    last = rows[-1]["tick"] if rows else "-"
    lock = arch == "single" or bool(meta.get("lockstep"))
    nxt = rows[-1]["tick"] + 1 if rows and not meta.get("finished") else None
    pace = ("BACK TO BACK (lockstep): a tick waits for its answers, the next starts at once"
            if lock else f"real time · tick period {meta['tick_s']:g} s")
    tin = sum(j.get("in_tok") or 0 for j in done)
    tout = sum(j.get("out_tok") or 0 for j in done)
    out.append(f"{pace} · tick {last} of {meta.get('last_tick', '?')} · model calls "
               f"answered {len(done)}" + ("" if lock else f" · still out {len(jobs) - len(done)}")
               + f" · tokens in {tin:,} / out {tout:,}")
    stats = []
    for a in agents:
        mine = [j for j in done if j["agent"] == a]
        if mine:
            med = statistics.median(j["finish_s"] - j["start_s"] for j in mine)
            ins = [j["in_tok"] for j in mine if j.get("in_tok")]
            bad = sum(j["verdict"] in ("bad", "stale") for j in mine)
            stats.append(f"{LABEL[a]} {len(mine)} (median {med:.1f} s"
                         + (f", {statistics.median(ins):.0f} tokens in" if ins else "")
                         + f", {bad} not usable)")
    if stats:
        out.append(c(" · ".join(stats), "dim"))
    score = _score_line(model, scores, truth)
    if score:
        out.append(c(score, "green"))
    out.append("")

    # ---- the compute units -------------------------------------------------
    out.append(f" {'UNIT':<46} NOW")
    p0 = rows[-1]["p0_ms"] if rows else None
    worst = max((r["p0_ms"] for r in rows if r["p0_ms"] is not None), default=None)
    wait = meta["tick_s"] - (now_s % meta["tick_s"])
    code = ("waiting for the first tick" if p0 is None else
            f"tick {last:<4} code path took {p0:.0f} ms (worst {worst:.0f}, limit "
            f"{HARD_LIMIT_MS}) · "
            + (f"tick {nxt} running for {meta.get('since_last_s', 0):.0f} s" if lock
               else f"next tick in {wait:.0f} s"))
    if meta.get("finished"):
        code = "episode finished"
    out.append(f" {('CODE  ' + meta['host'])[:46]:<46} "
               + c(code, *(("red",) if worst and worst > HARD_LIMIT_MS else ())))
    for lane in meta.get("lanes", ("npu", "cpu")):
        text, working = lane_now(lane, jobs, placement, slots.get(lane), now_s,
                                 nxt if lock else None)
        unit = f"{lane.upper():<5} board · {meta['models'].get(lane, '?')}"
        if slots.get(lane) is None and meta.get("poll") and not meta.get("finished"):
            text += "   [lane did not answer]"
        out.append(f" {unit[:46]:<46} "
                   + c(text, *(("magenta", "bold") if working else ("dim",))))
        mine = [j for j in done if lane_of(j, placement) == lane]
        if mine:
            j = max(mine, key=lambda j: (j["tick"], j["finish_s"]))
            queue = ("" if j.get("queue_ms") is None
                     else f", waited {j['queue_ms']:.0f} ms in the queue")
            tok = ("" if j.get("in_tok") is None
                   else f", {j['in_tok']} tokens in / {j.get('out_tok')} out")
            out.append(c(f"   last answer: tick {j['tick']} {LABEL.get(j['agent'], j['agent'])}, "
                         f"ran {j['finish_s'] - j['start_s']:.1f} s{tok}{queue}"
                         f"{', ' + j['verdict'] if j['verdict'] else ''}", "dim"))
    out.append("")

    # ---- tick list ---------------------------------------------------------
    out.append(c(header(arch)[:cols - 1], "bold"))
    body = []
    for r in rows:
        cells = [cell(r, a, jobs, now_s, slots.get(placement.get(a))) for a in agents]
        pub, bel, conf = _acc_cells(r)
        code_ms = "" if r["p0_ms"] is None else f"{r['p0_ms']:.0f}ms"
        line = (f"{r['tick']:>5}  {r['plant']:<8}  {r['state']:<13} {r['triage']:<11} "
                f"{code_ms:>6}  " + " ".join(f"{x:>{CELL_W}}" for x in cells)
                + f"  {pub:>3} {bel:>3} {conf:>4}  {r['cause']}")
        style = (("yellow", "bold") if any(x.startswith("▶") or x == "answered" for x in cells)
                 else ("red",) if any("✗" in x or "~" in x for x in cells)
                 else ("dim",) if r["triage"] == "QUIET" else ())
        body.append((line[:cols - 1], style))
    room = max(3, lines - len(out) - 3)
    if len(body) > room:
        body = [(f"  … {len(body) - room + 1} earlier ticks (all in the tick file)", ("dim",))] \
            + body[-(room - 1):]
    out += [c(line, *style) for line, style in body]
    out.append(c(("cells: ▶ running · answered = waits for the next tick · 2.5s 712t = call time "
                  "and input tokens · ✓ used · ∅ ranked no shown case · ✗ wrong format or failed · "
                  "~ stale · reuse · · not asked · — quiet   pub/bel: rank-1 published / belief "
                  "alone ✓ true case ≈ its group ✗ wrong pre before onset   conf: shown "
                  "confidence of rank-1")[:cols - 1], "dim"))
    return out


# ===========================================================================
#  Reading the run
# ===========================================================================
class Tail:
    """The complete lines of a growing JSONL file, read incrementally."""

    def __init__(self, path: Path):
        self.path, self.pos, self.records = path, 0, []

    def poll(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("rb") as f:
            f.seek(self.pos)
            data = f.read()
        end = data.rfind(b"\n") + 1            # a half-written last line waits
        for line in data[:end].splitlines():
            if line.strip():
                self.records.append(json.loads(line))
        self.pos += end


class SlotPoller(threading.Thread):
    """Asks each lane's llama-server whether its slot is processing."""

    def __init__(self, urls: dict, period_s: float):
        super().__init__(daemon=True, name="slot-poller")
        self.urls, self.period_s, self.busy = urls, period_s, {k: None for k in urls}

    def run(self) -> None:
        while True:
            for lane, url in self.urls.items():
                try:
                    with urllib.request.urlopen(url.rstrip("/") + "/slots", timeout=1.0) as r:
                        self.busy[lane] = any(s.get("is_processing") for s in json.load(r))
                except Exception:
                    self.busy[lane] = None
            time.sleep(self.period_s)


def episode_dir(ep: str) -> Path:
    """dev_* episodes live in data/episodes_dev, the reporting ones in data/episodes."""
    return ROOT / ("data/episodes_dev" if ep.startswith("dev_") else "data/episodes") / ep


class Screen:
    """One monitor over one tick file at a time; `frame` renders it."""

    def __init__(self, cfg: dict, arch: str, tick_s: float, poller: SlotPoller | None,
                 lockstep: bool | None = None):
        self.cfg, self.arch, self.tick_s, self.poller = cfg, arch, tick_s, poller
        self.lockstep = lockstep
        self.tail: Tail | None = None
        self.episode = ""
        self.truth = None
        lanes = cfg["multi"]["lanes"]
        self.models = {k: str(v.get("model_file", "?")).split("-Q4_0")[0] for k, v in lanes.items()}
        if arch == "single":
            mf = cfg["llm"].get("llamaserver", {}).get("model_file") or lanes["npu"].get("model_file")
            self.models = {"npu": str(mf).split("-Q4_0")[0]}

    def follow(self, path: Path, episode: str) -> None:
        if self.tail is None or self.tail.path != path:
            self.tail = Tail(path)
            self.episode = episode
            self.truth = load_truth(episode_dir(episode))

    def frame(self, size, colour: bool, top: list[str] | None = None,
              finished: bool = False) -> list[str]:
        tail = self.tail
        tail.poll()
        recs = tail.records
        n = len(recs)
        arch = arch_of(recs) if recs else self.arch
        lock = self.lockstep if self.lockstep is not None else (arch == "single" or is_lockstep(recs))
        since = 0.0 if finished or not n or not tail.path.exists() else \
            time.time() - tail.path.stat().st_mtime
        if lock:
            # back to back: the ticks' own real times, plus the tick now running
            now_s = sum(r.get("tick_latency_ms") or 0.0 for r in recs) / 1000 + since
        else:
            # the run's own clock: tick i of the file is due at i * tick_s
            now_s = max(0, n - 1) * self.tick_s + since
        placement = (self.cfg["multi"]["fixed_placement"] if arch == "multi"
                     else {"diagnostician": "npu", "verifier": "npu"})
        meta = {"episode": self.episode, "tick_s": self.tick_s, "finished": finished,
                "lockstep": lock, "since_last_s": since,
                "host": f"laptop {socket.gethostname()} ({platform.machine()})",
                "placement": placement, "poll": self.poller is not None,
                "lanes": ("npu", "cpu") if arch == "multi" else ("npu",),
                "models": self.models}
        if self.truth:
            dur = self.truth["gt"]["duration_min"] * 60
            meta["last_tick"] = int(dur // self.cfg["agent"]["tick_period_s"]) - 1
        slots = dict(self.poller.busy) if self.poller is not None and not finished else {}
        model = build(recs, self.truth)
        model["arch"] = arch
        return render(model, meta, slots, now_s, size, colour=colour,
                      scores=running_scores(recs, self.truth), truth=self.truth, top=top)


def newest(run_dir: Path | None) -> Path | None:
    """The live tick file written to last, in `run_dir` or anywhere under
    results/benchmarks."""
    found = run_dir.glob("*.ticks.jsonl") if run_dir else BENCH_DIR.glob("*/*.ticks.jsonl")
    files = [p for p in found if ".interrupted-" not in p.name]
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def episode_of(path: Path) -> str:
    """<ep>_<arch>[_rN].ticks.jsonl -> <ep>."""
    stem = path.name[:-len(".ticks.jsonl")]
    for arch in ("_multi", "_single"):
        if arch in stem:
            return stem[:stem.rindex(arch)]
    return stem


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default=None, help="a test folder (default: newest under results/benchmarks)")
    ap.add_argument("--episode", default=None, help="default: the newest tick file")
    ap.add_argument("--config", default=str(ROOT / "configs/base.yaml"))
    ap.add_argument("--overlay", default=str(ROOT / "configs/fast.yaml"))
    ap.add_argument("--tick-s", type=float, default=30.0, help="the run's --tick-s")
    ap.add_argument("--refresh", type=float, default=0.5, help="screen refresh, seconds")
    ap.add_argument("--no-poll", action="store_true",
                    help="do not ask the lanes whether they are busy")
    ap.add_argument("--once", action="store_true", help="print one frame and exit")
    args = ap.parse_args()

    from run_demo import load_config
    cfg = load_config(args.config, None, args.overlay)
    run_dir = Path(args.dir) if args.dir else None
    poller = None
    if not args.no_poll:
        poller = SlotPoller({k: v["url"] for k, v in cfg["multi"]["lanes"].items()}, 1.0)
        poller.start()
        time.sleep(0.3 if args.once else 0.0)

    def pick() -> Path | None:
        if args.episode:
            found = [p for d in ([run_dir] if run_dir else BENCH_DIR.glob("*"))
                     for p in Path(d).glob(f"{args.episode}_*.ticks.jsonl")]
            return max(found, key=lambda p: p.stat().st_mtime, default=None)
        return newest(run_dir)

    path = pick()
    if path is None:
        print(f"no live tick file in {run_dir or BENCH_DIR} yet: start a run first "
              f"(scripts/benchmark.sh)", file=sys.stderr)
        return 2
    screen = Screen(cfg, "multi", args.tick_s, poller)
    screen.follow(path, episode_of(path))
    if args.once or not sys.stdout.isatty():
        print("\n".join(screen.frame(shutil.get_terminal_size((160, 45)), sys.stdout.isatty())))
        return 0
    term = sys.stdout
    term.write("\033[?1049h\033[?25l")                 # alternate screen, hide cursor
    try:
        while True:
            now = pick()
            if now is not None:
                screen.follow(now, episode_of(now))   # the run moved to the next episode
            lines = screen.frame(shutil.get_terminal_size((160, 45)), True)
            term.write("\033[H" + "".join(l + "\033[K\n" for l in lines) + "\033[J")
            term.flush()
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        return 0
    finally:
        term.write("\033[?25h\033[?1049l")
        term.flush()


if __name__ == "__main__":
    raise SystemExit(main())
