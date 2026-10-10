"""run_demo.py --live-ticks: every tick's assessment reaches a JSONL file while
the run is still going. Outcome-based: assertions on the file's contents."""

import json
import sys
import time
from pathlib import Path

import pytest

import run_demo

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
needs_dev = pytest.mark.skipif(not (DEV / "dev_B02_tube_leak_fast").exists(),
                               reason="dev episodes not generated")


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_a_tick_is_on_disk_before_the_run_ends(tmp_path):
    w = run_demo.LiveTickWriter(str(tmp_path / "sub" / "ticks.jsonl"))
    w.episode = "ep_X"
    try:
        w({"tick": 7, "state": "ALARM"})
        end = time.monotonic() + 2.0
        while time.monotonic() < end and not w.path.read_text():
            time.sleep(0.01)
        # the writer is still open: the line is there without close()
        assert _lines(w.path) == [{"episode": "ep_X", "tick": 7, "state": "ALARM"}]
        w({"tick": 8, "state": "NORMAL"})
    finally:
        w.close()
    assert [(d["tick"], d["state"]) for d in _lines(w.path)] == [(7, "ALARM"), (8, "NORMAL")]


@needs_dev
def test_live_ticks_file_holds_the_ticks_of_the_saved_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    live = tmp_path / "ticks.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "run_demo.py", "--backend", "mock", "--arch", "multi",
        "--overlay", "configs/fast.yaml", "--mode", "realtime", "--tick-s", "0.01",
        "--episodes-dir", str(DEV), "--episode", "dev_B02_tube_leak_fast",
        "--live-ticks", str(live), "--live-print", "--tag", "t", "--out", str(tmp_path)])
    run_demo.main()
    shown = capsys.readouterr().out.splitlines()
    saved = json.loads((tmp_path / "runs_mock_t.json").read_text())[0]["assessments"]
    got = _lines(live)
    assert got and {d["episode"] for d in got} == {"dev_B02_tube_leak_fast"}
    keep = ("tick", "state", "triage", "hypotheses", "actions", "escalate")
    assert [{k: d[k] for k in keep} for d in got] == [{k: a[k] for k in keep} for a in saved]
    # --live-print: one screen line per tick, QUIET ticks too, in tick order
    ticks = [int(x[10:14]) for x in shown if x[9:10] == "t" and x[10:14].strip().isdigit()]
    assert ticks == [a["tick"] for a in saved]
    assert any(" QUIET " in x for x in shown) and any("sent: diag_" in x for x in shown)
