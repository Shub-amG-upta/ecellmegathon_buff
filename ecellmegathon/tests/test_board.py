"""board/board.py and board/npu_proof.py (ported from FieldMind's
tests/test_board_env.py and tests/test_board_preflight.py)."""
import os
import re
import subprocess

import pytest

from board import board
from board.npu_proof import check_lane_log, parse_meminfo, placement


def test_clean_env_has_no_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/x")
    env = board.clean_env()
    assert "PYTHONPATH" not in env and env["PATH"] == os.environ["PATH"]


def test_adb_subprocess_gets_clean_env_and_laptop_time(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/x")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, "cpu-0-0-0 31000\nnsp0 29000\n", "")

    monkeypatch.setattr(board.subprocess, "run", fake_run)
    out = board.chip_temperature()
    assert "PYTHONPATH" not in seen["env"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}", out["t"])
    assert (out["cpu_max_c"], out["npu_max_c"]) == (31.0, 29.0)


def test_lane_command_env_offload_and_fixed_flags():
    lib = "/data/local/tmp/llm/llama.cpp/lib"
    npu, cpu = board.lane_command("npu", "m.gguf"), board.lane_command("cpu", "m.gguf", threads=6)
    for cmd in (npu, cpu):
        assert f"LD_LIBRARY_PATH={lib} " in cmd and f"ADSP_LIBRARY_PATH={lib} " in cmd
        assert " -c 4096 -np 1 " in cmd and "-m /data/local/tmp/llm/m.gguf " in cmd
        assert cmd.endswith("-fit off --cache-ram 0 -lv 4")
    assert "--device HTP0 -ngl 99" in npu and "--port 8080" in npu
    assert "--device none -ngl 0 -t 6" in cpu and "--port 8081" in cpu
    assert "HTP0" not in cpu
    assert board.lane_command("npu", "m.gguf", log_level=None).endswith("-fit off --cache-ram 0")


def _fake_adb(sha_on_board):
    calls = []

    def fake(*args, serial="", timeout=60):
        calls.append(args)
        out = f"{sha_on_board}  /data/local/tmp/llm/x\n" if "sha256sum" in args[-1] else ""
        return subprocess.CompletedProcess(args, 0, out, "")
    return fake, calls


def test_lane_refuses_a_file_outside_the_manifest(monkeypatch):
    fake, calls = _fake_adb("0" * 64)
    monkeypatch.setattr(board, "_adb", fake)
    with pytest.raises(board.ModelNotAllowed):
        board.start_lane("npu", "../geniex/models/Llama-3.2-3B-Instruct-Q4_0.gguf")
    assert not any("llama-server" in a[-1] for a in calls)


def test_lane_refuses_a_wrong_sha256_and_accepts_the_right_one(monkeypatch):
    name = "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf"
    fake, calls = _fake_adb("f" * 64)
    monkeypatch.setattr(board, "_adb", fake)
    with pytest.raises(board.ModelNotAllowed):
        board.start_lane("npu", name)
    assert not any("llama-server" in a[-1] for a in calls)
    fake, calls = _fake_adb(board.CANDIDATES[name])
    monkeypatch.setattr(board, "_adb", fake)
    info = board.start_lane("npu", name)
    assert info["model_sha256"] == board.CANDIDATES[name]
    assert any("llama-server" in a[-1] for a in calls)


M = "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf"
NPU_LOG = (f"llama_model_loader: loaded meta data from /data/local/tmp/llm/{M}\n"
           "load_tensors: offloaded 29/29 layers to GPU\n"
           "load_tensors:         HTP0 model buffer size =  1911.90 MiB\n")
CPU_LOG = (f"llama_model_loader: loaded meta data from /data/local/tmp/llm/{M}\n"
           "load_tensors: offloaded 0/29 layers to GPU\n")


def test_lane_logs_prove_placement():
    assert placement(NPU_LOG) == {"offloaded": 29, "layers": 29, "htp0_model_mib": 1911.9}
    assert check_lane_log("npu", NPU_LOG, M) == []
    assert check_lane_log("cpu", CPU_LOG, M) == []
    assert check_lane_log("npu", NPU_LOG.replace("29/29", "20/29"))      # partial offload
    assert check_lane_log("npu", NPU_LOG.replace("1911.90", "0.00"))     # no HTP0 memory
    assert check_lane_log("cpu", NPU_LOG)                                # CPU lane on HTP0
    assert check_lane_log("npu", NPU_LOG, "gemma.gguf")                  # another file
    assert check_lane_log("npu", "nothing useful")                       # no -lv 4


def test_meminfo_parsing():
    m = parse_meminfo("MemTotal:       11633360 kB\nMemAvailable:    6290000 kB\nCached: 1 kB\n")
    assert m == {"MemTotal_kB": 11633360, "MemAvailable_kB": 6290000}
