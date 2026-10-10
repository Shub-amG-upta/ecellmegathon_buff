"""low_conf_rate reads the SHOWN confidence (<= 0.5), falling back to the decision
value on runs that predate confidence_shown; low_conf_rate_decision always reads
the decision value. Held-out only (RCA-06 is the one held-out case with episodes)."""
from bench.evaluator import t2_root_cause


def _run(hyps_per_tick, target="RCA-06"):
    gt = {"family": "C", "root_cause_id": target, "fault_onset_t": 0}
    asm = [{"tick": k, "hypotheses": [h], "belief_ranking": []}
           for k, h in enumerate(hyps_per_tick)]
    return {"ground_truth": gt, "assessments": asm}


def H(conf, shown=None, ref="RCA-14"):
    h = {"case_ref": ref, "confidence": conf}
    if shown is not None:
        h["confidence_shown"] = shown
    return h


def test_low_conf_rate_uses_the_shown_value_and_decision_variant_the_old_one():
    # decision 0.98 (confident) but shown 0.5 (a tie): low by the shown value only
    r = t2_root_cause(_run([H(0.98, 0.5)] * 4))
    assert r["low_conf_rate"] == 1.0
    assert r["low_conf_rate_decision"] == 0.0


def test_exactly_half_counts_as_low_and_just_above_does_not():
    assert t2_root_cause(_run([H(0.9, 0.5)]))["low_conf_rate"] == 1.0
    assert t2_root_cause(_run([H(0.9, 0.51)]))["low_conf_rate"] == 0.0


def test_old_runs_without_confidence_shown_fall_back_to_the_decision_value():
    r = t2_root_cause(_run([H(0.4), H(0.9)]))
    assert r["low_conf_rate"] == 0.5 and r["low_conf_rate_decision"] == 0.5


def test_not_reported_for_library_targets():
    r = t2_root_cause(_run([H(0.98, 0.5)], target="RCA-14"))
    assert r["low_conf_rate"] is None and r["low_conf_rate_decision"] is None
