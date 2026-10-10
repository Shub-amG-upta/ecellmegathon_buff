"""
Board helpers for runs that use the QIDK's llama-server lanes (Phase 0b).
Host side only: shells out to adb and talks HTTP to the forwarded lanes.

  chip_temperature()       every thermal zone (type, degC), plus the max over
                           CPU-named and NPU-named zones. Zones are discovered on
                           the board, not assumed.
  lane_health(url)         True when llama-server's /health says ok.
  lane_speed(url, prompt)  one call; prefill/decode tok/s from the server's own
                           `timings` block (never estimated).
  decode_rate_wallclock()  decode tok/s from CLIENT wall clock by differencing
                           two calls that differ only in max_tokens: an
                           independent route to the same quantity, used to
                           re-derive the server's predicted_per_second.

  .venv/bin/python -m bench.board temp
  .venv/bin/python -m bench.board speed http://localhost:8080 [prompt_file]
  .venv/bin/python -m bench.board env      (what the board scripts run with)
  .venv/bin/python -m bench.board start npu <model.gguf>   (also: cpu)
  .venv/bin/python -m bench.board log npu
  .venv/bin/python -m bench.board stop

Two host rules for every board script (bench.board, bench.probe_device):
  * PYTHONPATH is unset. This laptop exports PYTHONPATH into the QAIRT SDK's
    python folder, which can shadow or satisfy imports the venv should own.
    drop_pythonpath() re-executes the interpreter without it; clean_env() is
    the environment handed to every adb subprocess.
  * Timestamps are LAPTOP time (laptop_time()). The board's clock is wrong
    (it read 2023-09-20 on 2026-10-03), so board time is never logged.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request


def clean_env() -> dict:
    """The host environment minus PYTHONPATH; pass as env= to every subprocess."""
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def drop_pythonpath() -> None:
    """Re-execute this interpreter without PYTHONPATH if it is set. Call first
    thing in a board script's __main__; a no-op once it is unset."""
    if "PYTHONPATH" in os.environ:
        os.execve(sys.executable, [sys.executable] + sys.orig_argv[1:], clean_env())


def laptop_time() -> str:
    """ISO local time with UTC offset, from the laptop's clock."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")

# Qualcomm thermal zone type names: CPU clusters are cpu*/cpuss*; the Hexagon
# NPU/DSP shows as nsp*, cdsp*, q6* depending on the kernel. Recorded raw too.
_CPU_KEYS = ("cpu",)
_NPU_KEYS = ("nsp", "cdsp", "q6", "npu")

_ZONE_CMD = ('for z in /sys/class/thermal/thermal_zone*; do '
             'echo "$(cat $z/type 2>/dev/null) $(cat $z/temp 2>/dev/null)"; done')


# ---- llama-server lanes -----------------------------------------------------
# Where the pinned llama.cpp Android package and the GGUFs live on the board
# (pushed in the Phase 0b board setup; reports/phase0b_board_setup.md).
DEVICE_PKG = "/data/local/tmp/llm/llama.cpp"
DEVICE_MODELS = "/data/local/tmp/llm"
DEVICE_LOGS = "/data/local/tmp/llm/logs"

# HUMAN DECISION 2026-10-03: llama-server is ALWAYS launched with -c 4096 -np 1,
# the same for every model and both lanes. The single-agent prompt is ~2484
# tokens and context size changes NPU speed, so it is fixed, never per-model.
CTX_SIZE = 4096
N_PARALLEL = 1

LANE_PORT = {"npu": 8080, "cpu": 8081}

# Log level for timed calls. RULE (human, fixed before the measurement): if
# -lv 4 lowers prefill or decode tok/s by more than 5% (calls 2-3 of each
# launch), use it only for a one-off offload-confirmation launch per model;
# otherwise keep -lv 4 everywhere. MEASURED 2026-10-03 on the pure 3B, NPU lane
# (logs/smoke_C.json): prefill 898.6 vs 899.4 tok/s (-0.09%), decode 16.23 vs
# 16.01 tok/s (+1.37%). Neither is lowered by more than 5%: -lv 4 EVERYWHERE.
TIMED_LOG_LEVEL: int | None = 4

# The only model files a lane may load: the Phase 0b candidates, built by
# bench/build_candidate_gguf.sh (every matrix Q4_0, token embedding and output
# Q8_0). start_lane() checks the file's sha256 ON THE BOARD against this table
# before launching, so the old impure file still on the board can never be
# loaded by accident. Values: reports/phase0b_board_setup.md.
CANDIDATES = {
    "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf":
        "5aa3ece50ab33d09a7181888a75f8755f924c662dc99626e7f45440adfeadcdb",
    "Qwen3-1.7B-Q4_0-pure-embq8.gguf":
        "4a4ebf10354822c45dfa38b9248a42a59ebae53c0ad992147b881dd7ed09c56c",
    "gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf":
        "3a229fece56839877093042f0699939d9c4a65691dda81999f80ff27dae2cc5f",
    "Qwen2.5-0.5B-Instruct-Q4_0-pure-embq8.gguf":
        "00d3bb3f9210f132ef246cc5db2a7d8c9f8b63785679a95aef3558875b50e341",
}


class ModelNotAllowed(RuntimeError):
    pass


def board_sha256(model_file: str, serial: str = "") -> str:
    out = _adb("shell", f"sha256sum {DEVICE_MODELS}/{model_file}", serial=serial,
               timeout=300).stdout.split()
    return out[0] if out else ""


def check_model(model_file: str, serial: str = "") -> str:
    """Raise unless model_file is a known candidate AND its sha256 on the board
    equals the recorded one. Returns the sha256."""
    want = CANDIDATES.get(model_file)
    if want is None:
        raise ModelNotAllowed(f"{model_file!r} is not in bench.board.CANDIDATES")
    got = board_sha256(model_file, serial)
    if got != want:
        raise ModelNotAllowed(f"{model_file}: board sha256 {got or '(missing)'} != {want}")
    return got


def lane_command(lane: str, model_file: str, threads: int = 6,
                 log_level: int | None = 4) -> str:
    """The board-side shell command for one lane (no redirection, no &).
    LD_LIBRARY_PATH and ADSP_LIBRARY_PATH both point at the package's lib/
    folder: it holds libggml-hexagon.so (host side) and libggml-htp-v75.so (the
    code the DSP loads; without ADSP_LIBRARY_PATH the NPU lane cannot start it).
    -fit off: nothing is silently adjusted to "fit" (HTP0 reports 0 MiB free).
    --cache-ram 0: HUMAN DECISION 2026-10-03, every lane, every model. The
    server's host prompt cache (default 8192 MiB) kept a KV copy of every
    finished prompt, although no prompt is ever reused (cache_prompt false); on
    the 3B it filled board memory after ~36 calls and the next call stalled
    (soak test, reports/phase0b_board_setup.md).
    -lv 4: at the default level this build prints no loader lines; level 4 adds
    the tensor-type counts, "offloaded N/N layers" and the HTP0 buffer sizes
    that the NPU-confirmation rule reads from the startup log."""
    if lane == "npu":
        offload = "--device HTP0 -ngl 99"
    elif lane == "cpu":
        offload = f"--device none -ngl 0 -t {threads}"
    else:
        raise ValueError(f"unknown lane {lane!r}")
    return (f"cd {DEVICE_PKG} && LD_LIBRARY_PATH={DEVICE_PKG}/lib "
            f"ADSP_LIBRARY_PATH={DEVICE_PKG}/lib ./bin/llama-server "
            f"-m {DEVICE_MODELS}/{model_file} --host 0.0.0.0 --port {LANE_PORT[lane]} "
            f"-c {CTX_SIZE} -np {N_PARALLEL} {offload} -fit off --cache-ram 0"
            + (f" -lv {log_level}" if log_level is not None else ""))


def _adb(*args: str, serial: str = "", timeout: float = 60) -> subprocess.CompletedProcess:
    cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env=clean_env())


def lane_log_path(lane: str) -> str:
    return f"{DEVICE_LOGS}/lane_{lane}.log"


def start_lane(lane: str, model_file: str, threads: int = 6, serial: str = "",
               log_level: int | None = 4) -> dict:
    """Start one lane in the background on the board and forward its port.
    Refuses any file that is not a known candidate with the recorded sha256.
    Returns the command and the LAPTOP start time (the board clock is wrong)."""
    sha = check_model(model_file, serial)
    cmd = lane_command(lane, model_file, threads, log_level)
    port = LANE_PORT[lane]
    _adb("shell", f"mkdir -p {DEVICE_LOGS}", serial=serial)
    _adb("shell", f"nohup sh -c '{cmd}' > {lane_log_path(lane)} 2>&1 &", serial=serial)
    _adb("forward", f"tcp:{port}", f"tcp:{port}", serial=serial)
    return {"lane": lane, "started_laptop_time": laptop_time(), "command": cmd,
            "model_file": model_file, "model_sha256": sha,
            "board_log": lane_log_path(lane), "url": f"http://localhost:{port}"}


def stop_lanes(serial: str = "") -> str:
    """Stop llama-server by process name only. Never deletes files."""
    return _adb("shell", "pkill llama-server; sleep 1; pgrep -l llama-server",
                serial=serial).stdout.strip()


def lane_log(lane: str, serial: str = "") -> str:
    return _adb("shell", f"cat {lane_log_path(lane)}", serial=serial).stdout


def wait_health(url: str, timeout_s: float = 300) -> float | None:
    """Seconds until /health is ok, or None on timeout (laptop wall clock)."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        if lane_health(url):
            return round(time.perf_counter() - t0, 1)
        time.sleep(2)
    return None


def _to_c(v: int) -> float:
    # sysfs thermal reports millidegrees C (kernel ABI, Documentation/ABI/testing/
    # sysfs-class-thermal); a few vendor zones report degrees. >= 1000 => milli.
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
    return {"t": laptop_time(),
            "cpu_max_c": mx(_CPU_KEYS), "npu_max_c": mx(_NPU_KEYS),
            "n_zones": len(zones)}


def chip_temperature(adb_serial: str = "", raw: bool = False) -> dict:
    cmd = ["adb"] + (["-s", adb_serial] if adb_serial else []) + ["shell", _ZONE_CMD]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                           env=clean_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}
    zones = parse_zones(p.stdout)
    out = summarise(zones)
    if raw:
        out["zones"] = zones
    return out


def lane_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/health", timeout=5) as r:
            return json.loads(r.read().decode()).get("status") == "ok"
    except Exception:
        return False


def _chat(url: str, prompt: str, max_tokens: int, timeout: float = 300) -> tuple[dict, float]:
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 0.0, "seed": 0, "cache_prompt": False, "stream": False}
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return data, (time.perf_counter() - t0) * 1000


def lane_speed(url: str, prompt: str, max_tokens: int = 64) -> dict:
    data, wall = _chat(url, prompt, max_tokens)
    tm = data.get("timings") or {}
    out = {"url": url, "model": data.get("model"),
           "prompt_tokens": tm.get("prompt_n"), "decode_tokens": tm.get("predicted_n"),
           "prefill_tok_s": tm.get("prompt_per_second"),
           "decode_tok_s": tm.get("predicted_per_second"),
           "ttft_ms_server": tm.get("prompt_ms"), "wall_ms": round(wall, 1)}
    # a field the server did not send stays None and is listed, never filled
    out["missing"] = [k for k, v in out.items() if v is None]
    return out


def mean_rates(samples: list[dict]) -> dict:
    """Mean of each lane_speed() rate field over several calls, skipping None;
    reports how many samples were missing each field."""
    out = {}
    for k in ("prompt_tokens", "decode_tokens", "prefill_tok_s", "decode_tok_s",
              "ttft_ms_server"):
        vals = [s[k] for s in samples if s.get(k) is not None]
        out[k] = round(sum(vals) / len(vals), 2) if vals else None
        out[f"{k}_missing"] = len(samples) - len(vals)
    out["n"] = len(samples)
    return out


def decode_rate_wallclock(url: str, prompt: str, n1: int = 16, n2: int = 80) -> dict:
    """(t2 - t1) / (d2 - d1) from client wall clock. The prompt is identical, so
    the prefill and HTTP overhead cancel; only the extra decoded tokens remain.
    Uses predicted_n only to know how many tokens were actually decoded (a call
    can stop early at EOS), not the server's timing."""
    d1, w1 = _chat(url, prompt, n1)
    d2, w2 = _chat(url, prompt, n2)
    k1 = (d1.get("timings") or {}).get("predicted_n")
    k2 = (d2.get("timings") or {}).get("predicted_n")
    ok = k1 is not None and k2 is not None and k2 > k1 and w2 > w1
    rate = (k2 - k1) / ((w2 - w1) / 1000) if ok else None   # None if a count is missing
    return {"decoded": [k1, k2], "wall_ms": [round(w1, 1), round(w2, 1)],
            "decode_tok_s_wallclock": round(rate, 2) if rate else None,
            "decode_tok_s_server": (d2.get("timings") or {}).get("predicted_per_second")}


if __name__ == "__main__":
    drop_pythonpath()
    if len(sys.argv) >= 2 and sys.argv[1] == "env":
        print(json.dumps({"laptop_time": laptop_time(),
                          "PYTHONPATH": os.environ.get("PYTHONPATH")}))
    elif len(sys.argv) >= 4 and sys.argv[1] == "start":
        info = start_lane(sys.argv[2], sys.argv[3])
        info["ready_after_s"] = wait_health(info["url"])
        print(json.dumps(info, indent=1))
    elif len(sys.argv) >= 2 and sys.argv[1] == "stop":
        print(json.dumps({"laptop_time": laptop_time(), "still_running": stop_lanes()}))
    elif len(sys.argv) >= 3 and sys.argv[1] == "log":
        print(lane_log(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "temp":
        print(json.dumps(chip_temperature(raw=True), indent=1))
    elif len(sys.argv) >= 3 and sys.argv[1] == "speed":
        pr = open(sys.argv[3]).read() if len(sys.argv) > 3 else "Say hello in five words."
        print(json.dumps(lane_speed(sys.argv[2], pr), indent=1))
    else:
        print(__doc__)
