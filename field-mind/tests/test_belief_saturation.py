"""bench/belief_saturation.summarise on a hand-built run: counts, not formulas."""
from bench.belief_saturation import summarise
from fieldmind.agent.world_model import CLAMP


def _tick(k, lo, conf, refs=("RCA-14", "RCA-18")):
    brk = [{"case_ref": refs[0], "log_odds": lo[0], "confidence": 0.5},
           {"case_ref": refs[1], "log_odds": lo[1], "confidence": 0.5}]
    return {"tick": k, "belief_ranking": brk,
            "hypotheses": [{"case_ref": refs[0], "confidence": conf}]}


def _run(ticks):
    gt = {"family": "C", "root_cause_id": "RCA-14", "fault_onset_t": 0}
    return {"episode_id": "x", "ground_truth": gt, "assessments": ticks}


def test_counts_clamp_ties_and_confident_wrong_group():
    ticks = [
        _tick(1, (CLAMP, CLAMP), 0.982),                  # tie AT clamp, right group
        _tick(2, (CLAMP, 1.0), 0.982),                    # at clamp, no tie
        _tick(3, (1.5, 1.5), 0.82),                       # tie below clamp
        _tick(4, (0.2, 0.1), 0.55, refs=("RCA-11", "RCA-14")),   # confident, wrong group
        _tick(5, (0.1, 0.0), 0.3),                        # low confidence
    ]
    s = summarise([_run(ticks)])["library"]
    assert s["ticks"] == 5
    assert s["rank1_at_clamp"] == 2
    assert s["ties"] == 2 and s["ties_at_clamp"] == 1
    assert s["confident_and_wrong_group"] == 1
    assert s["shown_conf_deciles"][9] == 2 and s["shown_conf_deciles"][3] == 1


def test_no_fault_class_counts_every_tick_with_belief():
    gt = {"family": "N", "root_cause_id": "NONE", "fault_onset_t": None}
    run = {"episode_id": "n", "ground_truth": gt,
           "assessments": [_tick(1, (CLAMP, 0.0), 0.982),
                           {"tick": 2, "belief_ranking": [], "hypotheses": []}]}
    s = summarise([run])["no-fault"]
    assert s["ticks"] == 1 and s["rank1_at_clamp"] == 1


def test_ece_known_values():
    from bench.belief_saturation import ece
    # 4 ticks in the top bin, mean conf 0.9, 2 of 4 correct -> |0.9-0.5| = 0.4
    assert ece([(0.9, True), (0.9, True), (0.9, False), (0.9, False)])["ece"] == 0.4
    # perfectly calibrated bins -> 0
    assert ece([(0.1, False)] * 9 + [(0.1, True)])["ece"] == 0.0
    # confidence exactly 1.0 lands in the last bin, not off the end
    assert ece([(1.0, True)])["bins"][-1][1] == 1
    # two bins weighted by size: (0.1, wrong) x1 -> 0.1 err; (0.9, right) x3 -> 0.1 err
    r = ece([(0.1, False), (0.9, True), (0.9, True), (0.9, True)])
    assert abs(r["ece"] - (0.25 * 0.1 + 0.75 * 0.1)) < 1e-4


def test_shown_field_is_read_but_decision_flag_overrides():
    from bench.belief_saturation import conf_of
    h = {"confidence": 0.98, "confidence_shown": 0.5}
    assert conf_of(h) == 0.5 and conf_of(h, decision=True) == 0.98
    assert conf_of({"confidence": 0.7}) == 0.7


def test_confident_boundary_is_strictly_above_half():
    # a tie shows exactly 0.5, which must NOT count as confident
    ticks = [_tick(1, (1.0, 0.0), 0.5, refs=("RCA-11", "RCA-14")),     # 0.5: not confident
             _tick(2, (1.0, 0.0), 0.51, refs=("RCA-11", "RCA-14"))]    # 0.51: confident, wrong
    s = summarise([_run(ticks)])["library"]
    assert s["confident_and_wrong_group"] == 1
    assert s["low_conf_share"] == 0.5


def test_correctness_is_group_level_not_case_level():
    # truth RCA-14; the shown rank-1 is RCA-18, a different case in the SAME group
    same_group = _tick(1, (1.0, 0.0), 0.9, refs=("RCA-18", "RCA-14"))
    other_group = _tick(2, (1.0, 0.0), 0.9, refs=("RCA-11", "RCA-14"))
    s = summarise([_run([same_group, other_group])])["library"]
    assert s["confident_and_wrong_group"] == 1          # only RCA-11 is wrong
    # case-level ECE sees both as wrong; group-level sees one right and one wrong
    assert s["ece_case"] == 0.9          # |0.9 - 0/2|
    assert s["ece_group"] == 0.4         # |0.9 - 1/2|


def test_trajectory_rows_carry_both_the_decision_and_the_shown_value():
    from bench.belief_saturation import trajectories
    t = _tick(7, (3.0, 0.2), 0.957)
    t["hypotheses"][0]["confidence_shown"] = 0.95
    row = trajectories([{"episode_id": "e", "assessments": [t]}], ["e"])["e"][0]
    assert row[3] == 0.957 and row[4] == 0.95
