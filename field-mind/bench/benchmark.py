#!/usr/bin/env python3
"""
=============================================================================
 BENCHMARK  --  run named tests on the board, with the stage monitor in the
 same terminal, every result kept under results/benchmarks/<test name>/
=============================================================================

    scripts/benchmark.sh <test name> <episode> [<episode> ...] [options]

    scripts/benchmark.sh grammar_v1 ep_N01_normal ep_A01_fcv_seize
    scripts/benchmark.sh single_ref ep_A01_fcv_seize --arch single
    scripts/benchmark.sh rt_demo ep_B01_tube_leak --mode realtime --tick-s 30
    scripts/benchmark.sh tiebreak dev_A01_fcv_seize --overlay configs/accuracy.yaml --merge-rule tiebreak

One folder per test name, results/benchmarks/<test name>/:
  test.json                  the test's settings and every run (who, when, git commit,
                             chip temperatures, outcome)
  RESULTS.md                 one row per finished episode run, rewritten after each
  temps.jsonl                chip temperature before and after every episode
  <ep>_<arch>.summary.json   evaluation + run facts (calls, tokens, unusable answers)
  <ep>_<arch>.run.json.gz    every tick, every prompt and raw reply (replayable)
  <ep>_<arch>.log            the run's own output
  <ep>_<arch>.lanes.txt      the lane startup lines that prove NPU / CPU placement
  <ep>_<arch>.ticks.jsonl    the live tick file while the episode runs (deleted once
                             the run file is saved; kept as .interrupted-<time> if not)

The same name again ADDS to the folder: new episodes are run and saved next to
the old ones. An episode the test already has is skipped (so a stopped test
resumes where it stopped) unless --again, which runs it once more and saves it
as <ep>_<arch>_r2, _r3, ...: nothing is ever overwritten. A test's settings
(architecture, mode, overlay and its contents, merge rule, models, backend) are
fixed by its first run; a later run with other settings is refused, so one
folder never mixes two configurations. Use a new name for a new configuration.

Board protocol (unchanged from the earlier runners): one thing on the board at
a time; lanes restarted fresh before every episode with the fixed flags of
bench.board (-c 4096 -np 1 -fit off --cache-ram 0 -lv 4) and the model files the
config names, sha256-checked by bench.board; NPU placement confirmed from the
startup log (nonzero HTP0 buffer, every layer offloaded) and CPU lane without
HTP0; chip temperature logged before and after; the next episode waits until
the chip is back within --cool-margin of the reading at the start of the batch
(at most --cool-max s).

Ctrl-C stops the run and the lanes; the stopped episode's ticks and log are
kept as <stem>.interrupted-<time>.*, and the same command resumes.
BACKEND mock (--backend mock) runs on the laptop without the board, into
results/benchmarks_mock/: plumbing only, never an agent result.

Host-side tool; imports fieldmind/ read-only.
=============================================================================
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench import board                                            # noqa: E402
from bench.stage_monitor_multi import (Screen, SlotPoller, _c,     # noqa: E402
                                       episode_dir)

PY = str(ROOT / ".venv/bin/python")
ROOT_RESULTS: Path | None = None   # tests only: where test folders go instead of results/
SINGLE_MODEL = "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf"   # the single agent's board model (Phase 0b)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
OTHER_BOARD_RUNS = re.compile(
    r"python -m bench\.campaign all|board_session\.sh|benchmark_multi_lockstep_all\.sh"
    r"|run_demo\.py .*--backend llamaserver|bench/benchmark\.py")


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def now_iso() -> str:
    return board.laptop_time()


def stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S")


def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    tmp.replace(path)


def git_state() -> dict:
    def g(*a):
        p = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        return p.stdout.strip()
    dirty = g("status", "--porcelain", "--untracked-files=no", "--", ".", ":!results")
    return {"commit": g("rev-parse", "--short", "HEAD"), "branch": g("branch", "--show-current"),
            "uncommitted_changes": bool(dirty)}


# ===========================================================================
#  Settings
# ===========================================================================
def settings(args) -> tuple[dict, dict]:
    """(config the run uses, the test's fixed settings)."""
    from run_demo import load_config
    overlay = args.overlay
    if overlay is None and args.arch == "multi":
        overlay = "configs/fast.yaml"
    cfg = load_config(str(ROOT / "configs/base.yaml"), args.backend,
                      str(ROOT / overlay) if overlay else None)
    if args.merge_rule:
        cfg["multi"]["merge_rule"] = args.merge_rule
    if args.arch == "multi":
        models = {lane: cfg["multi"]["lanes"][lane].get("model_file") for lane in ("npu", "cpu")}
        if not all(models.values()):
            raise SystemExit("the overlay must name a model file for both lanes "
                             "(multi.lanes.<lane>.model_file), e.g. configs/fast.yaml")
    else:
        models = {"npu": cfg["llm"].get("llamaserver", {}).get("model_file") or SINGLE_MODEL}
    if args.mode == "realtime":
        if args.arch != "multi":
            raise SystemExit("--mode realtime is multi-agent only")
        if cfg["multi"].get("merge_rule", "model") != "model":
            raise SystemExit("--mode realtime needs merge_rule model (the other rules are "
                             "lockstep only)")
    params = {"arch": args.arch, "mode": args.mode,
              "tick_s": args.tick_s if args.mode == "realtime" else None,
              "overlay": overlay,
              "overlay_sha256": (hashlib.sha256((ROOT / overlay).read_bytes()).hexdigest()[:16]
                                 if overlay else None),
              "merge_rule": cfg["multi"].get("merge_rule") if args.arch == "multi" else None,
              "backend": args.backend, "models": models,
              "prompts_saved": not args.no_prompts}
    return cfg, params


class Test:
    """results/benchmarks/<name>/ and its test.json."""

    def __init__(self, root: Path, name: str, params: dict):
        self.dir = root / name
        self.path = self.dir / "test.json"
        self.name, self.params = name, params
        if self.path.exists():
            self.doc = json.loads(self.path.read_text())
            old = self.doc["params"]
            diff = {k: (old.get(k), params.get(k)) for k in set(old) | set(params)
                    if old.get(k) != params.get(k)}
            if diff:
                lines = "\n".join(f"  {k}: test has {a!r}, this run has {b!r}"
                                  for k, (a, b) in sorted(diff.items()))
                raise SystemExit(f"test {name!r} already exists with other settings:\n{lines}\n"
                                 f"Use a new test name for a new configuration.")
        else:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.doc = {"name": name, "created": now_iso(), "params": params, "runs": []}
            self.save()

    def save(self) -> None:
        write_json(self.path, self.doc)

    def finished_runs(self, ep: str, arch: str) -> list[int]:
        out = []
        for p in self.dir.glob(f"{ep}_{arch}*.summary.json"):
            m = re.fullmatch(rf"{re.escape(ep)}_{arch}(?:_r(\d+))?\.summary\.json", p.name)
            if m:
                out.append(int(m.group(1) or 1))
        return sorted(out)

    def add_run(self, entry: dict) -> dict:
        self.doc["runs"].append(entry)
        self.save()
        return entry


# ===========================================================================
#  One episode
# ===========================================================================
def run_stats(run: dict) -> dict:
    """Model calls, unusable answers, tokens and call times, from the run file."""
    envs = [e for a in run["assessments"] for e in a.get("envelopes", [])]
    envs += [t["envelope"] for a in run["assessments"]
             for t in (a.get("multi") or {}).get("text", [])]
    by = {}
    for e in envs:
        s = by.setdefault(e["agent"], {"calls": 0, "unusable": 0, "tokens_in": 0,
                                       "tokens_out": 0, "latency_s": []})
        s["calls"] += 1
        s["unusable"] += e.get("status") != "ok"
        t = e.get("tokens") or {}
        s["tokens_in"] += t.get("prefill") or 0
        s["tokens_out"] += t.get("decode") or 0
        s["latency_s"].append(float(e.get("latency_ms") or 0) / 1000)
    for a in run["assessments"]:                  # rejected note readings are unusable too
        for t in (a.get("multi") or {}).get("text", []):
            if t["envelope"].get("status") == "ok" and (t.get("notefact") or {}).get("status") != "ok":
                by["text_reader"]["unusable"] += 1
    for s in by.values():
        lat = s.pop("latency_s")
        s["median_call_s"] = round(statistics.median(lat), 2) if lat else None
    tot = {k: sum(s[k] for s in by.values()) for k in ("calls", "unusable", "tokens_in", "tokens_out")}
    return {"ticks": len(run["assessments"]), "by_agent": by, **tot}


class Runner(threading.Thread):
    """Runs the episodes one after another; the main thread shows the monitor."""

    def __init__(self, test: Test, cfg: dict, args, episodes: list[str]):
        super().__init__(daemon=True, name="benchmark-runner")
        self.test, self.cfg, self.args, self.episodes = test, cfg, args, episodes
        self.board = args.backend == "llamaserver"
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.phase = "starting"
        self.index = 0
        self.current = None                 # {"ep", "stem", "ticks": Path}
        self.done_lines: list[str] = []
        self.error: str | None = None
        self.child: subprocess.Popen | None = None
        self.base_c = None

    def say(self, phase: str) -> None:
        with self.lock:
            self.phase = phase
        if self.args.plain:
            print(f"[{time.strftime('%H:%M:%S')}] {phase}", flush=True)

    # ---------------------------------------------------------------- board
    def temp(self) -> float | None:
        t = board.chip_temperature()
        vals = [t.get("cpu_max_c"), t.get("npu_max_c")]
        vals = [v for v in vals if isinstance(v, (int, float))]
        return max(vals) if vals else None

    def note_temp(self, ep: str, when: str, c, waited=None) -> None:
        with (self.test.dir / "temps.jsonl").open("a") as f:
            f.write(json.dumps({"episode": ep, "when": when, "t": now_iso(), "max_c": c,
                                "cool_wait_s": waited}) + "\n")

    def cool(self, ep: str) -> tuple[float | None, int]:
        waited = 0
        while not self.stop.is_set():
            t = self.temp()
            if t is None or self.base_c is None or t <= self.base_c + self.args.cool_margin \
                    or waited >= self.args.cool_max:
                return t, waited
            self.say(f"cooling before {ep}: chip {t:.1f} C, waiting for "
                     f"{self.base_c + self.args.cool_margin:.1f} C ({waited} s of at most "
                     f"{self.args.cool_max} s)")
            self.stop.wait(15)
            waited += 15
        return None, waited

    def start_lanes(self, stem: str) -> None:
        board.stop_lanes()
        lanes = ("npu", "cpu") if self.args.arch == "multi" else ("npu",)
        models = self.test.params["models"]
        for lane in lanes:
            self.say(f"starting the {lane.upper()} lane ({models[lane]})")
            info = board.start_lane(lane, models[lane])
            if board.wait_health(info["url"], timeout_s=300) is None:
                raise RuntimeError(f"the {lane} lane did not come up (board log {info['board_log']})")
        logs = {lane: board.lane_log(lane) for lane in lanes}
        keep = re.compile(r"offload|HTP0 model buffer|n_ctx_slot|n_threads =")
        text = "".join(f"== {lane} ({models[lane]})\n"
                       + "".join(l + "\n" for l in logs[lane].splitlines() if keep.search(l))
                       for lane in lanes)
        (self.test.dir / f"{stem}.lanes.txt").write_text(text)
        npu = logs["npu"]
        off = re.search(r"offloaded (\d+)/(\d+) layers", npu)
        if not (re.search(r"HTP0 model buffer size = *[1-9]", npu) and off
                and off.group(1) == off.group(2)):
            raise RuntimeError("the NPU lane is not fully on the NPU (no HTP0 buffer or not "
                               f"every layer offloaded): see {stem}.lanes.txt")
        if "cpu" in logs and "HTP0 model buffer" in logs["cpu"]:
            raise RuntimeError(f"the CPU lane loaded onto the NPU: see {stem}.lanes.txt")

    # ---------------------------------------------------------------- run
    def command(self, ep: str, ticks: Path, out: Path) -> list[str]:
        a, p = self.args, self.test.params
        cmd = [PY, "run_demo.py", "--backend", a.backend, "--arch", a.arch,
               "--mode", a.mode, "--episodes-dir", str(episode_dir(ep).parent.relative_to(ROOT)),
               "--episode", ep, "--live-ticks", str(ticks), "--tag", ep, "--out", str(out)]
        if p["overlay"]:
            cmd += ["--overlay", p["overlay"]]
        if a.mode == "realtime":
            cmd += ["--tick-s", str(a.tick_s)]
        if a.merge_rule:
            cmd += ["--merge-rule", a.merge_rule]
        if p["prompts_saved"]:
            cmd.append("--log-prompts")
        if self.board and shutil.which("systemd-inhibit"):
            cmd = ["systemd-inhibit", "--what=sleep:idle", f"--why=FieldMind benchmark {ep}"] + cmd
        return cmd

    def run(self) -> None:
        try:
            if self.board:
                self.say("stopping any lanes and reading the chip temperature")
                board.stop_lanes()
                self.base_c = self.temp()
                self.note_temp("batch", "start", self.base_c)
            for i, ep in enumerate(self.episodes):
                if self.stop.is_set():
                    break
                with self.lock:
                    self.index = i
                if not self.episode(ep):
                    break
            if not self.stop.is_set() and self.error is None:
                self.say("all episodes done")
        except Exception as e:                       # shown, never swallowed
            self.error = f"{type(e).__name__}: {e}"
        finally:
            if self.board:
                board.stop_lanes()

    def episode(self, ep: str) -> bool:
        """Runs one episode; False stops the batch."""
        arch, test = self.args.arch, self.test
        have = test.finished_runs(ep, arch)
        if have and not self.args.again:
            self.done_lines.append(f"{ep}: already in this test (run {have[-1]}), skipped "
                                   f"(--again runs it once more)")
            self.say(self.done_lines[-1])
            return True
        n = (max(have) + 1) if have else 1
        stem = f"{ep}_{arch}" + (f"_r{n}" if n > 1 else "")
        d = test.dir
        ticks, log, out = d / f"{stem}.ticks.jsonl", d / f"{stem}.log", d / f".tmp_{stem}"
        self.keep_partial(stem)                       # an earlier stopped attempt
        shutil.rmtree(out, ignore_errors=True)
        entry = {"episode": ep, "run": n, "stem": stem, "status": "running",
                 "started": now_iso(), "git": git_state()}
        c0 = waited = None
        if self.board:
            c0, waited = self.cool(ep)
            if self.stop.is_set():
                return False
            self.start_lanes(stem)
            self.note_temp(ep, "before", c0, waited)
        entry.update(chip_c_before=c0, cool_wait_s=waited)
        test.add_run(entry)
        with self.lock:
            self.current = {"ep": ep, "stem": stem, "ticks": ticks, "finished": False}
        self.say(f"running {ep} (run {n})")
        t0 = time.time()
        with log.open("w") as f:
            f.write(f"=== {ep} run {n} of test {test.name}, started {entry['started']}\n")
            f.flush()
            self.child = subprocess.Popen(self.command(ep, ticks, out), cwd=ROOT, stdout=f,
                                          stderr=subprocess.STDOUT,
                                          env={**board.clean_env(), "PYTHONUNBUFFERED": "1"})
            while self.child.poll() is None:
                if self.stop.wait(0.5):
                    self.child.send_signal(signal.SIGINT)
                    try:
                        self.child.wait(20)
                    except subprocess.TimeoutExpired:
                        self.child.kill()
                    break
        rc = self.child.wait()
        entry["wall_s"] = round(time.time() - t0, 1)
        c1 = None
        if self.board:
            c1 = self.temp()
            self.note_temp(ep, "after", c1)
            board.stop_lanes()
        entry.update(chip_c_after=c1, exit_code=rc, finished=now_iso())
        runs = out / f"runs_{self.args.backend}_{ep}.json"
        summ = out / f"summary_{self.args.backend}_{ep}.json"
        if rc != 0 or not runs.exists() or not summ.exists() or self.stop.is_set():
            entry["status"] = "interrupted" if self.stop.is_set() else "failed"
            test.save()
            self.keep_partial(stem)
            shutil.rmtree(out, ignore_errors=True)
            if not self.stop.is_set():
                tail = log.with_name(log.name).read_text()[-1500:] if log.exists() else ""
                self.error = f"{ep} failed (exit {rc}). End of its log:\n{tail}"
            return False
        self.say(f"saving {ep}")
        run = json.loads(runs.read_text())[0]
        ev = json.loads(summ.read_text())["per_episode"][0]
        stats = run_stats(run)
        meta = {"test": test.name, "episode": ep, "run": n, "params": test.params,
                "started": entry["started"], "finished": entry["finished"],
                "wall_s": entry["wall_s"], "git": entry["git"],
                "chip_c_before": c0, "chip_c_after": c1, "cool_wait_s": waited,
                "where": ("agent code on the laptop, model calls on the board" if self.board
                          else "MOCK backend on the laptop: plumbing only, NOT an agent result"),
                "energy_mwh": None}
        write_json(d / f"{stem}.summary.json", {"meta": meta, "evaluation": ev, "calls": stats})
        with gzip.open(d / f"{stem}.run.json.gz", "wt") as f:
            json.dump(run, f)
        ticks.unlink(missing_ok=True)                 # the run file holds every tick
        shutil.rmtree(out, ignore_errors=True)
        entry["status"] = "done"
        test.save()
        write_results(test)
        with self.lock:
            self.current = dict(self.current, finished=True)
        self.done_lines.append(one_line(ep, n, ev, stats, entry))
        return True

    def keep_partial(self, stem: str) -> None:
        """Ticks, log and lane lines of a stopped attempt, kept under another name."""
        s = stamp()
        for kind in ("ticks.jsonl", "log", "lanes.txt"):
            p = self.test.dir / f"{stem}.{kind}"
            if p.exists() and not (self.test.dir / f"{stem}.summary.json").exists():
                p.rename(p.with_name(f"{stem}.interrupted-{s}.{kind}"))


# ===========================================================================
#  RESULTS.md
# ===========================================================================
def _f(x, nd=2):
    return "–" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def one_line(ep, n, ev, stats, entry) -> str:
    t2 = ev["T2_root_cause"]
    acc = (f"group top-1 {_f(t2.get('group_top1'))} (belief alone {_f(t2.get('belief_group'))})"
           if t2.get("applicable") else
           f"false alarms {ev['T5_false_positives'].get('fp_per_hour')}/h"
           if ev["T5_false_positives"].get("applicable") else "rank-1 not scored")
    return (f"{ep} run {n} saved: {acc} · {stats['calls']} model calls, {stats['unusable']} "
            f"unusable · {stats['tokens_in']:,} tokens in · {entry['wall_s']:.0f} s")


def write_results(test: Test) -> None:
    rows = []
    for p in sorted(test.dir.glob("*.summary.json")):
        s = json.loads(p.read_text())
        m, ev, st = s["meta"], s["evaluation"], s["calls"]
        t2, t3, t4, t6 = (ev["T2_root_cause"], ev["T3_faithfulness"], ev["T4_actions"],
                          ev["T6_lead_time"])
        rows.append((m["episode"], m["run"], m["finished"][:16].replace("T", " "),
                     st.get("ticks", "–"), st["calls"], st["unusable"], f"{st['tokens_in']:,}",
                     _f(t2.get("group_top1")), _f(t2.get("belief_group")),
                     _f(t2.get("top1_strict")), _f(t3.get("faithfulness")),
                     (f"{_f(t4.get('precision'))} / {_f(t4.get('recall'))}"
                      if t4.get("applicable") else "–"),
                     _f(t6.get("lead_time_min"), 1) if t6.get("applicable") else "–",
                     (_f(ev["T5_false_positives"].get("fp_per_hour"))
                      if ev["T5_false_positives"].get("applicable") else "–"),
                     _f(ev.get("S1_tick_latency_ms_p95"), 0), f"{m['wall_s']:.0f}",
                     f"{_f(m.get('chip_c_before'), 1)} → {_f(m.get('chip_c_after'), 1)}"))
    head = ("episode", "run", "finished", "ticks", "model calls", "unusable", "tokens in",
            "group top-1", "belief group top-1", "exact top-1", "faithfulness",
            "actions P / R", "lead time min", "false alarms /h", "tick p95 ms", "wall s", "chip C")
    p = test.params
    mock = p["backend"] == "mock"
    lines = [f"# Benchmark `{test.name}`", "",
             f"{'**MOCK backend: plumbing only, NOT an agent result.** ' if mock else ''}"
             f"Architecture **{p['arch']}**, mode **{p['mode']}**"
             + (f" (tick {p['tick_s']} s)" if p["tick_s"] else "")
             + f", overlay `{p['overlay']}`, merge rule `{p['merge_rule']}`, models "
             + ", ".join(f"{k}: `{v}`" for k, v in p["models"].items())
             + ". Agent code ran on the laptop and only model calls on the board, so tick times "
               "are laptop times. Energy not measured. All plant data is synthetic.", "",
             "Group top-1 is the share of scored ticks (fault onset onward) whose published "
             "rank-1 cause is in the true case's look-alike group; belief group top-1 is the "
             "same for the code-only ranking. A held-out true case has no exact top-1. "
             "Unusable = a model answer that was not used (wrong format, failed call, rejected "
             "note reading). Settings and every run, stopped ones included: `test.json`.", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(map(str, r)) + " |" for r in rows]
    (test.dir / "RESULTS.md").write_text("\n".join(lines) + "\n")


# ===========================================================================
#  The terminal
# ===========================================================================
def top_lines(r: Runner) -> list[str]:
    with r.lock:
        cur, phase, i = r.current, r.phase, r.index
    mock = "MOCK BACKEND (plumbing only, NOT an agent result) · " if not r.board else ""
    out = [f"{mock}BENCHMARK {r.test.name} → {_rel(r.test.dir)}/ · episode {i + 1} of "
           f"{len(r.episodes)}: {r.episodes[i]} · {phase}"]
    out += [f"  {l}" for l in r.done_lines[-4:]]
    return out


def waiting_screen(r: Runner, size) -> list[str]:
    cols, _ = size
    out = [_c(l[:cols - 1], "cyan") for l in top_lines(r)]
    out += ["", "  (the monitor appears when the episode's first tick is out)"]
    res = r.test.dir / "RESULTS.md"
    if res.exists():
        out += [""] + [l[:cols - 1] for l in res.read_text().splitlines() if l.startswith("|")]
    return out


def plain(r: Runner) -> None:
    """No full screen: one printed line per tick (pipes, logs, --plain)."""
    from run_demo import LiveTickWriter
    tail_path, pos = None, 0
    while r.is_alive():
        with r.lock:
            cur = r.current
        if cur and cur["ticks"] != tail_path:
            tail_path, pos = cur["ticks"], 0
        if tail_path is not None and tail_path.exists():
            with tail_path.open() as f:
                f.seek(pos)
                data = f.read()
            end = data.rfind("\n") + 1
            for line in data[:end].splitlines():
                print(LiveTickWriter.line(json.loads(line)), flush=True)
            pos += end
        time.sleep(0.5)


def main() -> int:
    board.drop_pythonpath()
    ap = argparse.ArgumentParser(
        description="Run a named benchmark test with the live stage monitor in this terminal.",
        epilog="Results: results/benchmarks/<name>/ (RESULTS.md, test.json, one set of files "
               "per episode run). Same name again adds to it; --again re-runs saved episodes.")
    ap.add_argument("name", help="test name: letters, digits, . _ - (the folder name)")
    ap.add_argument("episodes", nargs="+", help="episode ids (ep_* reporting, dev_* dev set)")
    ap.add_argument("--arch", choices=["multi", "single"], default="multi")
    ap.add_argument("--overlay", default=None,
                    help="config overlay (default configs/fast.yaml for multi, none for single)")
    ap.add_argument("--mode", choices=["lockstep", "realtime"], default="lockstep",
                    help="lockstep (default): ticks back to back, each waits for its model "
                         "answers; realtime: a tick every --tick-s seconds, never waits")
    ap.add_argument("--tick-s", type=float, default=30.0, help="realtime: seconds per tick")
    ap.add_argument("--merge-rule", choices=["model", "belief_only", "tiebreak", "nudge", "hybrid"],
                    default=None, help="multi: overrides multi.merge_rule")
    ap.add_argument("--again", action="store_true",
                    help="run episodes this test already has once more (saved as _r2, _r3, ...)")
    ap.add_argument("--backend", choices=["llamaserver", "mock"], default="llamaserver",
                    help="mock: laptop only, results/benchmarks_mock/ (never an agent result)")
    ap.add_argument("--no-prompts", action="store_true",
                    help="do not save prompts and raw replies in the run file")
    ap.add_argument("--cool-margin", type=float, default=5.0)
    ap.add_argument("--cool-max", type=int, default=900)
    ap.add_argument("--plain", action="store_true", help="print lines instead of the monitor")
    ap.add_argument("--refresh", type=float, default=0.5)
    args = ap.parse_args()

    if not NAME_RE.match(args.name):
        ap.error("test name: letters, digits, '.', '_' and '-' only, at most 80 characters")
    missing = [e for e in args.episodes if not (episode_dir(e) / "ground_truth.json").exists()]
    if missing:
        ap.error(f"unknown episode(s): {', '.join(missing)} (ep_* in data/episodes, "
                 f"dev_* in data/episodes_dev)")
    episodes = list(dict.fromkeys(args.episodes))
    cfg, params = settings(args)
    if args.backend == "llamaserver":
        devs = [l for l in subprocess.run(["adb", "devices"], capture_output=True, text=True,
                                          env=board.clean_env()).stdout.splitlines()[1:]
                if l.strip().endswith("device")]
        if len(devs) != 1:
            raise SystemExit(f"need exactly one board on adb, found {len(devs)}")
        busy = [l for l in subprocess.run(["pgrep", "-af", "python|bash|sh"], capture_output=True,
                                          text=True).stdout.splitlines()
                if OTHER_BOARD_RUNS.search(l) and int(l.split()[0]) != os.getpid()]
        if busy:
            raise SystemExit("another board run is going (one thing on the board at a time):\n  "
                             + "\n  ".join(b[:150] for b in busy))
    root = ROOT_RESULTS or ROOT / ("results/benchmarks" if args.backend == "llamaserver"
                                   else "results/benchmarks_mock")
    test = Test(root, args.name, params)
    runner = Runner(test, cfg, args, episodes)
    tty = sys.stdout.isatty() and not args.plain
    args.plain = not tty
    poller = None
    if args.backend == "llamaserver":
        urls = {k: v["url"] for k, v in cfg["multi"]["lanes"].items()}
        if args.arch == "single":
            urls = {"npu": urls["npu"]}
        poller = SlotPoller(urls, 1.0)
        poller.start()
    screen = Screen(cfg, args.arch, args.tick_s, poller, lockstep=args.mode == "lockstep")
    runner.start()
    stopped = False
    term = sys.stdout
    try:
        if not tty:
            plain(runner)
        else:
            term.write("\033[?1049h\033[?25l")             # alternate screen, hide cursor
            while runner.is_alive():
                size = shutil.get_terminal_size((160, 45))
                with runner.lock:
                    cur = dict(runner.current) if runner.current else None
                if cur and (cur["ticks"].exists() or cur["finished"]
                            or (screen.tail is not None and screen.tail.path == cur["ticks"])):
                    screen.follow(cur["ticks"], cur["ep"])
                    lines = screen.frame(size, True, top=top_lines(runner),
                                         finished=cur["finished"])
                else:
                    lines = waiting_screen(runner, size)
                term.write("\033[H" + "".join(l + "\033[K\n" for l in lines) + "\033[J")
                term.flush()
                time.sleep(args.refresh)
    except KeyboardInterrupt:
        stopped = True
        runner.stop.set()
    finally:
        if tty:
            term.write("\033[?25h\033[?1049l")
            term.flush()
    if stopped:
        print("stopping: the run, then the lanes (Ctrl-C again to leave at once) ...", flush=True)
        try:
            runner.join(90)
        except KeyboardInterrupt:
            pass
    runner.join()
    for line in runner.done_lines:
        print(line)
    if runner.error:
        print(f"\nSTOPPED ON AN ERROR: {runner.error}", file=sys.stderr)
    res = test.dir / "RESULTS.md"
    if res.exists():
        print("\n" + res.read_text())
    print(f"results: {_rel(test.dir)}/" + ("   (stopped by Ctrl-C: the same command "
                                                       "resumes)" if stopped else ""))
    return 1 if runner.error else (130 if stopped else 0)


if __name__ == "__main__":
    raise SystemExit(main())
