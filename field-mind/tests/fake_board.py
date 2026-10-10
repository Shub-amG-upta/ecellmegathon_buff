"""
A fake QIDK for the campaign tests: a local HTTP server that speaks enough of
llama-server (/health, /tokenize, /v1/chat/completions) plus the board interface
bench.campaign.RealBoard exposes. No adb, no model. Replies are a deterministic
function of the prompt and the loaded model, so a redone episode reproduces.

Fault injection comes from a plan dict (or the FAKE_BOARD_PLAN env var, JSON, for
subprocess tests):

  models: {tag: {broken_first, broken_repair, think, prefill_tok_s, decode_tok_s,
                 layers: [n, m], gguf_ok, reverse}}
  fail_calls: {"<n>": "http500" | "garbage" | "close" | "hang"}   n = chat call number, from 1
  fail_from: n                 every chat call from n on returns HTTP 500
  sigkill_at_call: n           SIGKILL this process when chat call n arrives (a real kill -9)
  temps: [..]                  successive max temperatures; the last one repeats
  adb_drop_at_temp: n          the n-th temperature read reports an adb error
  no_timings: true             the server omits its timings block
  mem_available_kb: [..]       successive MemAvailable reads (kB); the last one repeats
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bench import board as _board
from bench.campaign import MODELS

FILE_TO_TAG = {m["file"]: m["tag"] for m in MODELS}
IDLE_C = 33.0


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)


def make_reply(prompt: str, tag: str, mcfg: dict) -> str:
    """Schema-valid diagnostician / verifier JSON built from ids in the prompt."""
    if "adversarial verifier" in prompt[:200]:
        return json.dumps({"checks": [], "strongest_contradiction": None,
                           "revised_confidence": 0.6, "agree": True})
    repair = "bad_output" in prompt[:400] or "previous output" in prompt[:400].lower() \
        or "not valid" in prompt[:400].lower()
    rate = mcfg.get("broken_repair" if repair else "broken_first", 0.0)
    if (_h(tag + prompt) % 1000) / 1000 < rate:
        return "I think the drum level is falling because"          # no JSON at all
    facts = list(dict.fromkeys(re.findall(r"\bF\d+\b", prompt))) or ["F1"]
    cases = list(dict.fromkeys(re.findall(r"RCA-\d+", prompt)))[:3]
    if mcfg.get("reverse"):
        cases = cases[::-1]
    hyps = [{"rank": i + 1, "cause": f"cause of {c}", "confidence": round(0.6 - 0.15 * i, 2),
             "supports": facts[:1], "case_ref": c, "discriminator": "check"}
            for i, c in enumerate(cases)]
    return json.dumps({"headline": "fake", "hypotheses": hyps, "unexplained": []})


class FakeServer:
    def __init__(self, plan: dict):
        self.plan = plan
        self.tag = None
        self.chat_calls = 0
        self.lock = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, raw=None):
                data = raw if raw is not None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, {"status": "ok"}) if self.path == "/health" else self._send(404, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
                if self.path == "/tokenize":
                    return self._send(200, {"tokens": list(range((len(body["content"]) + 3) // 4))})
                with outer.lock:
                    outer.chat_calls += 1
                    n = outer.chat_calls
                p = outer.plan
                if p.get("sigkill_at_call") == n:
                    os.kill(os.getpid(), signal.SIGKILL)
                fault = (p.get("fail_calls") or {}).get(str(n))
                if p.get("fail_from") and n >= p["fail_from"]:
                    fault = "http500"
                if fault == "http500":
                    return self._send(500, {"error": "fake crash"})
                if fault == "garbage":
                    return self._send(200, raw=b"<html>not json</html>")
                if fault == "close":
                    self.connection.close()
                    return
                if fault == "hang":
                    time.sleep(3)
                    return self._send(200, {"error": "too late"})
                mcfg = (p.get("models") or {}).get(outer.tag, {})
                prompt = body["messages"][0]["content"]
                text = make_reply(prompt, outer.tag, mcfg)
                think_off = (body.get("chat_template_kwargs") or {}).get("enable_thinking") is False
                reasoning = "" if (think_off and not mcfg.get("think")) or not mcfg.get("think") \
                    else "Okay, let me think."
                cap = body["max_tokens"]
                full = (len(text) + 3) // 4
                finish = "stop"
                if full > cap:
                    text, full, finish = text[:cap * 4], cap, "length"
                pn = (len(prompt) + 3) // 4 + 12                     # 12 = template overhead
                pr, dr = mcfg.get("prefill_tok_s", 900.0), mcfg.get("decode_tok_s", 16.0)
                msg = {"role": "assistant", "content": text}
                if reasoning:
                    msg["reasoning_content"] = reasoning
                out = {"model": outer.tag, "choices": [{"message": msg, "finish_reason": finish}],
                       "timings": {"prompt_n": pn, "prompt_ms": pn / pr * 1000,
                                   "prompt_per_second": pr, "predicted_n": full,
                                   "predicted_ms": full / dr * 1000, "predicted_per_second": dr}}
                if p.get("no_timings"):
                    del out["timings"]
                self._send(200, out)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


class FakeBoard:
    lane = "npu"

    def __init__(self, plan: dict | None = None):
        self.plan = plan if plan is not None else json.loads(os.environ.get("FAKE_BOARD_PLAN", "{}"))
        self.server = FakeServer(self.plan)
        self.url = f"http://127.0.0.1:{self.server.port}"
        self.running = None
        self.starts: list[tuple] = []
        self.slept: list[float] = []
        self.temp_reads = 0

    def sleep(self, s: float) -> None:
        self.slept.append(s)

    def _mcfg(self, tag: str) -> dict:
        return (self.plan.get("models") or {}).get(tag, {})

    def check_model(self, model_file: str) -> str:
        if model_file not in _board.CANDIDATES:
            raise _board.ModelNotAllowed(model_file)
        return _board.CANDIDATES[model_file]

    def gguf(self, model_file: str) -> dict:
        ok = self._mcfg(FILE_TO_TAG[model_file]).get("gguf_ok", True)
        return {"verdict": "OK: every matrix is Q4_0/Q8_0" if ok else "FLAG: 1 matrix tensors outside Q4_0/Q8_0",
                "type_counts": {"F32": 58, "Q4_0": 196, "Q8_0": 1}}

    def start(self, model_file: str, log_level) -> dict:
        self.running = model_file
        self.server.tag = FILE_TO_TAG[model_file]
        self.starts.append((model_file, log_level))
        return {"command": _board.lane_command("npu", model_file, log_level=log_level),
                "url": self.url, "ready_after_s": 0.1}

    def stop(self) -> None:
        self.running = None

    def health(self) -> bool:
        return self.running is not None

    def log(self) -> str:
        n, m = self._mcfg(self.server.tag).get("layers", [29, 29])
        return ("llama_model_loader: - type  f32:   58 tensors\n"
                "llama_model_loader: - type q4_0:  196 tensors\n"
                "llama_model_loader: - type q8_0:    1 tensors\n"
                + ("load_tensors: offloading output layer to GPU\n" if n == m else "")
                + f"load_tensors: offloaded {n}/{m} layers to GPU\n"
                  "load_tensors:         HTP0 model buffer size =  1911.90 MiB\n")

    def temperature(self) -> dict:
        self.temp_reads += 1
        if self.plan.get("adb_drop_at_temp") == self.temp_reads:
            return {"error": "adb: device offline"}
        temps = self.plan.get("temps") or [IDLE_C]
        t = temps[min(self.temp_reads - 1, len(temps) - 1)]
        return {"t": "fake", "cpu_max_c": t, "npu_max_c": t - 1, "n_zones": 2}

    def memory(self) -> dict:
        """mem_available_kb: successive values from the plan; the last one repeats."""
        self.mem_reads = getattr(self, "mem_reads", 0) + 1
        seq = self.plan.get("mem_available_kb") or [6_000_000]
        return {"mem_available_kb": seq[min(self.mem_reads - 1, len(seq) - 1)],
                "server_rss_kb": 530_000 if self.running else None, "t": "fake"}

    def server_commit(self) -> str:
        return "99b95488c"


def make() -> FakeBoard:              # --board tests.fake_board:make
    return FakeBoard()
