"""Hypotheses no retrieved case has carried for N ticks are retired and logged."""
from pathlib import Path

import yaml

from fieldmind.agent import world_model as wmod
from fieldmind.kb.stores import CaseLibrary

ROOT = Path(__file__).resolve().parent.parent
LIB = CaseLibrary(ROOT / "data/kb/case_library.json")
CASE = {c["case_id"]: c for c in LIB.cases}
N = 20   # DERIVED: 10-min signature slope window / 30 s tick. The config value may be null (off).

# an all-FLAT signature: nothing the six tags carry (the RCA-09/10/15 situation)
FLAT = {(t, "FLAT", "-"): 0.35 for t in ("steam_flow", "drum_pressure", "bed_temp_avg",
                                         "ms_temperature", "drum_level", "feed_water_flow")}


def retired_events(wm):
    return [e for e in wm.timeline if e.kind == "HYP_RETIRED"]


def live(wm):
    return {h.case_ref for h in wm.hypotheses if not h.retired}


def test_rca09_and_rca10_really_do_tie_on_an_all_flat_signature():
    """Precondition for the tie test: retrieval cannot tell them apart."""
    m = {c["case_id"]: c["score"] for c in LIB.match(FLAT, k=20)}
    assert m["RCA-09"] == m["RCA-10"]


def test_stale_tied_hypothesis_is_retired_exactly_at_n_and_logged_once():
    wm = wmod.new_world_model("ep_unit", [])
    wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-09"], CASE["RCA-10"]], tick=0, retire_after=N)
    assert live(wm) == {"RCA-09", "RCA-10"}
    for t in range(1, N):                      # only RCA-09 keeps being retrieved
        wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-09"]], tick=t, retire_after=N)
    assert live(wm) == {"RCA-09", "RCA-10"}, "retired early (tick N-1)"
    assert retired_events(wm) == []

    wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-09"]], tick=N, retire_after=N)
    assert live(wm) == {"RCA-09"}
    ev = retired_events(wm)
    assert len(ev) == 1 and ev[0].tick == N and "RCA-10" in ev[0].detail
    assert [h.case_ref for h in wmod.rank_hypotheses(wm)] == ["RCA-09"]
    assert [b["case_ref"] for b in wmod.belief_ranking(wm)] == ["RCA-09"]

    wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-09"]], tick=N + 1, retire_after=N)
    assert len(retired_events(wm)) == 1, "a retired hypothesis must not be re-logged"


def test_retrieval_again_revives_and_resets_the_counter():
    wm = wmod.new_world_model("ep_unit", [])
    wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-09"], CASE["RCA-10"]], tick=0, retire_after=N)
    wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-09"]], tick=N, retire_after=N)
    assert live(wm) == {"RCA-09"}
    wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-10"]], tick=N + 5, retire_after=N)
    assert "RCA-10" in live(wm)
    wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-09"]], tick=N + 5 + N - 1, retire_after=N)
    assert "RCA-10" in live(wm), "counter was not reset by the re-retrieval"


def test_disabled_when_none():
    wm = wmod.new_world_model("ep_unit", [])
    wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-10"]], tick=0)
    wmod.update_hypotheses(wm, [], FLAT, [CASE["RCA-09"]], tick=500)
    assert "RCA-10" in live(wm) and retired_events(wm) == []


# ------------------------------------------------- the config switch, end to end
def _a01_retirements(retire_after, upto=110):
    import pytest
    from fieldmind.agent import world_model as wm_
    from bench.harness import Episode, SensorWindow, build_agent
    ep_dir = ROOT / "data/episodes/ep_A01_fcv_seize"
    if not ep_dir.exists():
        pytest.skip("data/episodes is generated and gitignored")
    cfg = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
    cfg["llm"]["backend"] = "mock"
    cfg["agent"]["belief"]["retire_after_ticks"] = retire_after
    ep = Episode(ep_dir)
    orch, asset, *_ = build_agent(cfg, ep.notes, ep.records)
    win = SensorWindow(window_min=60.0, sample_period_s=cfg["checks"]["sample_period_s"])
    wm = wm_.new_world_model(ep.id, asset.equipment)
    for k in range(upto):
        for r in ep.samples_between(k * 30, (k + 1) * 30):
            win.append(r["t"], r)
        if win.ready(min_minutes=5.0):
            orch.tick(wm, win, k, "x", now_s=(k + 1) * 30)
    return [e for e in wm.timeline if e.kind == "HYP_RETIRED" and "not retrieved" in e.detail]


def test_null_in_config_switches_retirement_off_and_20_turns_it_on():
    assert _a01_retirements(None) == []
    assert len(_a01_retirements(20)) > 0
