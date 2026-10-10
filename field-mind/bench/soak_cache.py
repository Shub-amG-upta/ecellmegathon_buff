"""
Soak test (2026-10-03): separate memory from heat as the cause of the campaign's
stalled 3B calls. Same 50 prompts, same order, campaign request body, on a fresh
3B NPU lane:  run A = current flags;  run B = current flags + --cache-ram 0.

  .venv/bin/python -m bench.soak_cache A|B   -> logs/soak_<run>.jsonl, prints the reading

Prompts: the first 50 distinct prompts of the crashed campaign job
B/round2/llama32-3b/dev_B01_tube_leak (ticks 51-81, 34 diagnostician + 16
verifier), in the order the campaign sent them, read from that job's call log.
They ARE real calls, so their sizes match real calls exactly.

Reading rule (human, fixed before the runs):
  CONFIRMED if run A stalls (a call over 120 s) or its prefill rate falls more
  than 20% from its first 5 calls, AND run B completes all 50 calls with no call
  over 60 s, prefill within 10% of its first 5 calls, and server RSS not growing.
  Anything else: stop and report.
"""
from __future__ import annotations

import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from bench import board

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "results/board_aborted_20261003/tmp/B__round2__llama32-3b__dev_B01_tube_leak.calls.jsonl"
MODEL = "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf"
N = 50
CLIENT_TIMEOUT_S = 300          # long enough to SEE a >120 s call; the campaign uses 120
STALL_S, B_MAX_S = 120.0, 60.0
A_DROP, B_BAND = 0.20, 0.10
IDLE_MARGIN_C = 5.0


def prompts() -> list[dict]:
    seen, out = set(), []
    for ln in SRC.read_text().splitlines():
        r = json.loads(ln)
        if r["prompt_sha256"] not in seen:
            seen.add(r["prompt_sha256"])
            out.append({"prompt": r["prompt"], "role": r["role"], "tick": r["tick"],
                        "sha": r["prompt_sha256"], "max_tokens": r["sampling"]["max_tokens"]})
    return out[:N]


def board_sh(cmd: str) -> str:
    return board._adb("shell", cmd, timeout=60).stdout.strip()


def probe() -> dict:
    """Server RSS, board MemAvailable, server prompt-cache size, chip temperature."""
    pid = board_sh("pidof llama-server")
    rss = board_sh(f"grep VmRSS /proc/{pid}/status") if pid else ""
    mem = board_sh("grep MemAvailable /proc/meminfo")
    cache = board_sh(f"grep 'cache state' {board.lane_log_path('npu')} | tail -1")
    t = board.chip_temperature()

    def kb(s):
        p = s.split()
        return int(p[1]) if len(p) >= 2 and p[1].isdigit() else None
    c = None
    if "cache state" in cache:
        seg = cache.split("cache state:")[1].split("(")[0]          # " 33 prompts, 6797.624 MiB "
        c = {"prompts": int(seg.split()[0]), "mib": float(seg.split()[2])}
    return {"server_rss_kb": kb(rss), "mem_available_kb": kb(mem), "prompt_cache": c,
            "server_alive": bool(pid), "cpu_c": t.get("cpu_max_c"), "npu_c": t.get("npu_max_c"),
            "t": board.laptop_time()}


def chat(p: dict) -> tuple[dict | None, float, str]:
    # the campaign's request body (bench.campaign.CampaignBackend._body, 3B: no extra body)
    body = {"messages": [{"role": "user", "content": p["prompt"]}], "max_tokens": p["max_tokens"],
            "temperature": 0.0, "seed": 0, "cache_prompt": False, "stream": False}
    req = urllib.request.Request("http://localhost:8080/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=CLIENT_TIMEOUT_S) as r:
            return json.loads(r.read().decode()), time.perf_counter() - t0, ""
    except (TimeoutError, socket.timeout):
        return None, time.perf_counter() - t0, f"client timeout {CLIENT_TIMEOUT_S}s"
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        return None, time.perf_counter() - t0, f"error: {e}"


def idle_now() -> float:
    """Idle temperature with no lane running: stable to 0.5 C (max ~5 min)."""
    board.stop_lanes()
    prev = None
    for _ in range(20):
        t = board.chip_temperature()
        cur = max(t["cpu_max_c"], t["npu_max_c"])
        if prev is not None and abs(cur - prev) < 0.5:
            return cur
        prev = cur
        time.sleep(15)
    return cur


def run(name: str) -> dict:
    ps = prompts()
    assert len(ps) == N and len({p["sha"] for p in ps}) == N
    idle = idle_now()
    waited = 0
    while True:                                      # chip within 5 C of idle, no lane running
        t = board.chip_temperature()
        if max(t["cpu_max_c"], t["npu_max_c"]) <= idle + IDLE_MARGIN_C:
            break
        time.sleep(15)
        waited += 15
    sha = board.check_model(MODEL)
    cmd = board.lane_command("npu", MODEL) + (" --cache-ram 0" if name == "B" else "")
    board._adb("shell", f"mkdir -p {board.DEVICE_LOGS}")
    board._adb("shell", f"nohup sh -c '{cmd}' > {board.lane_log_path('npu')} 2>&1 &")
    board._adb("forward", "tcp:8080", "tcp:8080")
    ready = board.wait_health("http://localhost:8080", 300)
    head = {"run": name, "command": cmd, "model_sha256": sha, "idle_c": idle, "thermal_wait_s": waited,
            "ready_after_s": ready, "start": probe(), "prompt_source": str(SRC.relative_to(ROOT))}
    out = ROOT / f"logs/soak_{name}.jsonl"
    with open(out, "x") as fh:
        fh.write(json.dumps({"header": head}) + "\n")
        fh.flush()
        for i, p in enumerate(ps):
            d, wall, err = chat(p)
            tm = (d or {}).get("timings") or {}
            rec = {"i": i, "tick": p["tick"], "role": p["role"], "sha": p["sha"][:12], "wall_s": round(wall, 2),
                   "error": err, "prompt_n": tm.get("prompt_n"), "predicted_n": tm.get("predicted_n"),
                   "prefill_tok_s": round(tm["prompt_n"] / tm["prompt_ms"] * 1000, 1) if tm.get("prompt_ms") else None,
                   "decode_tok_s": round(tm["predicted_n"] / tm["predicted_ms"] * 1000, 2) if tm.get("predicted_ms") else None,
                   "stop": ((d or {}).get("choices") or [{}])[0].get("finish_reason"), **probe()}
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(json.dumps(rec), flush=True)
            if err:
                break                                # the server is stuck or gone: later calls would queue
    board.stop_lanes()
    return reading(name)


def load(name: str) -> list[dict]:
    return [json.loads(ln) for ln in (ROOT / f"logs/soak_{name}.jsonl").read_text().splitlines()][1:]


def stats(rows: list[dict]) -> dict:
    pre = [r["prefill_tok_s"] for r in rows if r["prefill_tok_s"]]
    base = sum(pre[:5]) / 5 if len(pre) >= 5 else None
    worst = min(pre) if pre else None
    rss = [r["server_rss_kb"] for r in rows if r["server_rss_kb"]]
    return {"calls_ok": sum(1 for r in rows if not r["error"]), "max_wall_s": max(r["wall_s"] for r in rows),
            "first5_prefill": round(base, 1) if base else None, "min_prefill": worst,
            "max_prefill_drop": round(1 - worst / base, 3) if base and worst else None,
            "rss_first_mb": rss[0] // 1024 if rss else None, "rss_last_mb": rss[-1] // 1024 if rss else None,
            "rss_max_mb": max(rss) // 1024 if rss else None}


def reading(name: str) -> dict:
    s = stats(load(name))
    print(f"run {name}: {s}")
    return s


def verdict() -> str:
    a, b = stats(load("A")), stats(load("B"))
    a_bad = a["max_wall_s"] > STALL_S or a["calls_ok"] < N or (a["max_prefill_drop"] or 0) > A_DROP
    # "RSS not growing": last reading within 2% of the first (load-time noise), never above the first by more
    b_ok = (b["calls_ok"] == N and b["max_wall_s"] <= B_MAX_S and (b["max_prefill_drop"] or 1) <= B_BAND
            and b["rss_max_mb"] <= b["rss_first_mb"] * 1.02)
    return "CONFIRMED" if a_bad and b_ok else "NOT CONFIRMED: stop and report"


if __name__ == "__main__":
    board.drop_pythonpath()
    if sys.argv[1] in ("A", "B"):
        run(sys.argv[1])
    else:
        print(verdict())
