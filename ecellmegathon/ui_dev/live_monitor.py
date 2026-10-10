"""
Live monitor: see every model call while the app or the eval runs, and what the QIDK is doing.

Sits between the app/eval and the model servers as a pass-through proxy, prints each call as it
happens, and watches the board:

  LLM  :8090 -> :8080 (llama-server on the QIDK NPU)   the user line, Asked, form so far, the reply,
                                                       new vs cached prompt tokens, prefill/decode time
  STT  :8094 -> :8082 (whisper-server)                 audio length, transcript, time
  TTS  :8095 -> :8083 (TTS server)                     text, audio length, cache hit, engine time
  board: the llama-server log (crashes, NPU placement), whether the process is alive,
         CPU/NPU temperature and free memory every 15 s

Run it in its own window, then point the app or the eval at it:

  .venv/Scripts/python -m ui_dev.live_monitor
  (other window)  LLM_URL=http://127.0.0.1:8090 STT_URL=http://127.0.0.1:8094 TTS_URL=http://127.0.0.1:8095

Everything printed is also appended to logs/live_monitor.log. The proxy adds well under a
millisecond of local forwarding; the times printed come from the servers themselves where they
report them, and from this proxy's wall clock otherwise (labelled "wall").

Stdlib only.
"""
from __future__ import annotations

import argparse
import http.client
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG = ROOT / "logs" / "live_monitor.log"
BOARD_LOG = "/data/local/tmp/llm/logs/lane_npu.log"

os.system("")  # turns on ANSI colours in the Windows console
C = {"llm": "\033[95m", "stt": "\033[96m", "tts": "\033[92m", "board": "\033[93m", "err": "\033[91m",
     "dim": "\033[2m", "b": "\033[1m", "0": "\033[0m"}
_print_lock = threading.Lock()
_count = {"llm": 0, "stt": 0, "tts": 0}


def say(kind: str, first: str, *more: str) -> None:
    """Print one event (a header line plus indented detail lines) and append it to the log."""
    stamp = time.strftime("%H:%M:%S")
    with _print_lock:
        print(f"{C['dim']}{stamp}{C['0']} {C.get(kind, '')}{C['b']}{first}{C['0']}", flush=True)
        for m in more:
            print(f"         {m}", flush=True)
        try:
            LOG.parent.mkdir(exist_ok=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write("\n".join([f"{stamp} {first}"] + [f"         {m}" for m in more]) + "\n")
        except OSError:
            pass


def short(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ---- what to show for each kind of call ----------------------------------------------
def prompt_parts(req: dict) -> dict:
    """Form / Asked / User from the last user message the dialog builds (app/prompt.py)."""
    msgs = req.get("messages") or []
    user = next((m.get("content", "") for m in reversed(msgs) if m.get("role") == "user"), "")
    out = {"form": "", "asked": "", "user": user}
    for key, label in (("form", "Form:"), ("asked", "Asked:"), ("user", "User:")):
        m = re.search(rf"^{label}\s*(.*)$", user, re.M)
        if m:
            out[key] = m.group(1).strip()
    return out


def describe_llm(req: dict, resp: dict | None, wall_ms: float, where: str) -> list[str]:
    p = prompt_parts(req)
    lines = [f"asked: {p['asked'] or 'none'}   form so far: {short(p['form'] or '{}', 110)}",
             f'user said: "{short(p["user"])}"']
    if resp is None:
        return lines
    t = resp.get("timings") or {}
    new, pre_ms = t.get("prompt_n"), t.get("prompt_ms")
    dec, dec_ms = t.get("predicted_n"), t.get("predicted_ms")
    cached = t.get("cache_n")
    parts = []
    if new is not None and pre_ms is not None:
        parts.append(f"prefill {new} new tok" + (f" (+{cached} cached)" if cached else "") + f" {pre_ms:.0f} ms")
    if dec is not None and dec_ms:
        parts.append(f"decode {dec} tok {dec_ms:.0f} ms ({dec / dec_ms * 1000:.1f} tok/s)")
    parts.append(f"wall {wall_ms:.0f} ms")
    if pre_ms is not None and dec_ms is not None:
        parts.append(f"outside the model {max(0.0, wall_ms - pre_ms - dec_ms):.0f} ms")
    lines.append(f"{where} · " + " · ".join(parts))
    content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    lines.append(f"reply: {short(content, 200)}")
    return lines


def wav_seconds(data: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return round(w.getnframes() / w.getframerate(), 2)
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


def describe_stt(body: bytes, resp_body: bytes, wall_ms: float) -> list[str]:
    i = body.find(b"RIFF")
    secs = wav_seconds(body[i:]) if i >= 0 else None
    try:
        text = json.loads(resp_body.decode("utf-8", "replace")).get("text", "")
    except (ValueError, AttributeError):
        text = resp_body[:120].decode("utf-8", "replace")
    return [f"audio {secs if secs is not None else '?'} s in · wall {wall_ms:.0f} ms",
            f'heard: "{short(text)}"']


def describe_tts(body: bytes, resp_body: bytes, headers: dict, wall_ms: float) -> list[str]:
    try:
        text = json.loads(body).get("text", "")
    except ValueError:
        text = ""
    h = {k.lower(): v for k, v in headers.items()}
    eng = h.get("x-tts-ms")
    return [f'text: "{short(text)}"',
            f"{h.get('x-tts-device', '?')} · {h.get('x-tts-model', '?')} · cache {h.get('x-tts-cache', '?')}"
            f" · audio {wav_seconds(resp_body)} s out · engine {eng + ' ms' if eng else 'not reported'}"
            f" · wall {wall_ms:.0f} ms"]


# ---- the pass-through proxy -------------------------------------------------------------
def make_proxy(kind: str, target: str, where: str):
    t = urlparse(target)

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _forward(self, method: str):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            fwd = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}
            interesting = method == "POST"
            if interesting:
                _count[kind] += 1
                num = _count[kind]
            t0 = time.perf_counter()
            try:
                conn = http.client.HTTPConnection(t.hostname, t.port, timeout=300)
                conn.request(method, self.path, body=body or None, headers=fwd)
                r = conn.getresponse()
                data, status, headers = r.read(), r.status, dict(r.getheaders())
                conn.close()
            except Exception as e:  # noqa: BLE001 (connection refused, reset, timeout)
                wall = (time.perf_counter() - t0) * 1000
                if interesting:
                    say("err", f"{kind.upper()} #{num} FAILED after {wall:.0f} ms: {e}",
                        f"target {target} is not answering (server down? board disconnected?)")
                self.send_error(502, f"live_monitor: {target} unreachable: {e}")
                return
            wall = (time.perf_counter() - t0) * 1000
            if interesting:
                try:
                    if kind == "llm":
                        req = json.loads(body or b"{}")
                        resp = json.loads(data) if status == 200 else None
                        lines = describe_llm(req, resp, wall, where)
                    elif kind == "stt":
                        lines = describe_stt(body, data, wall)
                    else:
                        lines = describe_tts(body, data, headers, wall)
                except Exception as e:  # noqa: BLE001 (never break the call because of display)
                    lines = [f"(could not summarise: {e})"]
                head = f"{kind.upper()} #{num} → {where}" + ("" if status == 200 else f"  HTTP {status}")
                if status != 200:
                    lines.append(f"error body: {short(data.decode('utf-8', 'replace'), 200)}")
                say(kind if status == 200 else "err", head, *lines)
            try:
                self.send_response(status)
                for k, v in headers.items():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionError, OSError):
                pass   # the caller gave up (e.g. a health probe's timeout); nothing to send to

        def do_POST(self):
            self._forward("POST")

        def do_GET(self):
            self._forward("GET")

    return Proxy


# ---- watching the board -----------------------------------------------------------------
LOG_KEEP = re.compile(r"error|abort|fail|dspqueue|backtrace|offloaded \d+/\d+ layers|HTP0 model buffer"
                      r"|server is listening|model loaded|out of memory", re.I)


def adb_cmd(adb: str, serial: str, *args: str) -> list[str]:
    return [adb] + (["-s", serial] if serial else []) + list(args)


def tail_board_log(adb: str, serial: str) -> None:
    """Follow the LLM server's log on the board; show crashes and placement lines only."""
    while True:
        try:
            p = subprocess.Popen(adb_cmd(adb, serial, "shell", f"tail -n 0 -f {BOARD_LOG}"),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace")
            for line in p.stdout:
                line = line.rstrip()
                if LOG_KEEP.search(line):
                    bad = re.search(r"error|abort|fail|dspqueue|out of memory", line, re.I)
                    say("err" if bad else "board", ("BOARD LOG: " if not bad else "BOARD LLM ERROR: ") + short(line, 200))
        except OSError as e:
            say("err", f"cannot run adb ({e}); board log not followed")
            return
        time.sleep(3)   # adb dropped (USB): try again


def watch_board(adb: str, serial: str, every: float) -> None:
    """Every `every` seconds: is llama-server alive, chip temperature, free memory."""
    from board.board import parse_zones, summarise  # read-only use of Track A's helpers
    alive_before = None
    zone_cmd = ('for z in /sys/class/thermal/thermal_zone*; do echo "$(cat $z/type 2>/dev/null) '
                '$(cat $z/temp 2>/dev/null)"; done; echo MEM $(grep MemAvailable /proc/meminfo); '
                'echo PIDS $(pgrep llama-server)')
    while True:
        try:
            out = subprocess.run(adb_cmd(adb, serial, "shell", zone_cmd), capture_output=True, text=True,
                                 timeout=20, encoding="utf-8", errors="replace").stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            say("err", f"BOARD not reachable over adb: {e}")
            time.sleep(every)
            continue
        if not out.strip():
            say("err", "BOARD not answering over adb (unplugged or rebooting?)")
            time.sleep(every)
            continue
        s = summarise(parse_zones(out))
        mem = re.search(r"MEM MemAvailable:\s*(\d+)", out)
        pids = re.search(r"PIDS\s*(.*)", out)
        alive = bool(pids and pids.group(1).strip())
        if alive_before and not alive:
            say("err", "!!! llama-server on the board has STOPPED (crashed or was killed) !!!",
                "every LLM call now fails; see the BOARD LLM ERROR lines above, then restart it")
        alive_before = alive
        free = f"{int(mem.group(1)) / 1048576:.1f} GiB" if mem else "?"
        say("board", f"BOARD  llama-server {'running' if alive else 'NOT running'} · "
                     f"CPU {s['cpu_max_c']} °C · NPU {s['npu_max_c']} °C · free memory {free}")
        time.sleep(every)


def placement_line(adb: str, serial: str) -> str:
    try:
        from board.npu_proof import placement
        log = subprocess.run(adb_cmd(adb, serial, "shell", f"cat {BOARD_LOG}"), capture_output=True,
                             text=True, timeout=20, encoding="utf-8", errors="replace").stdout
        f = placement(log)
        if f["offloaded"] is None:
            return "LLM placement: unknown (no 'offloaded' line in the board log yet)"
        return (f"LLM on the QIDK: {f['offloaded']}/{f['layers']} layers on HTP0 (Hexagon NPU), "
                f"HTP0 model buffer {f['htp0_model_mib']} MiB; token sampling + JSON grammar on the board CPU")
    except Exception as e:  # noqa: BLE001
        return f"LLM placement: could not read the board log ({e})"


def main() -> None:
    ap = argparse.ArgumentParser(description="live view of every model call and the QIDK")
    ap.add_argument("--llm", default="8090:http://127.0.0.1:8080", help="listen_port:target")
    ap.add_argument("--stt", default="8094:http://127.0.0.1:8082", help="listen_port:target")
    ap.add_argument("--tts", default="8095:http://127.0.0.1:8083", help="listen_port:target")
    ap.add_argument("--adb", default="adb", help="path to adb")
    ap.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL", ""))
    ap.add_argument("--every", type=float, default=15.0, help="seconds between board readings")
    ap.add_argument("--no-board", action="store_true", help="don't use adb (laptop-only runs)")
    a = ap.parse_args()

    where = {"llm": "QIDK NPU (HTP0)", "stt": f"STT ({os.environ.get('STT_DEVICE', 'laptop')})",
             "tts": "TTS"}
    say("b", "LIVE MONITOR  (Ctrl+C to stop)")
    for kind in ("llm", "stt", "tts"):
        port, target = getattr(a, kind).split(":", 1)
        srv = ThreadingHTTPServer(("127.0.0.1", int(port)), make_proxy(kind, target, where[kind]))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        say(kind, f"{kind.upper()}: listening on http://127.0.0.1:{port} -> {target}")
    say("b", "Point the app or the eval at it:",
        "PowerShell: $env:LLM_URL='http://127.0.0.1:8090'; $env:STT_URL='http://127.0.0.1:8094'; "
        "$env:TTS_URL='http://127.0.0.1:8095'")
    if not a.no_board:
        say("board", placement_line(a.adb, a.serial))
        threading.Thread(target=tail_board_log, args=(a.adb, a.serial), daemon=True).start()
        threading.Thread(target=watch_board, args=(a.adb, a.serial, a.every), daemon=True).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        say("b", "monitor stopped")


if __name__ == "__main__":
    main()
