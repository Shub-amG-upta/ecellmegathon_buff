"""bench/benchmark.py (scripts/benchmark.sh) and the monitor's new columns.
Outcome-based: assertions on the folders and files a run leaves, and on what
the monitor builds from real (mock) tick records."""

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bench.stage_monitor_multi import build, cell, load_truth, running_scores, episode_dir

ROOT = Path(__file__).resolve().parent.parent
needs_dev = pytest.mark.skipif(not (ROOT / "data/episodes_dev/dev_C01_wet_coal").exists(),
                               reason="dev episodes not generated")


def bench(tmp_root, *args):
    """The runner on the mock backend, writing under a temporary results root."""
    code = ("import sys, bench.benchmark as b; from pathlib import Path; "
            f"b.ROOT_RESULTS = Path({str(tmp_root)!r}); sys.argv = ['benchmark.py', *{list(args)!r}]; "
            "raise SystemExit(b.main())")
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                          timeout=600)


@pytest.fixture(scope="module")
def first(tmp_path_factory):
    root = tmp_path_factory.mktemp("bench")
    p = bench(root, "t", "dev_C01_wet_coal", "ep_N01_normal", "--backend", "mock", "--plain")
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    return root / "t", p


@needs_dev
def test_a_test_folder_holds_every_episode_and_the_settings(first):
    d, p = first
    names = sorted(x.name for x in d.iterdir())
    for ep in ("dev_C01_wet_coal", "ep_N01_normal"):
        for kind in ("summary.json", "run.json.gz", "log"):
            assert f"{ep}_multi.{kind}" in names
    assert "RESULTS.md" in names and "test.json" in names
    assert not [n for n in names if n.endswith(".ticks.jsonl") or n.startswith(".tmp_")]
    t = json.loads((d / "test.json").read_text())
    assert t["params"]["arch"] == "multi" and t["params"]["overlay"] == "configs/fast.yaml"
    assert [(r["stem"], r["status"]) for r in t["runs"]] == [
        ("dev_C01_wet_coal_multi", "done"), ("ep_N01_normal_multi", "done")]
    res = (d / "RESULTS.md").read_text()
    assert "MOCK backend" in res and res.count("\n| dev_C01_wet_coal | 1 |") == 1


@needs_dev
def test_summary_counts_match_the_saved_run(first):
    d, _ = first
    s = json.loads((d / "dev_C01_wet_coal_multi.summary.json").read_text())
    run = json.loads(gzip.decompress((d / "dev_C01_wet_coal_multi.run.json.gz").read_bytes()))
    envs = [e for a in run["assessments"] for e in a["envelopes"]]
    envs += [t["envelope"] for a in run["assessments"] for t in a["multi"]["text"]]
    assert s["calls"]["calls"] == len(envs) > 0
    assert s["calls"]["tokens_in"] == sum(e["tokens"]["prefill"] for e in envs)
    assert s["calls"]["ticks"] == len(run["assessments"])
    assert s["meta"]["energy_mwh"] is None and "MOCK" in s["meta"]["where"]


@needs_dev
def test_same_name_resumes_again_adds_and_other_settings_are_refused(first, tmp_path):
    d, _ = first
    root = d.parent
    p = bench(root, "t", "dev_C01_wet_coal", "--backend", "mock", "--plain")
    assert p.returncode == 0 and "skipped" in p.stdout
    assert not list(d.glob("dev_C01_wet_coal_multi_r2.*"))
    p = bench(root, "t", "dev_C01_wet_coal", "--backend", "mock", "--plain", "--again")
    assert p.returncode == 0, p.stderr[-1500:]
    assert (d / "dev_C01_wet_coal_multi_r2.summary.json").exists()
    assert (d / "dev_C01_wet_coal_multi.summary.json").exists()          # run 1 untouched
    p = bench(root, "t", "dev_C01_wet_coal", "--backend", "mock", "--plain",
              "--merge-rule", "tiebreak")
    assert p.returncode != 0 and "other settings" in p.stderr
    assert json.loads((d / "test.json").read_text())["params"]["merge_rule"] == "model"


def test_bad_names_and_unknown_episodes_are_refused_before_anything_runs(tmp_path):
    p = bench(tmp_path, "../x", "ep_N01_normal", "--backend", "mock")
    assert p.returncode != 0 and "test name" in p.stderr
    p = bench(tmp_path, "x", "ep_Z99_none", "--backend", "mock")
    assert p.returncode != 0 and "unknown episode" in p.stderr
    p = bench(tmp_path, "x", "ep_N01_normal", "--backend", "mock", "--arch", "single",
              "--mode", "realtime")
    assert p.returncode != 0 and "multi-agent only" in p.stderr
    assert not list(tmp_path.iterdir())


# ---------------------------------------------------------------- the monitor
def _records(d: Path, stem: str) -> list[dict]:
    return json.loads(gzip.decompress((d / f"{stem}.run.json.gz").read_bytes()))["assessments"]


@needs_dev
def test_per_tick_marks_add_up_to_the_evaluators_group_top1(first):
    d, _ = first
    recs = _records(d, "dev_C01_wet_coal_multi")
    truth = load_truth(episode_dir("dev_C01_wet_coal"))
    rows = build(recs, truth)["rows"]
    scored = [r["acc"] for r in rows if r["acc"]["scored"]]
    t2 = running_scores(recs, truth)["t2"]
    # a different route to the same number: marks counted tick by tick
    assert len(scored) == t2["n_scored"] > 0
    assert round(sum(a["pub"] in ("✓", "≈") for a in scored) / len(scored), 3) == t2["group_top1"]
    assert round(sum(a["bel"] in ("✓", "≈") for a in scored) / len(scored), 3) == t2["belief_group"]
    assert all(a["conf"] is not None for a in scored)
    assert all(r["acc"]["pub"] in ("", "pre") for r in rows if not r["acc"]["scored"])


@needs_dev
def test_cells_carry_each_calls_input_tokens(first):
    d, _ = first
    recs = _records(d, "dev_C01_wet_coal_multi")
    model = build(recs)
    envs = [e for a in recs for e in a["envelopes"]] + \
           [t["envelope"] for a in recs for t in a["multi"]["text"]]
    assert sum(j["in_tok"] for j in model["jobs"]) == sum(e["tokens"]["prefill"] for e in envs)
    r = next(r for r in model["rows"] if any(j["tick"] == r["tick"] and j["agent"] == "diag_heat"
                                             for j in model["jobs"]))
    j = next(j for j in model["jobs"] if j["tick"] == r["tick"] and j["agent"] == "diag_heat")
    assert f" {j['in_tok']}t" in cell(r, "diag_heat", model["jobs"], 0.0)


@needs_dev
def test_real_time_answers_get_the_tokens_of_their_own_envelope(tmp_path, monkeypatch):
    import run_demo
    monkeypatch.chdir(ROOT)
    live = tmp_path / "t.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "run_demo.py", "--backend", "mock", "--arch", "multi", "--overlay", "configs/fast.yaml",
        "--mode", "realtime", "--tick-s", "0.02", "--episodes-dir", "data/episodes_dev",
        "--episode", "dev_C01_wet_coal", "--live-ticks", str(live), "--tag", "t",
        "--out", str(tmp_path)])
    run_demo.main()
    recs = [json.loads(x) for x in live.read_text().splitlines()]
    jobs = [j for j in build(recs)["jobs"] if j.get("finish_s") is not None and not j["verdict"] == "stale"]
    envs = [e for a in recs for e in a["envelopes"]] + \
           [t["envelope"] for a in recs for t in a["multi"]["text"]]
    assert jobs and all(j["in_tok"] for j in jobs)
    assert sum(j["in_tok"] for j in jobs) == sum(e["tokens"]["prefill"] for e in envs)


@needs_dev
def test_single_agent_ticks_show_diagnosis_and_verifier_calls(tmp_path):
    p = bench(tmp_path, "s", "dev_A01_fcv_seize", "--arch", "single", "--backend", "mock",
              "--plain")
    assert p.returncode == 0, p.stderr[-1500:]
    recs = _records(tmp_path / "s", "dev_A01_fcv_seize_single")
    model = build(recs, load_truth(episode_dir("dev_A01_fcv_seize")))
    assert model["arch"] == "single"
    n_env = sum(e["agent"] in ("diagnostician", "verifier") for a in recs for e in a["envelopes"])
    assert len(model["jobs"]) == n_env > 0
    assert {j["agent"] for j in model["jobs"]} <= {"diagnostician", "verifier"}
