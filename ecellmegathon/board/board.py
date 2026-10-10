"""
QIDK helpers, host side: shells out to adb and talks HTTP to llama-server.

Ported from FieldMind (bench/board.py), where every function here ran against
the same board (SM8650). Dropped: the multi-lane campaign helpers.

  python -m board.board start npu <model.gguf> [--ctx 2048] [--spec] [--cool-cpu]   start llama-server on HTP0
                                                 (--spec: copy-ahead speculative decoding, to compare)
  python -m board.board start cpu <model.gguf>   same model on the CPU, :8081 (comparison only)
  python -m board.board check npu                prove NPU placement from the startup log
  python -m board.board speed http://localhost:8080 [prompt_file]
  python -m board.board temp
  python -m board.board log npu
  python -m board.board stop

Host rules (each one cost FieldMind a debugging session):
  * PYTHONPATH is unset for every adb subprocess (a QAIRT SDK install exports
    one that shadows imports). drop_pythonpath() re-executes without it.
  * Timestamps are LAPTOP time. The board's clock read 2023 in October 2026.
  * LD_LIBRARY_PATH and ADSP_LIBRARY_PATH on every launch, or the server
    quietly runs on the CPU.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request


def clean_env() -> dict:
    """The host environment minus PYTHONPATH; pass as env= to every subprocess."""
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def drop_pythonpath() -> None:
    if "PYTHONPATH" in os.environ:
        os.execve(sys.executable, [sys.executable] + sys.orig_argv[1:], clean_env())


def laptop_time() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---- where things live on the board -----------------------------------------
# The pinned llama.cpp Android package (tag b11371, commit 99b95488) and the
# GGUFs, as pushed for FieldMind. See docs/reference/phase0b_board_setup.md.
DEVICE_PKG = "/data/local/tmp/llm/llama.cpp"
DEVICE_MODELS = "/data/local/tmp/llm"
DEVICE_LOGS = "/data/local/tmp/llm/logs"

# HUMAN DECISION 2026-10-10: -c 2048 (FieldMind used 4096 for ~2,500-token prompts).
# Our largest call is ~960 tokens with the answer (ESTIMATE, chars/3.5, longest eval
# turn), so 2048 leaves ~2x headroom. 1024 was considered and left out: the worst
# case (every field at its limit + a long sentence) is ~1,260. --ctx overrides it.
CTX_SIZE = 2048
N_PARALLEL = 1
LANE_PORT = {"npu": 8080, "cpu": 8081, "type": 8084}      # "type": v7's type model, also on the NPU

# Copy-ahead (prompt-lookup) speculative decoding, off by default. Most of our answer is copied from the
# user's own words, so llama-server can draft the next tokens by matching the last N generated tokens
# against the prompt, then check up to M drafted tokens in one batch on the NPU. N and M are starting
# values to tune on the board (DESIGN CHOICE: our copied spans are short, 3-15 tokens). UNVERIFIED on
# HTP0 and with grammar-constrained sampling; start_lane checks the board's llama-server has the flags
# (they were renamed in recent llama.cpp builds), then measure decode tok/s and field accuracy.
SPEC_FLAGS = ("--spec-type ngram-simple --spec-ngram-simple-size-n {n} --spec-ngram-simple-size-m {m}")
SPEC_N, SPEC_M = 3, 8

# Cooler CPU (COOL_CPU=1 or --cool-cpu), off by default until measured. On the NPU lane the CPU only samples
# tokens and checks the grammar, but llama-server starts a thread per core and busy-polls them (--poll 50):
# in every board run the CPU ran as hot as the NPU (~104 °C) and the board rebooted once at 105.8 °C.
# 2 threads and no polling should cut CPU heat; it may add a little per-token delay. Measure CPU °C,
# decode tok/s and accuracy against the same run without it (eval/DECISION_v4_v5.md).
COOL_CPU_FLAGS = "-t 2 -tb 2 --poll 0"

# The only model files a lane may load, built by board/build_candidate_gguf.sh
# (every matrix Q4_0, token embedding and output Q8_0). start_lane() checks the
# sha256 ON THE BOARD before launching. Add a row for any new model you build.
CANDIDATES = {
    "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf":
        "5aa3ece50ab33d09a7181888a75f8755f924c662dc99626e7f45440adfeadcdb",
    "Qwen3-1.7B-Q4_0-pure-embq8.gguf":
        "4a4ebf10354822c45dfa38b9248a42a59ebae53c0ad992147b881dd7ed09c56c",
    "gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf":
        "3a229fece56839877093042f0699939d9c4a65691dda81999f80ff27dae2cc5f",
    "Qwen2.5-0.5B-Instruct-Q4_0-pure-embq8.gguf":
        "00d3bb3f9210f132ef246cc5db2a7d8c9f8b63785679a95aef3558875b50e341",
    # unsloth/gemma-3-4b-it @ bf46152c47f5, llama.cpp 99b95488, --override-kv tokenizer.ggml.eos_token_id=int:1
    "gemma-3-4b-it-Q4_0-pure-embq8.gguf":
        "ff8946f75978c5796ec1f752585eac34e394f4737c0954995943754c8f5677df",
}


class ModelNotAllowed(RuntimeError):
    pass


def _adb(*args: str, serial: str = "", timeout: float = 60) -> subprocess.CompletedProcess:
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env=clean_env())


def board_sha256(model_file: str, serial: str = "") -> str:
    out = _adb("shell", f"sha256sum {DEVICE_MODELS}/{model_file}", serial=serial,
               timeout=300).stdout.split()
    return out[0] if out else ""


def check_model(model_file: str, serial: str = "") -> str:
    """Raise unless model_file is a known candidate AND its sha256 on the board
    equals the recorded one. Returns the sha256."""
    want = CANDIDATES.get(model_file)
    if want is None:
        raise ModelNotAllowed(f"{model_file!r} is not in board.board.CANDIDATES")
    got = board_sha256(model_file, serial)
    if got != want:
        raise ModelNotAllowed(f"{model_file}: board sha256 {got or '(missing)'} != {want}")
    return got


def lane_command(lane: str, model_file: str, threads: int = 6,
                 log_level: int | None = 4, ctx: int = CTX_SIZE, spec: bool = False,
                 cool_cpu: bool = False, swa_full: bool = False) -> str:
    """The board-side shell command for one llama-server (no redirection, no &).

    -fit off      the server may not change the context or the offload itself
    --cache-ram 0 without it the host prompt cache grew ~240 MiB per call and
                  board memory ran out after ~36 calls (FieldMind soak test)
    -lv 4         prints "offloaded N/N layers" and the HTP0 buffer sizes that
                  npu_proof reads; measured cost under 2%
    """
    if lane in ("npu", "type"):
        offload = "--device HTP0 -ngl 99"
    elif lane == "cpu":
        offload = f"--device none -ngl 0 -t {threads}"
    else:
        raise ValueError(f"unknown lane {lane!r}")
    return (f"cd {DEVICE_PKG} && LD_LIBRARY_PATH={DEVICE_PKG}/lib "
            f"ADSP_LIBRARY_PATH={DEVICE_PKG}/lib ./bin/llama-server "
            f"-m {DEVICE_MODELS}/{model_file} --host 0.0.0.0 --port {LANE_PORT[lane]} "
            f"-c {ctx} -np {N_PARALLEL} {offload} -fit off --cache-ram 0"
            + (" " + SPEC_FLAGS.format(n=SPEC_N, m=SPEC_M) if spec else "")
            + (" " + COOL_CPU_FLAGS if cool_cpu and lane in ("npu", "type") else "")
            # Gemma 3 uses sliding-window attention: without --swa-full llama-server doesn't reuse the cached
            # prompt (board: 660 ms prefill per call instead of ~100 ms)
            + (" --swa-full" if swa_full else "")
            + (f" -lv {log_level}" if log_level is not None else ""))


def lane_log_path(lane: str) -> str:
    return f"{DEVICE_LOGS}/lane_{lane}.log"


def server_has_flag(flag: str, serial: str = "") -> bool:
    """Does the board's llama-server know this flag? (reads its --help)"""
    out = _adb("shell", f"cd {DEVICE_PKG} && LD_LIBRARY_PATH={DEVICE_PKG}/lib ./bin/llama-server --help 2>&1",
               serial=serial, timeout=60).stdout
    return flag in out


def start_lane(lane: str, model_file: str, threads: int = 6, serial: str = "",
               log_level: int | None = 4, ctx: int = CTX_SIZE, spec: bool = False,
               cool_cpu: bool | None = None) -> dict:
    """Start llama-server in the background on the board and forward its port."""
    sha = check_model(model_file, serial)
    if not 512 <= ctx <= 32768:
        raise ValueError(f"context size {ctx} out of range")
    if spec and not server_has_flag("--spec-ngram-simple-size-n", serial):
        raise RuntimeError("the board's llama-server has no --spec-type ngram-simple flags "
                           "(older build); start without SPEC=1")
    if cool_cpu is None:                                  # COOL_CPU=1 reaches run_bench.py's start_lane too
        cool_cpu = os.environ.get("COOL_CPU", "0") == "1"
    if cool_cpu and not server_has_flag("--poll", serial):
        raise RuntimeError("the board's llama-server has no --poll flag; start without COOL_CPU=1")
    swa_full = "gemma" in model_file.lower() and server_has_flag("--swa-full", serial)
    cmd = lane_command(lane, model_file, threads, log_level, ctx, spec, cool_cpu, swa_full)
    port = LANE_PORT[lane]
    _adb("shell", f"mkdir -p {DEVICE_LOGS}", serial=serial)
    _adb("shell", f"nohup sh -c '{cmd}' > {lane_log_path(lane)} 2>&1 &", serial=serial)
    _adb("forward", f"tcp:{port}", f"tcp:{port}", serial=serial)
    return {"lane": lane, "started_laptop_time": laptop_time(), "command": cmd, "ctx": ctx, "spec": spec,
            "cool_cpu": cool_cpu, "swa_full": swa_full,
            "model_file": model_file, "model_sha256": sha,
            "board_log": lane_log_path(lane), "url": f"http://localhost:{port}"}


def stop_lanes(serial: str = "") -> str:
    """Stop llama-server by process name only. Never deletes files."""
    return _adb("shell", "pkill llama-server; sleep 1; pgrep -l llama-server",
                serial=serial).stdout.strip()


def lane_log(lane: str, serial: str = "") -> str:
    return _adb("shell", f"cat {lane_log_path(lane)}", serial=serial).stdout


def lane_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=5) as r:
            return json.loads(r.read().decode()).get("status") == "ok"
    except Exception:
        return False


def wait_health(url: str, timeout_s: float = 300) -> float | None:
    """Seconds until /health is ok, or None on timeout (laptop wall clock)."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        if lane_health(url):
            return round(time.perf_counter() - t0, 1)
        time.sleep(2)
    return None


# ---- chip temperature -------------------------------------------------------
# CPU clusters are cpu*/cpuss*; the Hexagon NPU shows as nsp*, cdsp* or q6*.
_CPU_KEYS = ("cpu",)
_NPU_KEYS = ("nsp", "cdsp", "q6", "npu")
_ZONE_CMD = ('for z in /sys/class/thermal/thermal_zone*; do '
             'echo "$(cat $z/type 2>/dev/null) $(cat $z/temp 2>/dev/null)"; done')


def _to_c(v: int) -> float:
    # sysfs reports millidegrees C; a few vendor zones report degrees.
    return v / 1000.0 if abs(v) >= 1000 else float(v)


def parse_zones(text: str) -> list[tuple[str, float]]:
    out = []
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) != 2:
            continue
        try:
            out.append((parts[0], _to_c(int(parts[1]))))
        except ValueError:
            continue
    return out


def summarise(zones: list[tuple[str, float]]) -> dict:
    def mx(keys):
        v = [t for name, t in zones if any(k in name.lower() for k in keys)
             and -20.0 < t < 150.0]
        return round(max(v), 1) if v else None
    return {"t": laptop_time(), "cpu_max_c": mx(_CPU_KEYS), "npu_max_c": mx(_NPU_KEYS),
            "n_zones": len(zones)}


# Android's own thermal status (dumpsys thermalservice): 0 NONE … 6 SHUTDOWN. Logged next to the
# temperatures to see when the system throttles, and to tell a thermal shutdown from other reboots.
THERMAL_STATUS = ["NONE", "LIGHT", "MODERATE", "SEVERE", "CRITICAL", "EMERGENCY", "SHUTDOWN"]


def parse_thermal_status(text: str) -> int | None:
    m = re.search(r"(?:Current\s+)?[Tt]hermal\s+[Ss]tatus:?\s*(\d)", text)
    return int(m.group(1)) if m else None


def chip_temperature(serial: str = "", raw: bool = False) -> dict:
    try:
        p = _adb("shell", _ZONE_CMD, serial=serial, timeout=20)
        st = _adb("shell", "dumpsys thermalservice 2>/dev/null | head -20", serial=serial, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}
    zones = parse_zones(p.stdout)
    out = summarise(zones)
    status = parse_thermal_status(st.stdout)
    out["thermal_status"] = status
    out["thermal_status_name"] = THERMAL_STATUS[status] if status is not None and status < 7 else None
    if raw:
        out["zones"] = zones
    return out


# ---- one timed call ---------------------------------------------------------
def lane_speed(url: str, prompt: str, max_tokens: int = 64) -> dict:
    """One call; prefill/decode tok/s from the server's own `timings` block."""
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 0.0, "seed": 0, "cache_prompt": False, "stream": False}
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode())
    wall = (time.perf_counter() - t0) * 1000
    tm = data.get("timings") or {}
    out = {"url": url, "model": data.get("model"),
           "prompt_tokens": tm.get("prompt_n"), "decode_tokens": tm.get("predicted_n"),
           "prefill_tok_s": tm.get("prompt_per_second"),
           "decode_tok_s": tm.get("predicted_per_second"),
           "prefill_ms_server": tm.get("prompt_ms"), "wall_ms": round(wall, 1)}
    out["missing"] = [k for k, v in out.items() if v is None]
    return out


if __name__ == "__main__":
    drop_pythonpath()
    a = sys.argv[1:]
    if len(a) >= 3 and a[0] == "start":
        ctx = int(a[a.index("--ctx") + 1]) if "--ctx" in a else CTX_SIZE
        info = start_lane(a[1], a[2], ctx=ctx, spec="--spec" in a, cool_cpu=True if "--cool-cpu" in a else None)
        info["ready_after_s"] = wait_health(info["url"])
        print(json.dumps(info, indent=1))
    elif len(a) >= 2 and a[0] == "check":
        from board.npu_proof import check_lane_log
        problems = check_lane_log(a[1], lane_log(a[1]))
        print("OK: placement confirmed" if not problems else "\n".join(problems))
        sys.exit(1 if problems else 0)
    elif a[:1] == ["stop"]:
        print(json.dumps({"laptop_time": laptop_time(), "still_running": stop_lanes()}))
    elif len(a) >= 2 and a[0] == "log":
        print(lane_log(a[1]))
    elif a[:1] == ["temp"]:
        print(json.dumps(chip_temperature(raw=True), indent=1))
    elif len(a) >= 2 and a[0] == "speed":
        pr = open(a[2]).read() if len(a) > 2 else "Say hello in five words."
        print(json.dumps(lane_speed(a[1], pr), indent=1))
    else:
        print(__doc__)
