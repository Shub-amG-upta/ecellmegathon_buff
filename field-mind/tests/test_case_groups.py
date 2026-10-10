"""Look-alike groups and the group / separating-case / belief metrics (Phase 0a)."""
import json


from bench import case_groups
from bench.evaluator import _row, t2_root_cause
from fieldmind.agent import world_model as wmod
from fieldmind.schemas import Hypothesis

# Stage 5 (reports/stage5_case_library.md item 2), restricted to the 13 library
# cases. Written out by hand here, NOT derived from the code under test.
STAGE5_LIBRARY_GROUPS = [
    {"RCA-14", "RCA-18"},             # cluster T (06 and 08 are held out)
    {"RCA-01", "RCA-16"},             # cluster W
    {"RCA-09", "RCA-10", "RCA-15"},   # cluster empty (17 is held out)
]


def _groups(d):
    return [set(g["members"]) for g in d["groups"]]


def test_committed_file_is_what_the_library_produces():
    assert json.loads(case_groups.OUT.read_text()) == case_groups.compute()


def test_library_groups_match_stage5_clusters():
    d = case_groups.compute()
    assert sorted(map(sorted, _groups(d))) == sorted(map(sorted, STAGE5_LIBRARY_GROUPS))
    # RCA-11 is alone in the library (RCA-12 and CBD-open are not library cases).
    assert "RCA-11" in d["singletons"]


def test_groups_follow_the_library_signatures(tmp_path):
    """Corrupt a signature in a COPY of the library: the groups must change."""
    lib = json.loads(case_groups.LIBRARY.read_text())
    for c in lib["cases"]:
        if c["case_id"] == "RCA-16":
            c["signature"] = {"drum_level": ["UP", "FAST"]}      # no longer a look-alike
    p = tmp_path / "lib.json"
    p.write_text(json.dumps(lib))
    d = case_groups.compute(p)
    assert {"RCA-01", "RCA-16"} not in _groups(d)
    assert "RCA-16" in d["singletons"]


def test_heldout_membership_is_labelled_cited():
    h = case_groups.compute()["heldout_membership"]
    assert h["RCA-06"]["group_id"] == "RCA-14+RCA-18"
    assert all(v["provenance"] == "CITED, not computed" for v in h.values())


# ---------------------------------------------------------------- scoring
def _run(target, ranks, belief, conf=0.9, disc="check X", onset=0):
    """One scored tick. ranks/belief are lists of case ids, rank-1 first."""
    hyps = [{"case_ref": c, "confidence": conf, "discriminator": disc} for c in ranks]
    brk = [{"case_ref": c, "log_odds": 1.0 - i, "confidence": 0.5} for i, c in enumerate(belief)]
    return {"ground_truth": {"root_cause_id": target, "fault_onset_t": onset},
            "assessments": [{"tick": 1, "hypotheses": hyps, "belief_ranking": brk}]}


def test_group_credit_without_top1():
    r = t2_root_cause(_run("RCA-14", ["RCA-18", "RCA-14"], ["RCA-18", "RCA-14"]))
    assert (r["top1"], r["group_top1"], r["sep_named"]) == (0.0, 1.0, 1.0)
    assert (r["belief_top1"], r["belief_group"], r["belief_top3"]) == (0.0, 1.0, 1.0)


def test_sep_needs_the_true_case_in_top3_with_a_discriminator():
    gone = t2_root_cause(_run("RCA-14", ["RCA-18", "RCA-03", "RCA-04"], ["RCA-18"]))
    assert (gone["group_top1"], gone["sep_named"]) == (1.0, 0.0)
    nodisc = t2_root_cause(_run("RCA-14", ["RCA-18", "RCA-14"], ["RCA-18"], disc=""))
    assert (nodisc["group_top1"], nodisc["sep_named"]) == (1.0, 0.0)


def test_wrong_group_scores_zero():
    r = t2_root_cause(_run("RCA-01", ["RCA-11", "RCA-01"], ["RCA-11"]))
    assert (r["group_top1"], r["sep_named"], r["belief_group"]) == (0.0, 0.0, 0.0)


def test_heldout_episode_only_scores_group_and_low_conf():
    r = t2_root_cause(_run("RCA-06", ["RCA-18", "RCA-14"], ["RCA-14"], conf=0.4))
    assert r["heldout"] is True
    assert r["group_top1"] == 1.0 and r["belief_group"] == 1.0
    assert r["low_conf_rate"] == 1.0
    for k in ("top1_strict", "top3_strict", "sep_named", "belief_top1", "belief_top3"):
        assert r[k] is None, k
    assert t2_root_cause(_run("RCA-06", ["RCA-18"], ["RCA-14"], conf=0.8))["low_conf_rate"] == 0.0


def test_tied_belief_top1_is_split_not_awarded_to_insertion_order():
    run = _run("RCA-14", ["RCA-14"], [])
    # two cases tied at the top log-odds; the target happens to be listed first
    run["assessments"][0]["belief_ranking"] = [
        {"case_ref": "RCA-14", "log_odds": 2.0, "confidence": 0.9},
        {"case_ref": "RCA-03", "log_odds": 2.0, "confidence": 0.9}]
    r = t2_root_cause(run)
    assert r["belief_top1"] == 1.0 and r["belief_top1_tiefair"] == 0.5
    assert r["belief_top_tie_rate"] == 1.0


def test_library_episode_has_no_low_conf_rate():
    assert t2_root_cause(_run("RCA-14", ["RCA-14"], ["RCA-14"]))["low_conf_rate"] is None


def test_not_applicable_when_no_target():
    assert t2_root_cause(_run(None, ["RCA-14"], ["RCA-14"])) == {"applicable": False}


def test_none_is_skipped_not_averaged_as_zero():
    eps = [{"T2_root_cause": {"top1_strict": None, "group_top1": 1.0}},
           {"T2_root_cause": {"top1_strict": 0.5, "group_top1": 0.0}}]
    row = _row(eps)
    assert row["top1"] == 0.5          # not 0.25
    assert row["group_top1"] == 0.5
    assert _row([{"T2_root_cause": {"top1_strict": None}}])["top1"] is None


# --------------------------------------------------------------- telemetry
def test_belief_ranking_orders_by_log_odds_and_skips_retired():
    wm = new_world_model_for_test()
    a = Hypothesis(cause="a", case_ref="RCA-A", log_odds=0.5, confidence=0.9)
    b = Hypothesis(cause="b", case_ref="RCA-B", log_odds=2.0, confidence=0.1)  # conf disagrees on purpose
    c = Hypothesis(cause="c", case_ref="RCA-C", log_odds=3.0, retired=True)
    wm.hypotheses = [a, b, c]
    assert [x["case_ref"] for x in wmod.belief_ranking(wm)] == ["RCA-B", "RCA-A"]


def new_world_model_for_test():
    return wmod.new_world_model("ep_test", [])
