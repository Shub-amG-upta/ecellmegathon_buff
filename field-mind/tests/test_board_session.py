"""bench/board_session.sh resumes per episode: an episode killed half-way is
redone, never skipped. Runs the real script with BACKEND=mock (board steps are
skipped on the mock) and SIGKILLs the whole process group in the middle of an
episode. Its resume is its own (a saved summary marks a finished episode; the
summary is the LAST file written), not bench/campaign.py's call cache."""

import gzip
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "data/episodes_dev"
KILL_IN = "dev_D01_high_cv_coal"          # the longest quick-set episode
SINGLE = "dev_A02_fcv_seize_fast dev_N01_normal"

pytestmark = pytest.mark.skipif(not (DEV / KILL_IN).exists(), reason="dev episodes not generated")


def _session(out: Path, log: Path):
    """Output goes to a FILE: a pipe nobody reads would fill and block the script."""
    env = dict(os.environ, BACKEND="mock", ROOT=str(out), SINGLE_EPISODES=SINGLE,
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    out.mkdir(parents=True, exist_ok=True)
    fh = open(log, "w")
    return subprocess.Popen(["bench/board_session.sh"], cwd=ROOT, env=env,
                            stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)


def _finish(p, log: Path) -> str:
    p.wait(timeout=900)
    return log.read_text()


def _load(p: Path) -> dict:
    d = json.loads(gzip.decompress(p.read_bytes()))
    d = d[0] if isinstance(d, list) else d
    return d["runs"][0] if "runs" in d else d


def test_killed_episode_is_redone_and_finished_ones_are_skipped(tmp_path):
    out = tmp_path / "session"
    a = out / "step0_multi_quick"
    live = a / f"{KILL_IN}_multi.ticks.jsonl"
    p = _session(out, tmp_path / "log1.txt")
    deadline = time.time() + 300
    while time.time() < deadline:
        if live.exists() and len(live.read_text().splitlines()) >= 30:
            break
        assert p.poll() is None, (tmp_path / "log1.txt").read_text()[-3000:]
        time.sleep(0.05)
    else:
        pytest.fail("never reached the middle of the episode")
    os.killpg(p.pid, signal.SIGKILL)
    p.wait()
    half = len(live.read_text().splitlines())
    assert not (a / f"{KILL_IN}_multi.summary.json").exists()           # half-written
    assert (a / "dev_C02_feeder_trip_multi.summary.json").exists()      # finished before it

    p2 = _session(out, tmp_path / "log2.txt")
    log = _finish(p2, tmp_path / "log2.txt")
    assert p2.returncode == 0, log[-3000:]
    assert "dev_C02_feeder_trip: already saved, skipping" in log
    assert f"{KILL_IN}: already saved" not in log                     # redone, not skipped
    run = _load(a / f"{KILL_IN}_multi.run.json.gz")
    full = len(live.read_text().splitlines())
    assert len(run["assessments"]) == full == run["n_ticks"] > half
    kept = list(a.glob(f"{KILL_IN}_multi.ticks.interrupted-*.jsonl"))
    assert len(kept) == 1 and len(kept[0].read_text().splitlines()) == half
    assert "differences from the run: 0" in (out / "step0_replay_check.txt").read_text()

    # (c): a half-written single-agent episode (run file but no summary) is redone
    c = out / "single_full_dev"
    ep = "dev_N01_normal"
    (c / f"{ep}_single.summary.json").unlink()
    (c / f"{ep}_single.run.json.gz").write_bytes(gzip.compress(b"half"))
    (c / "tmp" / f"runs_mock_{ep}.json").write_text("{half")
    p3 = _session(out, tmp_path / "log3.txt")
    log = _finish(p3, tmp_path / "log3.txt")
    assert p3.returncode == 0, log[-3000:]
    assert "dev_A02_fcv_seize_fast: already saved, skipping" in log
    assert f"{ep}: saved" in log
    assert _load(c / f"{ep}_single.run.json.gz")["episode_id"] == ep
    assert (c / f"{ep}_single.summary.json").exists()

    # (b): a saved run the replay cannot reproduce stops the session before (c)
    f = a / f"{KILL_IN}_multi.run.json.gz"
    run = _load(f)
    a1 = next(x for x in run["assessments"] if len(x["hypotheses"]) >= 2)
    a1["hypotheses"][0], a1["hypotheses"][1] = a1["hypotheses"][1], a1["hypotheses"][0]
    f.write_bytes(gzip.compress(json.dumps(run).encode()))
    (c / "dev_A02_fcv_seize_fast_single.summary.json").unlink()
    p4 = _session(out, tmp_path / "log4.txt")
    log = _finish(p4, tmp_path / "log4.txt")
    assert p4.returncode == 1
    assert "(b) the replay does not reproduce a saved run: stopping before (c)" in log
    assert "=== (c)" not in log
    assert not (c / "dev_A02_fcv_seize_fast_single.summary.json").exists()

