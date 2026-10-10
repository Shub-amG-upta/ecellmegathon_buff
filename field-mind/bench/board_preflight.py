#!/usr/bin/env python3
"""
Preflight and model checks for bench/board_session.sh (accuracy-fix work).

    .venv/bin/python -m bench.board_preflight lanes OUT.json
        start BOTH lanes with Llama 3.2 3B (NPU :8080, CPU :8081), confirm each
        answers a trivial prompt with the expected model on the expected
        backend, check placement from each server's own startup log, log
        /proc/meminfo and each llama-server's RSS (non-fatal: a warning and
        null when they cannot be read); stop the lanes. Exit 0 only when the
        model, lane, placement and HTP0 checks hold; else print them, exit 1.

    .venv/bin/python -m bench.board_preflight runs multi|single RUN.json.gz...
        every model call in saved runs must report the expected model and the
        lane its agent is placed on (multi: diagnosticians npu, verifier and
        text reader cpu; single: npu). Catches a lane that silently served
        another model or backend during a run. Exit 1 on any mismatch.

The pure checks (check_reply, check_lane_log, parse_meminfo, parse_rss,
check_run_models) are unit-tested in tests/test_board_preflight.py.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")

LLAMA = "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf"
LANES = {"npu": "http://localhost:8080", "cpu": "http://localhost:8081"}
# fixed placement of configs/accuracy.yaml; the single agent uses the NPU lane
MULTI_LANE = {"diagnostician": "npu", "verifier": "cpu", "text_reader": "cpu"}
TRIVIAL = "Reply with the single word OK."


def check_reply(reply, lane: str, model: str = LLAMA) -> list[str]:
    """A trivial call's LLMReply: answered, by the expected model, on the lane."""
    p = []
    if reply.status != "ok":
        p.append(f"{lane}: call status {reply.status} ({getattr(reply, 'error', '')})")
    if not (reply.text or "").strip():
        p.append(f"{lane}: empty answer")
    if Path(str(reply.model or "")).name != model:
        p.append(f"{lane}: server reports model {reply.model!r}, expected {model}")
    if reply.backend != lane:
        p.append(f"{lane}: reply labelled backend {reply.backend!r}")
    return p


def check_lane_log(lane: str, log: str) -> list[str]:
    """The server's own startup log. NPU: every layer offloaded and a nonzero
    HTP0 buffer. CPU: no layer offloaded and no HTP0 buffer. Both: the model
    path loaded is the expected file."""
    p = []
    m = re.search(r"offloaded (\d+)/(\d+) layers", log)
    htp = re.search(r"HTP0 model buffer size =\s*([0-9.]+)", log)
    if LLAMA not in log:
        p.append(f"{lane}: startup log does not name {LLAMA}")
    if m is None:
        p.append(f"{lane}: no 'offloaded N/M layers' line in the startup log")
    elif lane == "npu" and (m.group(1) != m.group(2) or not htp or float(htp.group(1)) <= 0):
        p.append(f"{lane}: not fully on HTP0 (offloaded {m.group(1)}/{m.group(2)}, "
                 f"HTP0 buffer {htp.group(1) if htp else 'none'})")
    elif lane == "cpu" and (m.group(1) != "0" or htp):
        p.append(f"{lane}: CPU lane has offloaded layers or an HTP0 buffer")
    return p


def parse_meminfo(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemFree", "MemAvailable", "SwapTotal", "SwapFree"):
            out[k + "_kB"] = int(v.split()[0])
    return out


def parse_rss(text: str) -> dict:
    """Lines 'PORT VmRSS: N kB' -> {port: rss_kB}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s+VmRSS:\s+(\d+)\s+kB", line)
        if m:
            out[int(m.group(1))] = int(m.group(2))
    return out


def collect_memory(adb) -> tuple[dict | None, dict | None, list[str]]:
    """Free memory and each llama-server's RSS, NON-FATAL: a board shell
    without pidof or a readable /proc gives None and a warning, never a failed
    preflight. `adb(cmd)` returns the shell's stdout and may raise."""
    warnings, meminfo, rss = [], None, None
    try:
        meminfo = parse_meminfo(adb("cat /proc/meminfo")) or None
    except Exception as e:                                  # noqa: BLE001
        warnings.append(f"meminfo not read: {e}")
    if meminfo is None or "MemAvailable_kB" not in meminfo:
        warnings.append("MemAvailable not found in /proc/meminfo")
    try:
        rss = parse_rss(adb("for p in $(pidof llama-server); do "
                            "echo $(tr '\\0' ' ' < /proc/$p/cmdline | sed -n 's/.*--port \\([0-9]*\\).*/\\1/p') "
                            "$(grep VmRSS /proc/$p/status); done")) or None
    except Exception as e:                                  # noqa: BLE001
        warnings.append(f"RSS not read: {e}")
    if rss is None:
        warnings.append("no llama-server RSS read (pidof or /proc unavailable?)")
    else:
        for lane, port in (("npu", 8080), ("cpu", 8081)):
            if port not in rss:
                warnings.append(f"{lane}: no RSS for a llama-server on port {port}")
    return meminfo, rss, warnings


def check_run_models(run: dict, arch: str, model: str = LLAMA) -> list[str]:
    """Every model call in a saved run: expected model, expected lane."""
    p = []
    for a in run["assessments"]:
        envs = list(a.get("envelopes", []))
        envs += [t["envelope"] for t in (a.get("multi") or {}).get("text", [])]
        for e in envs:
            want = "npu" if arch == "single" else MULTI_LANE.get(e["agent"])
            calls = e.get("calls") or [{}]
            if Path(str(e.get("model") or "")).name != model:
                p.append(f"t{a['tick']} {e['agent']}: model {e.get('model')!r}")
            if e.get("backend") != want:
                p.append(f"t{a['tick']} {e['agent']}: backend {e.get('backend')!r}, expected {want}")
            if any(c.get("status") not in (None, "ok") for c in calls):
                p.append(f"t{a['tick']} {e['agent']}: a call failed ({[c.get('status') for c in calls]})")
    return p


def _load(path: str) -> dict:
    d = json.loads(gzip.decompress(Path(path).read_bytes()))
    d = d[0] if isinstance(d, list) else d
    return d["runs"][0] if "runs" in d else d


def preflight(out: str) -> int:
    from bench import board
    from fieldmind.runtime.llm_backend import LlamaServerBackend
    rec, problems = {"laptop_time": board.laptop_time(), "model": LLAMA, "lanes": {}}, []
    board.stop_lanes()
    try:
        for lane, url in LANES.items():
            try:
                info = board.start_lane(lane, LLAMA)
            except board.ModelNotAllowed as e:
                problems.append(f"{lane}: {e}")
                continue
            info["ready_after_s"] = board.wait_health(url)
            if info["ready_after_s"] is None:
                problems.append(f"{lane}: llama-server never became healthy at {url}")
            rec["lanes"][lane] = info
        for lane, url in LANES.items():
            if lane not in rec["lanes"] or rec["lanes"][lane]["ready_after_s"] is None:
                continue
            log = board.lane_log(lane)
            problems += check_lane_log(lane, log)
            r = LlamaServerBackend(url=url, lane=lane, model_file=LLAMA,
                                   timeout_s=120).generate(TRIVIAL, max_tokens=8)
            problems += check_reply(r, lane)
            rec["lanes"][lane].update(answer=r.text, reply_model=r.model, reply_backend=r.backend,
                                      status=r.status, latency_ms=round(r.latency_ms, 1))
        rec["meminfo"], rec["rss"], rec["warnings"] = collect_memory(
            lambda cmd: board._adb("shell", cmd).stdout)
        for w in rec["warnings"]:
            print(f"preflight WARNING (not fatal): {w}", file=sys.stderr)
    finally:
        board.stop_lanes()
    rec["problems"] = problems
    Path(out).write_text(json.dumps(rec, indent=1) + "\n")
    m = rec.get("meminfo") or {}
    print(f"preflight: MemAvailable {m.get('MemAvailable_kB')} kB of {m.get('MemTotal_kB')} kB; "
          f"RSS by port {rec.get('rss')}")
    for lane, i in rec["lanes"].items():
        print(f"  {lane}: ready after {i.get('ready_after_s')} s, answered {i.get('answer')!r} "
              f"as {Path(str(i.get('reply_model'))).name} on {i.get('reply_backend')}")
    if problems:
        print("PREFLIGHT FAILED:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print("preflight: both lanes OK")
    return 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "lanes":
        from bench.board import drop_pythonpath
        drop_pythonpath()
        return preflight(sys.argv[2])
    if len(sys.argv) >= 4 and sys.argv[1] == "runs":
        bad = 0
        for f in sys.argv[3:]:
            p = check_run_models(_load(f), sys.argv[2])
            bad += bool(p)
            print(f"{Path(f).name}: {'OK' if not p else f'{len(p)} problems'}")
            for x in p[:5]:
                print("   ", x)
        return 1 if bad else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
