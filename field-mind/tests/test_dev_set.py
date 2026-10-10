"""Dev set (Session 1b): disjoint seeds, same scenarios, enough no-fault episodes.
Pure spec checks, no simulation, so they run without data/episodes."""
from data.generator.episode_build import (
    catalogue, dev_catalogue, DEV_NOFAULT_SEEDS)

# Seeds already spent elsewhere (see the comment above dev_catalogue()).
CALIBRATION_SEEDS = (set(range(0, 4)) | set(range(700, 712))
                     | set(range(900, 912)) | set(range(950, 970)))


def _faults(specs):
    return [s for s in specs if s.family != "N"]


def test_dev_seeds_disjoint_from_reporting_and_calibration():
    dev = {s.seed for s in dev_catalogue()}
    used = {s.seed for s in catalogue()} | CALIBRATION_SEEDS
    assert not dev & used, sorted(dev & used)


def test_dev_seeds_unique_and_ids_unique():
    d = dev_catalogue()
    assert len({s.seed for s in d}) == len(d)
    assert len({s.episode_id for s in d}) == len(d)
    assert not {s.episode_id for s in d} & {s.episode_id for s in catalogue()}


def test_every_fault_has_a_twin_with_identical_scenario():
    rep = {s.episode_id[len("ep_"):]: s for s in _faults(catalogue())}
    dev = {s.episode_id[len("dev_"):]: s for s in _faults(dev_catalogue())}
    assert rep.keys() == dev.keys() and len(dev) == 24
    for k, r in rep.items():
        d = dev[k]
        assert d.schedules == r.schedules
        assert (d.duration_min, d.fault_onset_s, d.family, d.tier) == (
            r.duration_min, r.fault_onset_s, r.family, r.tier)
        assert (d.root_cause_id, d.correct_action_ids, d.caught_in_time,
                d.dropout_tag) == (r.root_cause_id, r.correct_action_ids,
                                   r.caught_in_time, r.dropout_tag)
        assert d.seed != r.seed


def test_at_least_twelve_no_fault_and_they_copy_the_n_specs():
    nf = [s for s in dev_catalogue() if s.family == "N"]
    assert len(nf) >= 12 and len(DEV_NOFAULT_SEEDS) == len(nf)
    rep_n = [s for s in catalogue() if s.family == "N"]
    for i, s in enumerate(nf):
        r = rep_n[i % len(rep_n)]
        assert (s.duration_min, s.schedules) == (r.duration_min, r.schedules)


def test_repeat_of_points_inside_the_dev_set():
    d = dev_catalogue()
    ids = {s.episode_id for s in d}
    reps = [s for s in d if s.repeat_of]
    assert reps
    for s in reps:
        assert s.repeat_of in ids
