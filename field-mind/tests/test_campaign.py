"""Board campaign runner (bench/campaign.py) against the mock agent pipeline and
a fake llama-server (tests/fake_board.py). No board, no model."""
import gzip
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from bench import campaign as C
from bench.model_choice import call_stats
from tests.fake_board import FakeBoard

ROOT = Path(__file__).resolve().parent.parent
DEV = "data/episodes_dev"
LLAMA = "llama32-3b"
# fake rates: gemma is the fastest model that passes; qwen25 fails the offload check
MODELS_PLAN = {"qwen3-1.7b": {"decode_tok_s": 35.0, "prefill_tok_s": 450},
               "gemma3-1b-qat": {"decode_tok_s": 46, "prefill_tok_s": 600, "reverse": True},
               "qwen25-0.5b": {"decode_tok_s": 60, "prefill_tok_s": 1200, "layers": [20, 25]}}
REQUIRED_CALL_FIELDS = {"t", "phase", "model", "model_sha256", "server_flags", "episode", "tick",
                        "lane", "prompt_sha256", "prompt", "raw_reply", "parse_status",
                        "stop_reason", "timings", "chip_temp", "cache_hit"}
# timings, clocks, cache marks, and two bookkeeping fields of model_choice's output:
# the runs-file path, and the count of calls without timings (cache hits have none)
_TIMING = re.compile(r"latency|_ms$|temp|^t$|finished|started|network|thermal|wall_clock|"
                     r"cache_hit|n_cache_hits|S1_tick|git_commit|created|^file$|rate_calls_missing")


@pytest.fixture(autouse=True)
def _no_pick_override(monkeypatch):
    """The committed rule is 'no model passes -> stop'. Tests run it as committed;
    the human override of 2026-10-05 is switched on only in its own tests."""
    monkeypatch.setattr(C, "PICK_OVERRIDE", None)


def strip(x):
    """Everything except timings, clocks and cache marks."""
    if isinstance(x, dict):
        return {k: strip(v) for k, v in x.items() if not _TIMING.search(k)}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x


def mk(tmp_path, plan=None, name="out"):
    b = FakeBoard(plan or {})
    c = C.Campaign(tmp_path / name, b, commit=False, baseline_dir=tmp_path / "base")
    c.measure_idle()
    return c, b


def one_episode(c, phase="round1", ep="dev_A01_fcv_seize", tag=LLAMA, stage="B"):
    return c.run_episode_job(stage, phase, tag, ep, DEV)


def load_calls(c, phase="round1", ep="dev_A01_fcv_seize", tag=LLAMA, stage="B"):
    p = c.prefix(stage, phase, tag, ep) + ".calls.jsonl.gz"
    return [json.loads(ln) for ln in gzip.open(p, "rt")]


# ---- 2. per-call log ---------------------------------------------------------
def _backend(tmp_path, plan=None):
    c, b = mk(tmp_path, plan)
    c.ensure_lane(LLAMA, 4)
    c.ctx.stage = c.ctx.phase = "T"
    c.ctx.episode, c.ctx.tick = "ep", 7
    c.ctx.log = C.CallLog(tmp_path / "live.jsonl")
    return c, b, C.CampaignBackend(c.ctx)


def test_call_is_logged_at_once_with_every_field(tmp_path):
    c, b, be = _backend(tmp_path)
    be.generate("FACTS F1 F2 case RCA-01", role="diagnostician", max_tokens=64)
    # read from disk while the log is still open: the record is already there
    recs = [json.loads(ln) for ln in (tmp_path / "live.jsonl").read_text().splitlines()]
    assert len(recs) == 1
    r = recs[0]
    assert REQUIRED_CALL_FIELDS <= set(r)
    assert r["tick"] == 7 and r["episode"] == "ep" and r["lane"] == "npu"
    assert r["model_sha256"] == "5aa3ece50ab33d09a7181888a75f8755f924c662dc99626e7f45440adfeadcdb"
    assert r["server_flags"] == "-c 4096 -np 1 --device HTP0 -ngl 99 -fit off --cache-ram 0 -lv 4"
    assert r["prompt"] == "FACTS F1 F2 case RCA-01" and r["parse_status"] == "ok"
    assert r["stop_reason"] == "stop" and r["timings"]["prompt_ms"] > 0
    assert r["chip_temp"]["cpu_max_c"] == 33.0


def test_missing_server_timings_are_none(tmp_path):
    c, b, be = _backend(tmp_path, {"no_timings": True})
    rep = be.generate("FACTS F1 RCA-01", role="diagnostician", max_tokens=64)
    r = json.loads((tmp_path / "live.jsonl").read_text().splitlines()[0])
    assert all(v is None for v in r["timings"].values())
    assert rep.ttft_ms is None and rep.decode_ms is None and rep.prefill_tokens is None


# ---- 3. reply cache ----------------------------------------------------------
def test_cache_hit_is_marked_and_has_no_timings(tmp_path):
    c, b, be = _backend(tmp_path)
    be.generate("same prompt RCA-01 F1", role="diagnostician", max_tokens=64)
    be.generate("same prompt RCA-01 F1", role="diagnostician", max_tokens=64)
    a, h = [json.loads(ln) for ln in (tmp_path / "live.jsonl").read_text().splitlines()]
    assert b.server.chat_calls == 1                       # the second never reached the server
    assert a["cache_hit"] is False and h["cache_hit"] is True
    assert h["timings"] is None and h["latency_ms"] is None and h["chip_temp"] is None
    assert h["raw_reply"] == a["raw_reply"]


def test_cache_key_covers_model_flags_sampling_and_prompt():
    k = C.ReplyCache.key
    base = k("sha", "-c 4096", {"temperature": 0.0, "max_tokens": 256}, "p")
    assert base == k("sha", "-c 4096", {"max_tokens": 256, "temperature": 0.0}, "p")
    assert base != k("sha2", "-c 4096", {"temperature": 0.0, "max_tokens": 256}, "p")
    assert base != k("sha", "-c 2048", {"temperature": 0.0, "max_tokens": 256}, "p")
    assert base != k("sha", "-c 4096", {"temperature": 0.1, "max_tokens": 256}, "p")
    assert base != k("sha", "-c 4096", {"temperature": 0.0, "max_tokens": 256}, "q")


def test_cache_hits_are_excluded_from_timing_statistics(tmp_path):
    c, b = mk(tmp_path)
    s1 = one_episode(c, "round1")
    n_server = b.server.chat_calls
    s2 = one_episode(c, "again")                          # same model, flags, prompts
    assert b.server.chat_calls == n_server                # every call was a cache hit
    assert s2["meta"]["n_cache_hits"] == s2["meta"]["n_calls"] > 0
    run1, run2 = c.load_run("B", "round1", LLAMA, "dev_A01_fcv_seize"), \
        c.load_run("B", "again", LLAMA, "dev_A01_fcv_seize")
    st1, st2 = call_stats([run1]), call_stats([run2])
    assert st1["server_prefill_tok_s"] == 900.0 and st1["projected_verified_s"] is not None
    assert st2["server_prefill_tok_s"] is None and st2["server_decode_tok_s"] is None
    assert st2["projected_verified_s"] is None and st2["diag_latency_ms_mean"] is None
    assert strip(s1["evaluation"]) == strip(s2["evaluation"])     # same decisions, replayed


# ---- 4. infrastructure failures ---------------------------------------------
@pytest.mark.parametrize("fault", ["http500", "garbage", "close", "hang"])
def test_infra_failure_is_retried_and_never_scored(tmp_path, fault):
    clean, _ = mk(tmp_path, {}, "clean")
    want = one_episode(clean)
    c, b = mk(tmp_path, {"fail_calls": {"5": fault}}, "faulty")
    c.ctx.timeout_s = 1.0                                 # "hang" sleeps 3 s
    got = one_episode(c)
    j = c.manifest["jobs"]["B/round1/llama32-3b/dev_A01_fcv_seize"]
    assert j["status"] == "complete" and j["attempts"] == 2
    assert len(b.starts) == 2                             # the lane was restarted
    run = c.load_run("B", "round1", LLAMA, "dev_A01_fcv_seize")
    statuses = {e["status"] for a in run["assessments"] for e in a["envelopes"]}
    assert statuses <= {"ok", "invalid_schema"}           # no error/timeout envelope: no fallback
    assert not any(a.get("degraded_mode") for a in run["assessments"])
    assert strip(got["evaluation"]) == strip(want["evaluation"])
    assert "INFRASTRUCTURE" in c.last_error


def test_adb_drop_is_infrastructure(tmp_path):
    c, b = mk(tmp_path, {})
    b.plan["adb_drop_at_temp"] = b.temp_reads + 6         # a per-call temperature read fails
    one_episode(c)
    assert c.manifest["jobs"]["B/round1/llama32-3b/dev_A01_fcv_seize"]["attempts"] == 2


def test_stops_cleanly_after_three_retries_and_resumes(tmp_path):
    c, b = mk(tmp_path, {"fail_from": 3})
    with pytest.raises(C.InfraFailure):
        one_episode(c)
    jid = "B/round1/llama32-3b/dev_A01_fcv_seize"
    j = c.manifest["jobs"][jid]
    assert j["status"] == "incomplete" and j["attempts"] == 4      # 1 try + 3 retries
    assert not Path(c.prefix("B", "round1", LLAMA, "dev_A01_fcv_seize") + ".summary.json").exists()
    status = (c.out / "STATUS.md").read_text()
    assert "STOPPED at " + jid in status and "INFRASTRUCTURE" in status
    saved = json.loads(c.mpath.read_text())["jobs"][jid]
    assert saved["status"] == "incomplete"                # where it stopped is on disk
    c2, b2 = mk(tmp_path, {})                             # board fixed, relaunch
    one_episode(c2)
    assert c2.manifest["jobs"][jid]["status"] == "complete"


def test_run_all_returns_infra_exit_code(tmp_path):
    b = FakeBoard({"fail_from": 1})
    c = C.Campaign(tmp_path / "out", b, commit=False, baseline_dir=tmp_path / "base")
    assert c.run_all() == C.EXIT_INFRA
    assert not any(j["status"] == "complete" for j in c.manifest["jobs"].values())


def test_unknown_model_file_is_fatal_not_infra(tmp_path):
    c, b = mk(tmp_path)
    c.models["bad"] = {"tag": "bad", "file": "../geniex/models/Llama-3.2-3B-Instruct-Q4_0.gguf"}
    from bench.board import ModelNotAllowed
    with pytest.raises(ModelNotAllowed):
        c.ensure_lane("bad", 4)
    assert b.starts == []


# ---- 5. thermal gate ---------------------------------------------------------
def test_thermal_gate_waits_until_within_5c_of_idle(tmp_path):
    #            idle x2   then: hot, hot, 38.5 (over by 0.5), 38.0 (= idle + 5: pass)
    c, b = mk(tmp_path, {"temps": [33.0, 33.2, 50.0, 45.0, 38.3, 38.2]})
    assert c.manifest["idle_temp"]["max_c"] == 33.2
    b.slept.clear()
    g = c.thermal_gate()
    assert g == {"waited_s": 45.0, "limit_c": 38.2, "temp_c": 38.2, "timed_out": False}
    assert b.slept == [15, 15, 15]


def test_thermal_gate_gives_up_after_15_minutes(tmp_path):
    c, b = mk(tmp_path, {"temps": [33.0, 33.0, 60.0]})
    b.slept.clear()
    g = c.thermal_gate()
    assert g["timed_out"] is True and g["waited_s"] == 900 and sum(b.slept) == 900


# ---- 1. manifest / resume ----------------------------------------------------
def test_complete_episode_is_skipped_incomplete_is_redone(tmp_path):
    c, b = mk(tmp_path)
    one_episode(c)
    n = b.server.chat_calls
    c2, b2 = mk(tmp_path)                                 # a restart
    one_episode(c2)
    assert b2.server.chat_calls == 0                      # skipped: nothing was called
    summary = Path(c2.prefix("B", "round1", LLAMA, "dev_A01_fcv_seize") + ".summary.json")
    summary.unlink()                                      # no summary = not complete
    c3, b3 = mk(tmp_path)
    assert not c3.is_complete("B/round1/llama32-3b/dev_A01_fcv_seize")
    one_episode(c3)
    assert summary.exists() and c3.manifest["jobs"]["B/round1/llama32-3b/dev_A01_fcv_seize"]["attempts"] == 2
    assert n > 0


def test_reporting_order_is_round_robin_by_family():
    eps = sorted(p.name for p in (ROOT / "data/episodes").iterdir())
    order = C.Campaign.reporting_order(eps)
    assert len(order) == 30 and sorted(order) == eps
    assert [e.split("_")[1][0] for e in order[:12]] == list("NABCDENABCDE")
    assert order[0] == "ep_N01_normal" and order[6] == "ep_N02_normal"


def test_on_tick_hook_does_not_change_a_mock_run():
    import yaml
    from bench.harness import Episode, run_episode
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    ep = Episode(ROOT / DEV / "dev_A01_fcv_seize")
    seen = []
    a, b = run_episode(ep, cfg), run_episode(ep, cfg, on_tick=seen.append)
    assert strip(a) == strip(b) and seen == sorted(seen) and len(seen) == a["n_ticks"]


# ---- 7A. screening and the option C rule -------------------------------------
def _stage_a(tmp_path, models):
    b = FakeBoard({"models": models})
    c = C.Campaign(tmp_path / "out", b, commit=False, baseline_dir=tmp_path / "base")
    c.measure_idle()
    try:
        c.stage_a()
        stop = None
    except C.CampaignStop as e:
        stop = str(e)
    return c, b, stop


def test_screening_shapes_and_every_passing_model_gets_dev_runs(tmp_path):
    c, b, stop = _stage_a(tmp_path, MODELS_PLAN)
    a = c.manifest["stages"]["A"]
    assert stop is None and a["passing"] == ["llama32-3b", "qwen3-1.7b", "gemma3-1b-qat"]
    # HUMAN DECISION 2026-10-04 (replaces option C): every passing model, in candidate order
    assert a["dev_models"] == ["llama32-3b", "qwen3-1.7b", "gemma3-1b-qat"] and "second" not in a
    assert c.dev_models() == a["dev_models"]
    assert a["checks"]["qwen25-0.5b"] == {"htp0_all_layers": False, "gguf_all_q4_0_q8_0": True}
    s = json.loads(Path(c.prefix("A", "A", LLAMA, "screen") + ".summary.json").read_text())
    by = {}
    for x in s["calls"]:
        by.setdefault(x["role"], []).append(x)
    assert {r: len(v) for r, v in by.items()} == {"screen_diag": 5, "screen_ver": 5}
    assert all(x["prompt_n"] == 700 for x in by["screen_diag"])
    assert all(x["prompt_n"] == 350 for x in by["screen_ver"])
    assert all(x["predicted_n"] <= 60 for x in by["screen_diag"])
    # measured = mean server time of the 700/60 calls + mean of the 350/30 calls
    want = sum(sum(x["prompt_ms"] + x["predicted_ms"] for x in v) / len(v) for v in by.values()) / 1000
    assert s["measured_verified_s"] == round(want, 3)
    assert s["projected_verified_s"] == 6.79              # 1050/900 + 90/16
    calls = [json.loads(ln) for ln in gzip.open(c.prefix("A", "A", LLAMA, "screen") + ".calls.jsonl.gz", "rt")]
    assert len(calls) == 11 and not any(r["cache_hit"] for r in calls)   # warm-up + 10, never cached
    # every model: an offload-confirmation launch at -lv 4
    assert all((m["file"], 4) in b.starts for m in C.MODELS)


def test_qwen3_think_block_fails_its_screening(tmp_path):
    plan = dict(MODELS_PLAN, **{"qwen3-1.7b": {"decode_tok_s": 99, "prefill_tok_s": 2000, "think": True}})
    c, b, stop = _stage_a(tmp_path, plan)
    a = c.manifest["stages"]["A"]
    assert "qwen3-1.7b" not in a["passing"] and c.dev_models() == ["llama32-3b", "gemma3-1b-qat"]
    s = json.loads(Path(c.prefix("A", "A", "qwen3-1.7b", "screen") + ".summary.json").read_text())
    assert s["think_ok"] is False and s["think_blocks_in_calls"]
    assert s["thinking_off_mechanism"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_stop_if_llama_fails_screening(tmp_path):
    c, b, stop = _stage_a(tmp_path, {LLAMA: {"gguf_ok": False}})
    assert stop and "llama32-3b failed screening" in stop


def test_stop_if_no_other_model_passes(tmp_path):
    bad = {"layers": [1, 2]}
    c, b, stop = _stage_a(tmp_path, {"qwen3-1.7b": bad, "gemma3-1b-qat": bad, "qwen25-0.5b": bad})
    assert stop and "no other model passed" in stop


# ---- 7B/7C. dev rounds, drop rule, model choice ------------------------------
def _through_b(tmp_path, models):
    c, b, stop = _stage_a(tmp_path, models)
    assert stop is None
    try:
        c.stage_b()
        stop = None
    except C.CampaignStop as e:
        stop = str(e)
    return c, b, stop


def test_rounds_are_interleaved_and_a_model_is_dropped_after_round_1(tmp_path):
    plan = dict(MODELS_PLAN, **{"gemma3-1b-qat": {"decode_tok_s": 46, "prefill_tok_s": 600,
                                                  "broken_first": 0.35}})
    c, b, stop = _through_b(tmp_path, plan)
    bj = [j for j in c.manifest["jobs"] if j.startswith("B/")]
    assert bj[:3] == ["B/round1/llama32-3b/dev_A01_fcv_seize", "B/round1/qwen3-1.7b/dev_A01_fcv_seize",
                      "B/round1/gemma3-1b-qat/dev_A01_fcv_seize"]
    st = c.manifest["stages"]["B"]
    assert stop is None and list(st["dropped"]) == ["gemma3-1b-qat"]
    assert 0.30 < st["round1"]["gemma3-1b-qat"]["broken_json_first_reply"] < 0.60
    assert st["survivors"] == [LLAMA, "qwen3-1.7b"]
    assert bj[3:] == ["B/round2/llama32-3b/dev_B01_tube_leak", "B/round2/qwen3-1.7b/dev_B01_tube_leak",
                      "B/round3/llama32-3b/dev_C02_feeder_trip", "B/round3/qwen3-1.7b/dev_C02_feeder_trip"]


def test_interleaving_without_a_drop(tmp_path):
    c, b, stop = _through_b(tmp_path, MODELS_PLAN)
    bj = [j.split("/", 1)[1] for j in c.manifest["jobs"] if j.startswith("B/")]
    assert bj == [f"round{r}/{t}/{e}" for r, e in enumerate(C.ROUNDS, 1)
                  for t in (LLAMA, "qwen3-1.7b", "gemma3-1b-qat")]


def test_stop_if_every_model_is_dropped(tmp_path):
    bad = {"broken_first": 0.6, "broken_repair": 0.9}
    plan = dict(MODELS_PLAN, **{LLAMA: bad, "qwen3-1.7b": bad, "gemma3-1b-qat": bad})
    c, b, stop = _through_b(tmp_path, plan)
    assert stop and "every model dropped" in stop
    assert not any("round2" in j for j in c.manifest["jobs"])


def test_stage_c_stops_when_model_choice_passes_no_model(tmp_path):
    # valid JSON (no drop after round 1) but too slow for model_choice's 10 s limit:
    # projected = 1050 / 100 + 90 / 5 = 28.5 s
    slow = {"prefill_tok_s": 100, "decode_tok_s": 5}
    plan = {m["tag"]: dict(slow) for m in C.MODELS}
    c, b, stop = _through_b(tmp_path, plan)
    surv = c.manifest["stages"]["B"]["survivors"]
    assert stop is None and surv == [m["tag"] for m in C.MODELS]     # all 4 pass screening, nobody dropped
    with pytest.raises(C.CampaignStop, match="no model passes"):
        c.stage_c()
    st = c.manifest["stages"]["C"]
    assert st["pick"] is None and st["passing"] == []
    assert all(st["gates"][t]["projected_verified_s"] is False for t in surv)


# ---- 9. full dry run A-E, and kill -9 + resume --------------------------------
def _cli(out, base, plan):
    env = dict(os.environ, FAKE_BOARD_PLAN=json.dumps(plan), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-m", "bench.campaign", "all", "--out", str(out),
                           "--board", "tests.fake_board:make", "--no-commit",
                           "--baseline-dir", str(base)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=600)


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("full")
    p = _cli(d / "out", d / "base", {"models": MODELS_PLAN})
    return d, p


def test_full_dry_run_stages_a_to_e(full_run):
    d, p = full_run
    assert p.returncode == C.EXIT_OK, p.stderr[-2000:]
    m = json.loads((d / "out/manifest.json").read_text())
    assert all(m["stages"][s]["done"] for s in "ABCDE")
    assert all(j["status"] == "complete" for j in m["jobs"].values())
    assert m["stages"]["A"]["dev_models"] == [LLAMA, "qwen3-1.7b", "gemma3-1b-qat"]
    assert m["stages"]["B"]["survivors"] == [LLAMA, "qwen3-1.7b", "gemma3-1b-qat"]
    assert sum(k.startswith("B/") for k in m["jobs"]) == 9           # 3 models x 3 rounds
    pick = m["stages"]["C"]["pick"]
    assert pick in (LLAMA, "qwen3-1.7b", "gemma3-1b-qat") and (d / "out/C/model_choice.txt").exists()
    # D: 30 reporting episodes, round-robin, baseline written under a new name
    dj = [j.rsplit("/", 1)[1] for j in m["jobs"] if j.startswith("D/")]
    assert len(dj) == 30 and dj[:6] == ["ep_N01_normal", "ep_A01_fcv_seize", "ep_B01_tube_leak",
                                        "ep_C01_wet_coal", "ep_D01_high_cv_coal", "ep_E01_fouling_drift"]
    base = json.loads((d / "base" / f"single_v3_real_{pick}_npu_summary.json").read_text())
    assert base["summary"]["n_episodes"] == 30 and len(base["per_episode"]) == 30
    assert all(e["S3_energy_mwh"] is None for e in base["per_episode"])
    # E: runs only because the 3-episode verdict is INCONCLUSIVE; reuses the 3, runs the other 10
    assert m["stages"]["C"]["ranking_verdict_3_episodes"] == "INCONCLUSIVE"
    e = m["stages"]["E"]
    assert e["run"] and e["reused_model_choice_episodes"] and len(e["episodes_run"]) == 10
    assert not set(e["episodes_run"]) & set(C.ROUNDS)
    status = (d / "out/STATUS.md").read_text()
    assert "stage: finished" in status and "last error: none" in status


def test_kill_minus_9_mid_episode_then_resume_is_identical(full_run, tmp_path):
    ref, p_ref = full_run
    assert p_ref.returncode == 0
    # stage A makes 4 x 11 = 44 chat calls; call 74 is the 30th of the first stage-B episode
    p = _cli(tmp_path / "out", tmp_path / "base", {"models": MODELS_PLAN, "sigkill_at_call": 74})
    assert p.returncode == -9
    m = json.loads((tmp_path / "out/manifest.json").read_text())
    jid = "B/round1/llama32-3b/dev_A01_fcv_seize"
    assert m["jobs"][jid]["status"] == "incomplete"
    assert not (tmp_path / "out/B/llama32-3b/round1.dev_A01_fcv_seize.summary.json").exists()
    live = (tmp_path / "out/tmp/B__round1__llama32-3b__dev_A01_fcv_seize.attempt1.calls.jsonl").read_text()
    assert len(live.splitlines()) == 29                   # every finished call was already on disk
    p2 = _cli(tmp_path / "out", tmp_path / "base", {"models": MODELS_PLAN})     # relaunch
    assert p2.returncode == 0, p2.stderr[-2000:]
    m2 = json.loads((tmp_path / "out/manifest.json").read_text())
    assert m2["jobs"][jid]["attempts"] == 2
    assert all(j["attempts"] == 1 for k, j in m2["jobs"].items() if k != jid)   # the rest ran once
    redo = json.loads((tmp_path / "out/B/llama32-3b/round1.dev_A01_fcv_seize.summary.json").read_text())
    assert redo["meta"]["n_cache_hits"] == 29
    # the killed attempt's log was kept, not overwritten, and is identical to what was on disk
    kept = tmp_path / "out/attempts/B__round1__llama32-3b__dev_A01_fcv_seize.attempt1.calls.jsonl.gz"
    assert gzip.decompress(kept.read_bytes()).decode() == live
    assert m2["jobs"][jid]["attempt_logs"] == [str(kept.relative_to(tmp_path / "out"))]
    # results identical to the uninterrupted run, timings and clocks aside
    files = sorted(str(f.relative_to(ref / "out")) for f in (ref / "out").rglob("*")
                   if f.name.endswith((".summary.json", ".run.json.gz")) or f.parent.name in "CDE"
                   and f.suffix == ".json")
    assert len(files) > 90
    for rel in files:
        a, b = ref / "out" / rel, tmp_path / "out" / rel
        la, lb = (json.loads(gzip.decompress(x.read_bytes()) if x.suffix == ".gz" else x.read_text())
                  for x in (a, b))
        assert strip(la) == strip(lb), rel
    assert strip(json.loads((ref / "out/manifest.json").read_text())["stages"]) == strip(m2["stages"])


# ---- NPU confirmation from the startup log -----------------------------------
# line formats copied from the board's -lv 4 log (logs/smoke_B_diag_sched_npu.log)
_LOG = """0.00.150.913 I llama_model_loader: - type  f32:   58 tensors
0.00.150.914 I llama_model_loader: - type q4_0:  196 tensors
0.00.150.914 I llama_model_loader: - type q8_0:    1 tensors
{out}0.00.496.196 I load_tensors: offloading 27 repeating layers to GPU
0.00.496.196 I load_tensors: offloaded {n}/29 layers to GPU
0.00.496.201 I load_tensors:          CPU model buffer size =   399.23 MiB
0.00.496.203 I load_tensors:         HTP0 model buffer size =  {buf} MiB
"""
_OUT = "0.00.496.177 I load_tensors: offloading output layer to GPU\n"


def test_offload_check_needs_every_layer_the_output_layer_and_a_nonzero_buffer():
    good = C.parse_offload(_LOG.format(out=_OUT, n=29, buf="1911.90"))
    assert good["htp0_all_layers"] is True
    assert (good["layers_offloaded"], good["layers_total"], good["htp0_model_buffer_mib"]) == (29, 29, 1911.9)
    assert good["loader_type_counts"] == {"f32": 58, "q4_0": 196, "q8_0": 1}
    assert C.parse_offload(_LOG.format(out=_OUT, n=28, buf="1911.90"))["htp0_all_layers"] is False
    assert C.parse_offload(_LOG.format(out="", n=29, buf="1911.90"))["htp0_all_layers"] is False
    assert C.parse_offload(_LOG.format(out=_OUT, n=29, buf="0.00"))["htp0_all_layers"] is False
    assert C.parse_offload("llama_server: listening")["htp0_all_layers"] is False     # default log level


# ---- the real command line: infrastructure stop (exit 2) and rule stop (exit 3) ---
def test_cli_infra_fault_retries_three_times_then_exits_2(tmp_path):
    # stage A makes 4 x 11 = 44 chat calls; from call 50 on, every call fails
    p = _cli(tmp_path / "out", tmp_path / "base", {"models": MODELS_PLAN, "fail_from": 50})
    assert p.returncode == C.EXIT_INFRA, p.stderr[-1500:]
    assert "Traceback" not in p.stderr
    m = json.loads((tmp_path / "out/manifest.json").read_text())
    j = m["jobs"]["B/round1/llama32-3b/dev_A01_fcv_seize"]
    assert j["status"] == "incomplete" and j["attempts"] == 4          # 1 try + 3 retries
    status = (tmp_path / "out/STATUS.md").read_text()
    assert "STOPPED at B/round1/llama32-3b/dev_A01_fcv_seize" in status
    assert "INFRASTRUCTURE" in status and "HTTP 500" in status


def test_cli_rule_stop_exits_3(tmp_path):
    p = _cli(tmp_path / "out", tmp_path / "base", {"models": {LLAMA: {"gguf_ok": False}}})
    assert p.returncode == C.EXIT_STOP, p.stderr[-1500:]
    status = (tmp_path / "out/STATUS.md").read_text()
    assert "STOPPED by a committed rule" in status and "llama32-3b failed screening" in status


# ---- a failed attempt's call log is kept --------------------------------------
def test_failed_attempt_log_is_kept_not_overwritten(tmp_path):
    c, b = mk(tmp_path, {"fail_calls": {"5": "http500"}})
    one_episode(c)
    jid = "B/round1/llama32-3b/dev_A01_fcv_seize"
    j = c.manifest["jobs"][jid]
    assert j["attempts"] == 2 and len(j["attempt_logs"]) == 1
    old = [json.loads(ln) for ln in gzip.open(c.out / j["attempt_logs"][0], "rt")]
    assert len(old) == 5 and old[-1]["status"] == "infra" and "HTTP 500" in old[-1]["error"]
    assert [r["status"] for r in old[:4]] == ["ok"] * 4
    assert not list(c.tmp.glob("*.calls.jsonl"))          # nothing left behind


def test_call_log_refuses_to_overwrite(tmp_path):
    f = tmp_path / "x.calls.jsonl"
    f.write_text("earlier record\n")
    with pytest.raises(FileExistsError):
        C.CallLog(f)
    assert f.read_text() == "earlier record\n"


def test_status_attempt_number_comes_from_the_manifest(tmp_path):
    jid = "B/round1/llama32-3b/dev_A01_fcv_seize"
    c, b = mk(tmp_path, {"fail_from": 1})
    with pytest.raises(C.InfraFailure):
        one_episode(c)                                     # attempts 1-4 in this process
    seen = []
    c2, b2 = mk(tmp_path, {})                               # a relaunch: a new process

    def spy(model="", note=""):
        seen.append(note)
    c2.write_status = spy
    one_episode(c2)
    assert f"running {jid} (attempt 5)" in seen            # not "attempt 1"


# ---- memory guard and peak temperature (human decisions 3 and 4) --------------
def test_memory_guard_restarts_the_lane_before_an_episode_when_under_2gb(tmp_path):
    c, b = mk(tmp_path, {"mem_available_kb": [2_097_151, 6_000_000]})   # 1 kB under 2 GiB
    s = one_episode(c)
    g = s["meta"]["memory_guard"]
    assert g["restarted"] is True and g["before"]["mem_available_kb"] == 2_097_151
    assert g["after"]["mem_available_kb"] == 6_000_000 and g["before"]["server_rss_kb"] == 530_000
    assert len(b.starts) == 2                                 # the first launch + the guard's restart
    assert c.manifest["lane_restarts"][0]["before_job"] == "B/round1/llama32-3b/dev_A01_fcv_seize"


def test_memory_guard_does_nothing_at_2gb_and_never_restarts_inside_an_episode(tmp_path):
    c, b = mk(tmp_path, {"mem_available_kb": [2_097_152]})               # exactly 2 GiB: not under
    s = one_episode(c)
    assert s["meta"]["memory_guard"]["restarted"] is False and "lane_restarts" not in c.manifest
    assert len(b.starts) == 1
    assert b.mem_reads == 1                                   # read once, before the episode only


def test_peak_in_episode_temperature_is_reported(tmp_path):
    #            idle x2, gate, then start-of-episode and per-call reads; 93.8 once, mid-episode
    c, b = mk(tmp_path, {"temps": [33.0, 33.0, 34.0, 35.0, 50.0, 93.8, 60.0]})
    s = one_episode(c)
    p = s["meta"]["chip_temp_peak"]
    assert p["cpu_max_c"] == 93.8 and p["npu_max_c"] == 92.8
    assert p["n_reads"] == s["meta"]["n_calls"] + 2           # start + every call + end


# ---- resuming a results folder made under option C (two dev models, stage C: no pick) ----
def test_option_c_results_are_extended_not_rerun(tmp_path, monkeypatch):
    slow = {"prefill_tok_s": 100, "decode_tok_s": 5}                 # too slow for model_choice: no pick
    plan = {"models": {LLAMA: slow, "qwen25-0.5b": dict(slow, decode_tok_s=6)}}   # qwen3, gemma: fast
    # 1. the old rule: only the 3B and one other model get dev runs; stage C then stops
    monkeypatch.setattr(C.Campaign, "dev_models", lambda self: [LLAMA, "qwen25-0.5b"])
    old = C.Campaign(tmp_path / "out", FakeBoard(plan), commit=False, baseline_dir=tmp_path / "base")
    assert old.run_all() == C.EXIT_STOP
    m0 = json.loads(old.mpath.read_text())
    assert m0["stages"]["B"]["models"] == [LLAMA, "qwen25-0.5b"] and m0["stages"]["C"]["pick"] is None
    old_c = (tmp_path / "out/C/model_choice.json").read_text()
    monkeypatch.undo()
    # 2. relaunch with the new rule on the same folder
    b = FakeBoard(plan)
    new = C.Campaign(tmp_path / "out", b, commit=False, baseline_dir=tmp_path / "base")
    rc = new.run_all()
    m = json.loads(new.mpath.read_text())
    # the 4 screenings and the 6 finished dev episodes were not run again
    assert all(j["attempts"] == 1 for j in m["jobs"].values())
    done_before = {k for k in m0["jobs"]}
    ran_models = {f for f, _ in b.starts}
    assert ran_models == {C.MODELS[1]["file"], C.MODELS[2]["file"]} or rc == C.EXIT_OK and \
        {C.MODELS[1]["file"], C.MODELS[2]["file"]} <= ran_models     # qwen3, gemma (+ the pick, later stages)
    new_b = sorted(k for k in m["jobs"] if k.startswith("B/") and k not in done_before)
    assert new_b == sorted(f"B/round{r}/{t}/{e}" for r, e in enumerate(C.ROUNDS, 1)
                           for t in ("qwen3-1.7b", "gemma3-1b-qat"))
    # stage B now covers all four; the old round-1 rates were kept, the new ones added
    assert m["stages"]["B"]["models"] == [x["tag"] for x in C.MODELS]
    assert set(m["stages"]["B"]["round1"]) == set(m["stages"]["B"]["models"])
    assert m["stages"]["B"]["round1"][LLAMA] == m0["stages"]["B"]["round1"][LLAMA]
    # the first stage C result is kept, in the manifest and on disk, and stage C was recomputed
    assert m["superseded"][0]["stage"] == "C" and m["superseded"][0]["record"]["pick"] is None
    assert (tmp_path / "out/C/model_choice.superseded-1.json").read_text() == old_c
    assert set(m["stages"]["C"]["gates"]) == set(m["stages"]["B"]["models"])
    assert m["stages"]["C"]["pick"] in ("qwen3-1.7b", "gemma3-1b-qat") and rc == C.EXIT_OK


# ---- human override of the stage C stop (2026-10-05) --------------------------
SLOW = {"prefill_tok_s": 100, "decode_tok_s": 5}                     # fails model_choice's 10 s limit


def _run(tmp_path, plan, override, name="out"):
    b = FakeBoard({"models": plan})
    c = C.Campaign(tmp_path / name, b, commit=False, baseline_dir=tmp_path / "base")
    return c, b, c.run_all()


def test_committed_value_of_the_override_is_the_3b():
    # read from the source: the autouse fixture sets the live value to None for the other tests
    src = (ROOT / "bench/campaign.py").read_text()
    assert '\nPICK_OVERRIDE: str | None = "llama32-3b"\n' in src


def test_override_continues_past_a_no_pick_and_keeps_the_rule_result(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "PICK_OVERRIDE", LLAMA)
    c, b, rc = _run(tmp_path, {m["tag"]: dict(SLOW) for m in C.MODELS}, LLAMA)
    st = json.loads(c.mpath.read_text())["stages"]
    assert rc == C.EXIT_OK and st["C"]["pick"] == LLAMA
    assert st["C"]["rule_pick"] is None and st["C"]["passing"] == []          # the rule's own result
    assert st["C"]["override"]["failed_constraints"] == ["projected_verified_s"]
    assert st["D"]["model"] == LLAMA
    base = json.loads((tmp_path / "base" / f"single_v3_real_{LLAMA}_npu_summary.json").read_text())
    assert base["model_choice_override"]["model"] == LLAMA and "did not pass" in base["model_choice_override"]["note"]


def test_override_is_ignored_when_the_rule_picks_a_model(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "PICK_OVERRIDE", LLAMA)
    c, b, stop = _through_b(tmp_path, MODELS_PLAN)
    c.stage_c()
    st = c.manifest["stages"]["C"]
    assert st["pick"] is not None and st["pick"] == st["rule_pick"] and st["override"] is None


def test_override_cannot_pick_a_model_that_was_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "PICK_OVERRIDE", "gemma3-1b-qat")
    plan = {m["tag"]: dict(SLOW) for m in C.MODELS}
    plan["gemma3-1b-qat"] = dict(SLOW, broken_first=0.6, broken_repair=0.9)   # dropped after round 1
    c, b, rc = _run(tmp_path, plan, "gemma3-1b-qat")
    st = json.loads(c.mpath.read_text())["stages"]
    assert rc == C.EXIT_STOP and st["C"]["pick"] is None and st["C"]["override"] is None
    assert "gemma3-1b-qat" in st["B"]["dropped"]


def test_override_applies_to_a_campaign_already_stopped_at_stage_c(tmp_path, monkeypatch):
    plan = {m["tag"]: dict(SLOW) for m in C.MODELS}
    c0, b0, rc0 = _run(tmp_path, plan, None)                          # as committed: stop, exit 3
    assert rc0 == C.EXIT_STOP
    mc_before = (tmp_path / "out/C/model_choice.json").read_text()
    n_jobs = len(json.loads(c0.mpath.read_text())["jobs"])
    monkeypatch.setattr(C, "PICK_OVERRIDE", LLAMA)
    c1, b1, rc1 = _run(tmp_path, plan, LLAMA)                         # relaunch with the override
    m = json.loads(c1.mpath.read_text())
    assert rc1 == C.EXIT_OK and m["stages"]["C"]["pick"] == LLAMA and m["stages"]["C"]["rule_pick"] is None
    assert "override_applied" in m["stages"]["C"]
    # stage C was not recomputed and nothing finished was rerun
    assert (tmp_path / "out/C/model_choice.json").read_text() == mc_before
    assert not (tmp_path / "out/C/model_choice.superseded-1.json").exists() and "superseded" not in m
    assert all(j["attempts"] == 1 for j in m["jobs"].values())
    assert len(m["jobs"]) > n_jobs and sum(k.startswith("D/") for k in m["jobs"]) == 30
    assert {f for f, _ in b1.starts} == {C.MODELS[0]["file"]}         # only the 3B was ever loaded
