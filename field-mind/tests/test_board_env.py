"""Board scripts run with PYTHONPATH unset and stamp laptop time (Phase 0b setup)."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from bench import board

ROOT = Path(__file__).resolve().parent.parent


def test_board_script_drops_pythonpath():
    env = dict(os.environ, PYTHONPATH="/nonexistent/qairt/lib/python")
    p = subprocess.run([sys.executable, "-m", "bench.board", "env"], cwd=ROOT,
                       env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["PYTHONPATH"] is None


def test_clean_env_has_no_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/x")
    env = board.clean_env()
    assert "PYTHONPATH" not in env and env["PATH"] == os.environ["PATH"]


def test_adb_subprocess_gets_clean_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/x")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, "cpu-0-0-0 31000\n", "")

    monkeypatch.setattr(board.subprocess, "run", fake_run)
    out = board.chip_temperature()
    assert "PYTHONPATH" not in seen["env"]
    # laptop time: ISO with a UTC offset, and the laptop's year, not the board's
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}", out["t"])


def test_lanes_always_use_ctx_4096_np_1():
    # HUMAN DECISION 2026-10-03: -c 4096 -np 1 for every model and both lanes.
    import yaml
    for lane, model in (("npu", "a.gguf"), ("cpu", "b.gguf"), ("npu", "c.gguf")):
        cmd = board.lane_command(lane, model)
        assert " -c 4096 -np 1 " in cmd
        assert f"-m /data/local/tmp/llm/{model} " in cmd
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    assert cfg["llm"]["llamaserver"]["ctx_size"] == 4096


def test_lane_command_env_and_offload():
    lib = "/data/local/tmp/llm/llama.cpp/lib"
    npu, cpu = board.lane_command("npu", "m.gguf"), board.lane_command("cpu", "m.gguf", threads=6)
    for cmd in (npu, cpu):
        # both variables on every launch; the folder holds libggml-htp-v75.so
        assert f"LD_LIBRARY_PATH={lib} " in cmd and f"ADSP_LIBRARY_PATH={lib} " in cmd
    assert "--device HTP0 -ngl 99" in npu and "--port 8080" in npu
    assert "--device none -ngl 0 -t 6" in cpu and "--port 8081" in cpu
    assert "HTP0" not in cpu
    # loader lines (type counts, offloaded layers, HTP0 buffer) need log level 4
    assert npu.endswith("-fit off --cache-ram 0 -lv 4") and cpu.endswith("-fit off --cache-ram 0 -lv 4")


def _fake_adb(sha_on_board):
    calls = []

    def fake(*args, serial="", timeout=60):
        calls.append(args)
        out = f"{sha_on_board}  /data/local/tmp/llm/x\n" if "sha256sum" in args[-1] else ""
        return subprocess.CompletedProcess(args, 0, out, "")
    return fake, calls


def test_lane_refuses_a_file_outside_the_manifest(monkeypatch):
    import pytest
    fake, calls = _fake_adb("0" * 64)
    monkeypatch.setattr(board, "_adb", fake)
    # the old impure file still on the board, reached by a relative path
    with pytest.raises(board.ModelNotAllowed):
        board.start_lane("npu", "../geniex/models/Llama-3.2-3B-Instruct-Q4_0.gguf")
    assert not any("llama-server" in a[-1] for a in calls)      # nothing was launched


def test_lane_refuses_a_wrong_sha256_and_accepts_the_right_one(monkeypatch):
    import pytest
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


def test_log_level_is_optional():
    assert " -lv " not in board.lane_command("npu", "m.gguf", log_level=None)
    assert board.lane_command("npu", "m.gguf", log_level=None).endswith("-fit off --cache-ram 0")


def test_every_lane_and_model_has_the_host_prompt_cache_off():
    # HUMAN DECISION 2026-10-03: --cache-ram 0 on every lane, every model
    for lane in ("npu", "cpu"):
        for model in board.CANDIDATES:
            for lv in (4, None):
                assert " --cache-ram 0" in board.lane_command(lane, model, log_level=lv)
