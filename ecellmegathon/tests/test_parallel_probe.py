"""board/parallel_probe.py against fake llama-servers: the scenarios run, overlap is computed, nothing on a board."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from board import board as bb
from board import parallel_probe as pp

LOG = "load_tensors: offloaded 29/29 layers to GPU\nHTP0 model buffer size =  1911.90 MiB\n"


def _server(delay):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/apply-template":
                out = {"prompt": "<bos>S" + body["messages"][-1]["content"] + "T"}
            else:
                time.sleep(delay)                  # a server that can run while another one runs
                tm = {"prompt_n": 30, "prompt_ms": 50.0, "predicted_n": 10, "predicted_ms": delay * 900}
                if self.path == "/completion":
                    out = {"content": 'fire"}', "timings": tm}
                else:
                    out = {"choices": [{"message": {"content": '{"tool":"update_report","args":{"t":"fire"}}'}}],
                           "timings": tm}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_probe_runs_and_sees_full_overlap_on_servers_that_run_at_once(tmp_path, monkeypatch, capsys):
    servers = {"npu": _server(0.2), "type": _server(0.1), "cpu": _server(0.3)}
    monkeypatch.setattr(bb, "LANE_PORT", {k: s.server_address[1] for k, s in servers.items()})
    monkeypatch.setattr(bb, "lane_log", lambda lane, serial="": LOG)
    monkeypatch.setattr(bb, "chip_temperature", lambda *a, **k: {"npu_c": 50.0})
    monkeypatch.setattr(bb, "_adb", lambda *a, **k: type("R", (), {"stdout": "SM8750P"})())
    monkeypatch.setattr(sys, "argv", ["x", "--no-start", "--cpu", "--reps", "2", "--out", str(tmp_path)])
    assert pp.main() == 0
    saved = json.loads(next(tmp_path.glob("parallel_probe_*.json")).read_text())
    s = saved["summary"]["main+type (both NPU)"]
    assert saved["label"] == "BOARD" and s["overlap"] > 0.7 and s["parallel_wall_ms"] < s["sequential_ms"]
    assert saved["verdict"]["TYPE_PARALLEL worth a board eval"] is True
    assert "main NPU + main CPU" in saved["summary"] and len(saved["calls"]["par2_cpu"]) == 2
    for srv in servers.values():
        srv.shutdown()
