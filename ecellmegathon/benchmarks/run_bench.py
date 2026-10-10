"""
Board benchmark runner: N runs of the text eval on the QIDK, comparable across code versions.

For every run:
  1. stop llama-server (the NPU goes idle)
  2. COOL DOWN: wait until the NPU is at or below --cool-to °C (default 50), reading the chip every 15 s
  3. start a fresh llama-server on the NPU (temperature-0 replies drift between launches, so each run is
     its own launch) and confirm placement from its log ("offloaded N/N layers", HTP0 buffer)
  4. run eval/run_eval.py --label BOARD (through ui_dev/live_monitor.py when it is running, so every call
     shows on http://127.0.0.1:8099), sampling chip temperature every 10 s
  5. after the run: is llama-server still alive, any crash lines in its log (dspqueue, abort)
  6. save the eval's result, a .meta.json (git commit, settings, temperatures, crash check) and the
     eval's printed output under benchmarks/<date>_board_<SoC>/

Then it prints the comparison table (benchmarks/compare.py) for that folder.

  .venv/Scripts/python benchmarks/run_bench.py --tag v2 --runs 2
  .venv/Scripts/python benchmarks/run_bench.py --tag v2-like-v1 --runs 2 --ctx 4096 --no-fast-path

--tag names the configuration in the table (e.g. v1, v2). The settings that change results are recorded,
not assumed: --ctx (server context size), --no-fast-path (every turn to the LLM, as v1 did), --spec.
adb: on PATH, or --adb <path to adb>. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _s in (sys.stdout, sys.stderr):   # the Windows console cannot show every character
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
sys.path.insert(0, str(ROOT))

LLM_PORT = 8080
MONITOR_LLM = "http://127.0.0.1:8090"
MONITOR_DASH = "http://127.0.0.1:8099/api/events?after=999999999"
CRASH = re.compile(r"dspqueue|abort|segmentation|out of memory|fatal", re.I)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except Exception:  # noqa: BLE001
        return False


def git_info() -> dict:
    def g(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
        except OSError:
            return ""
    return {"commit": g("rev-parse", "--short", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(g("status", "--porcelain", "--untracked-files=no"))}


def chip(bb) -> tuple[float | None, float | None]:
    t = bb.chip_temperature()
    return t.get("npu_max_c"), t.get("cpu_max_c")


def cool_down(bb, cool_to: float, timeout_s: float) -> dict:
    t0 = time.time()
    npu, cpu = chip(bb)
    while npu is None or npu > cool_to:
        if time.time() - t0 > timeout_s:
            log(f"COOL-DOWN TIMED OUT after {timeout_s:.0f} s at NPU {npu} °C; running anyway (recorded)")
            return {"waited_s": round(time.time() - t0), "npu_c": npu, "cpu_c": cpu, "timed_out": True}
        log(f"cooling down: NPU {npu} °C, CPU {cpu} °C (target NPU <= {cool_to} °C)")
        time.sleep(15)
        npu, cpu = chip(bb)
    log(f"cool: NPU {npu} °C, CPU {cpu} °C after {time.time() - t0:.0f} s")
    return {"waited_s": round(time.time() - t0), "npu_c": npu, "cpu_c": cpu, "timed_out": False}


def start_server(bb, model: str, ctx: int, spec: bool) -> dict:
    from board.npu_proof import check_lane_log, placement
    bb.stop_lanes()
    info = bb.start_lane("npu", model, ctx=ctx, spec=spec)
    ready = bb.wait_health(f"http://127.0.0.1:{LLM_PORT}")
    if ready is None:
        raise RuntimeError("llama-server did not become healthy")
    problems = ["no log yet"]
    for _ in range(10):                      # the placement lines can land a moment after /health is ok
        logtxt = bb.lane_log("npu")
        problems = check_lane_log("npu", logtxt)
        if not problems:
            break
        time.sleep(1)
    if problems:
        raise RuntimeError("NPU placement not confirmed: " + "; ".join(problems))
    p = placement(logtxt)
    log(f"llama-server ready in {ready} s: {p['offloaded']}/{p['layers']} layers on HTP0, "
        f"{p['htp0_model_mib']} MiB, ctx {ctx}, spec {spec}")
    out = {"ready_after_s": ready, "placement": p, "command": info["command"], "model_sha256": info["model_sha256"],
           "ctx": ctx, "spec": spec}
    tm = os.environ.get("TYPE_MODEL")               # v7 type router: a second llama-server, also on the NPU
    if tm:
        ti = bb.start_lane("type", tm, ctx=ctx)
        if bb.wait_health(f"http://127.0.0.1:{bb.LANE_PORT['type']}") is None:
            raise RuntimeError("type model server did not become healthy")
        tp = ["no log yet"]
        for _ in range(10):
            tlog = bb.lane_log("type")
            tp = check_lane_log("npu", tlog)       # same rule as the main lane: every layer on HTP0
            if not tp:
                break
            time.sleep(1)
        if tp:
            raise RuntimeError("type model NPU placement not confirmed: " + "; ".join(tp))
        log(f"type model {tm} ready: {placement(tlog)}, swa_full {ti['swa_full']}")
        out["type_model"] = {"model": tm, "placement": placement(tlog), "command": ti["command"],
                             "model_sha256": ti["model_sha256"], "swa_full": ti["swa_full"]}
    return out


class TempSampler(threading.Thread):
    def __init__(self, bb, every: float = 10):
        super().__init__(daemon=True)
        self.bb, self.every, self.samples, self.stop = bb, every, [], threading.Event()

    def run(self):
        while not self.stop.is_set():
            npu, cpu = chip(self.bb)
            self.samples.append({"t": time.strftime("%H:%M:%S"), "npu_c": npu, "cpu_c": cpu})
            self.stop.wait(self.every)

    def summary(self) -> dict:
        n = [s["npu_c"] for s in self.samples if s["npu_c"] is not None]
        c = [s["cpu_c"] for s in self.samples if s["cpu_c"] is not None]
        return {"peak_npu_c": max(n) if n else None, "peak_cpu_c": max(c) if c else None,
                "samples": self.samples}


def run_eval(env: dict, split: str, no_fast_path: bool, transcript: Path) -> tuple[int, Path | None]:
    cmd = [sys.executable, str(ROOT / "eval" / "run_eval.py"), "--label", "BOARD", "--split", split]
    if no_fast_path:
        cmd.append("--no-fast-path")
    saved = None
    with transcript.open("w", encoding="utf-8") as out:
        p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            print("    " + line.rstrip(), flush=True)
            out.write(line)
            m = re.search(r"saved (.+\.json)\s*$", line)
            if m:
                saved = Path(m.group(1).strip())
        code = p.wait()
    return code, saved


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tag", required=True, help="configuration name for the table, e.g. v2")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--model", default="Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf")
    ap.add_argument("--ctx", type=int, default=None, help="server context size (default: board.board's)")
    ap.add_argument("--spec", action="store_true", help="copy-ahead speculative decoding (board.board --spec)")
    ap.add_argument("--no-fast-path", action="store_true", help="every turn to the LLM, as v1 did")
    ap.add_argument("--split", default="dev", choices=["dev"], help="dev only: the test split stays locked")
    ap.add_argument("--cool-to", type=float, default=50.0, help="NPU °C to wait for before each run")
    ap.add_argument("--cool-timeout", type=float, default=1200.0)
    ap.add_argument("--adb", default="", help="path to adb if it is not on PATH")
    ap.add_argument("--direct", action="store_true", help="don't route through the live monitor")
    ap.add_argument("--keep-server", action="store_true", help="leave llama-server running at the end")
    ap.add_argument("--out", default="", help="folder (default benchmarks/<date>_board_<SoC>)")
    a = ap.parse_args()

    if a.adb:
        os.environ["PATH"] = str(Path(a.adb).resolve().parent) + os.pathsep + os.environ.get("PATH", "")
    if not shutil.which("adb"):
        raise SystemExit("adb not found: put platform-tools on PATH or pass --adb")
    from board import board as bb
    ctx = a.ctx or bb.CTX_SIZE
    soc = bb._adb("shell", "getprop ro.soc.model").stdout.strip() or "unknown"
    out = Path(a.out) if a.out else ROOT / "benchmarks" / f"{time.strftime('%Y-%m-%d')}_board_{soc}"
    out.mkdir(parents=True, exist_ok=True)
    via_monitor = not a.direct and reachable(MONITOR_DASH)
    llm_url = MONITOR_LLM if via_monitor else f"http://127.0.0.1:{LLM_PORT}"
    log(f"board {soc} · tag {a.tag} · {a.runs} run(s) · ctx {ctx} · fast path {'off' if a.no_fast_path else 'on'}"
        f" · LLM via {'live monitor ' + llm_url + ' (watch http://127.0.0.1:8099)' if via_monitor else llm_url}")
    env = {**os.environ, "LLM": "llamaserver", "LLM_LANE": "npu", "LLM_MODEL": a.model, "LLM_URL": llm_url,
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    git = git_info()
    failures = 0
    # Run numbers continue from the runs already saved under this tag in this folder.
    first = 1 + sum(1 for f in out.glob("eval_BOARD_*.meta.json")
                    if json.loads(f.read_text(encoding="utf-8")).get("tag") == a.tag)
    for i in range(first, first + a.runs):
        log(f"===== run {i} ({a.tag}; {i - first + 1} of {a.runs} in this session) =====")
        bb.stop_lanes()
        cool = cool_down(bb, a.cool_to, a.cool_timeout)
        server = start_server(bb, a.model, ctx, a.spec)
        sampler = TempSampler(bb)
        sampler.start()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = f"eval_BOARD_{a.split}_{a.tag}_run{i}_{stamp}"
        t0 = time.time()
        code, saved = run_eval(env, a.split, a.no_fast_path, out / f"{base}.stdout.txt")
        duration = round(time.time() - t0)
        sampler.stop.set()
        sampler.join(timeout=20)
        alive = bool(bb._adb("shell", "pgrep llama-server").stdout.strip())
        crash_lines = [ln for ln in bb.lane_log("npu").splitlines() if CRASH.search(ln)]
        if saved and saved.is_file():
            shutil.copy2(saved, out / f"{base}.json")
        meta = {"tag": a.tag, "run": i, "label": "BOARD", "split": a.split, "soc": soc, "git": git,
                "model": a.model, "ctx": ctx, "spec": a.spec, "fast_path": not a.no_fast_path,
                "via_monitor": via_monitor, "llm_url": llm_url, "started": stamp, "duration_s": duration,
                "eval_exit_code": code, "result_file": f"{base}.json" if saved else None,
                "cool_down": cool, "server": server, "temperature": sampler.summary(),
                "server_alive_after": alive, "board_log_crash_lines": crash_lines[-10:]}
        (out / f"{base}.meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
        t = meta["temperature"]
        ok = code == 0 and saved is not None and alive and not crash_lines
        failures += not ok
        log(f"run {i}: {'OK' if ok else 'PROBLEM'} · {duration} s · peak NPU {t['peak_npu_c']} °C, "
            f"CPU {t['peak_cpu_c']} °C · server alive {alive} · crash lines {len(crash_lines)} · saved {base}.json")
    if not a.keep_server:
        bb.stop_lanes()
        log("llama-server stopped")
    from benchmarks import compare
    print()
    print(compare.table(compare.load([out])))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
