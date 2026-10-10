"""The pre-registered ranking statistic (bench/model_choice.py) on a synthetic run
whose answer is worked out by hand below, plus the verdict rule's boundaries."""
from __future__ import annotations

from bench.model_choice import ranking_question, verdict

FACTS = [{"id": "F1"}, {"id": "F2"}]


def _tick(t, model, brk, status="ok", cites=("F1",), shown=None):
    env = {"agent": "diagnostician", "status": status, "cited_facts": list(cites),
           "payload": {"hypotheses": [model]} if model else {}}
    return {"tick": t, "facts": FACTS, "envelopes": [env],
            "hypotheses": [{"case_ref": shown or (model or {}).get("case_ref"),
                            "confidence": 0.6}],
            "belief_ranking": [{"case_ref": c, "cause": f"cause {c}", "log_odds": lo}
                               for c, lo in brk]}


def _run(target="RCA-01", ep="ep_x"):
    m = lambda c: {"cause": f"model says {c}", "case_ref": c}          # noqa: E731
    ticks = [
        _tick(5, m("RCA-11"), [("RCA-01", 2.0)]),                       # pre-onset: out
        _tick(30, m("RCA-11"), [("RCA-01", 2.0), ("RCA-11", 1.0)]),     # counted: B 1, M 0
        _tick(31, m("RCA-16"), [("RCA-01", 2.0)]),                      # same group: out
        _tick(32, m("RCA-14"), [("RCA-01", 3.0), ("RCA-11", 3.0)]),     # tie: B 0.5, M 0
        _tick(33, m("RCA-11"), [("RCA-01", 2.0)], status="invalid_schema"),   # out
        _tick(34, m("RCA-11"), [("RCA-01", 2.0)], cites=("F9",)),       # gate drops order: out
        _tick(35, {"cause": "something new"}, [("RCA-11", 1.0)]),       # model-only: B 0, M 0
        _tick(36, {"cause": "cause RCA-01"}, [("RCA-11", 1.0), ("RCA-01", 0.5)]),
        #  ^ resolved by cause to RCA-01 (as _merge does): M 1, B 0
    ]
    return {"episode_id": ep, "assessments": ticks,
            "ground_truth": {"family": "A", "root_cause_id": target, "fault_onset_t": 900}}


def test_population_and_scoring_worked_by_hand():
    r = ranking_question([_run()])["pooled"]
    assert r["n"] == 4
    assert r["model_only"] == 1
    assert r["p_belief"] == round((1 + 0.5 + 0 + 0) / 4, 3)
    assert r["p_model"] == round((0 + 0 + 0 + 1) / 4, 3)


def test_heldout_and_normal_episodes_are_excluded():
    runs = [_run(target="RCA-06", ep="held"), _run(ep="lib")]
    runs.append({"episode_id": "n", "assessments": _run()["assessments"],
                 "ground_truth": {"family": "N", "root_cause_id": "NONE"}})
    out = ranking_question(runs)
    assert out["pooled"]["n"] == 4 and set(out["per_episode"]) == {"lib"}


def test_shown_variant_reads_the_shown_rank1_not_the_envelope():
    run = _run()
    for a in run["assessments"]:
        a["hypotheses"][0]["case_ref"] = "RCA-01"     # display always shows the truth
    r = ranking_question([run], use_shown=True)["pooled"]
    # the shown variant needs no ok envelope; it disagrees only where belief's
    # leader is RCA-11 (ticks 35, 36), and there the shown RCA-01 is right
    assert (r["n"], r["p_model"], r["p_belief"]) == (2, 1.0, 0.0)
    r = ranking_question([run])["pooled"]
    assert r["n"] == 4                                # the model's own rank-1 is unchanged


def _res(n, pb, pm, eps):
    return {"pooled": {"n": n, "p_belief": pb, "p_model": pm},
            "per_episode": {f"e{i}": {"n": en, "p_belief": b, "p_model": m}
                            for i, (en, b, m) in enumerate(eps)}}


def test_verdict_rule_boundaries():
    assert verdict(_res(29, 0.9, 0.1, [])).startswith("INCONCLUSIVE")
    assert verdict(_res(30, 0.50, 0.40, [(15, 0.5, 0.4)])) == "BELIEF_DECIDES"
    assert verdict(_res(30, 0.40, 0.50, [(15, 0.4, 0.5)])) == "MODEL_DECIDES"
    assert verdict(_res(30, 0.49, 0.40, [])) == "INCONCLUSIVE"                  # margin < 0.10
    # one qualifying episode disagrees -> inconclusive
    assert verdict(_res(60, 0.6, 0.4, [(30, 0.8, 0.2), (12, 0.3, 0.5)])) == "INCONCLUSIVE"
    # an episode under 10 ticks does not join the consistency clause
    assert verdict(_res(60, 0.6, 0.4, [(30, 0.8, 0.2), (9, 0.3, 0.5)])) == "BELIEF_DECIDES"


# ---- model-choice rule (human-approved; projected time is a PROJECTION) ------
from bench.model_choice import FLOOR_DEFAULT, projected_s, selection  # noqa: E402


def test_projection_is_the_approved_formula_at_measured_rates():
    # (700/900 + 60/12) + (350/900 + 30/12) = 0.7778 + 5 + 0.3889 + 2.5 = 8.667 s
    assert projected_s(900.0, 12.0) == 8.67
    assert projected_s(None, 12.0) is None and projected_s(900.0, None) is None


def _col(g, faith=0.9, lat=5000.0, eps=(0.5, 0.5, 0.5), bf=0.0, ba=0.0, proj=8.0):
    return {"library_group_top1": g, "model_citation_faithfulness_raw": faith,
            "diag_latency_ms_mean": lat, "broken_json_first_reply": bf,
            "broken_json_after_repair": ba, "projected_verified_s": proj,
            "group_top1_by_episode": dict(zip(("A", "B", "C"), eps)), "is_mock": False}


OK = {"htp0_all_layers": True, "gguf_all_q4_0_q8_0": True}


def test_hard_constraints_each_exclude():
    cols = {"good": _col(0.6), "json1": _col(0.9, bf=0.11), "json2": _col(0.9, ba=0.03),
            "slow": _col(0.9, proj=10.01), "nohtp": _col(0.9), "noggufcheck": _col(0.9)}
    checks = {n: OK for n in cols if n not in ("nohtp", "noggufcheck")}
    checks["nohtp"] = {"htp0_all_layers": False, "gguf_all_q4_0_q8_0": True}
    sel = selection(cols, checks)                         # noggufcheck: no entry = UNVERIFIED
    assert sel["passing"] == ["good"] and sel["pick"] == "good"
    assert sel["gates"]["noggufcheck"]["htp0_all_layers"] == "UNVERIFIED"
    # exactly at the limits passes
    sel = selection({"edge": _col(0.6, bf=0.10, ba=0.02, proj=10.0)}, {"edge": OK})
    assert sel["pick"] == "edge"


def test_tie_bands_then_call_time():
    cols = {"a": _col(0.70, faith=0.90, lat=9000), "b": _col(0.66, faith=0.89, lat=4000),
            "c": _col(0.64, faith=0.99, lat=1000)}                # c is outside the 0.05 band
    sel = selection(cols, {n: OK for n in cols})
    assert set(sel["group_tied"]) == {"a", "b"}
    assert sel["pick"] == "b"                                       # faith within 0.02 -> faster
    cols["b"]["model_citation_faithfulness_raw"] = 0.87            # now outside the 0.02 band
    assert selection(cols, {n: OK for n in cols})["pick"] == "a"


def test_no_model_passes_means_stop_and_ask():
    sel = selection({"x": _col(0.9, bf=0.5)}, {"x": OK})
    assert sel["pick"] is None and "stop and ask" in sel["pick_reason"]


def test_flags_below_floor_and_single_episode_lead():
    cols = {"lead": _col(0.70, eps=(0.95, 0.60, 0.55)),          # ahead on A only
            "other": _col(0.60, eps=(0.25, 0.75, 0.80)),
            "low": _col(FLOOR_DEFAULT - 0.001, eps=(0.5, 0.5, 0.5))}
    flags = selection(cols, {n: OK for n in cols})["flags"]
    assert any(f.startswith("low:") and "BELOW" in f for f in flags)
    assert not any(f.startswith("lead:") or f.startswith("other:") for f in flags)
    assert any(f.startswith("lead over other") and "one episode A" in f for f in flags)
    # a mock column, when present, supplies the floor
    cols["mock"] = dict(_col(0.80), is_mock=True)
    flags = selection(cols, {n: OK for n in cols})["flags"]
    assert any(f.startswith("lead:") and "BELOW" in f for f in flags)
