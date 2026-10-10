"""calibrate(): deadband = max(current, p85 rounded up), multiples preserved."""
from bench.band_calibration import calibrate, round_up_sig, flat_fraction

CUR = {"a": (0.05, 0.30, 1.00), "b": (0.5, 2.5, 8.3)}


def test_raises_deadband_to_p85_and_keeps_multiples():
    # 100 values 0.01..1.00 -> nearest-rank p85 = 0.85
    vals = [i / 100 for i in range(1, 101)]
    out = calibrate({"a": vals, "b": [0.0] * 100}, CUR)
    assert out["a"][0] == 0.85
    assert out["a"][1] == 5.1 and out["a"][2] == 17.0     # x6, x20
    assert flat_fraction(vals, out["a"][0]) >= 0.84       # 0.85 itself is not < 0.85


def test_never_lowers_a_deadband_that_already_meets_the_criterion():
    out = calibrate({"a": [0.001] * 50, "b": [0.0] * 50}, CUR)
    assert out["a"] == [0.05, 0.3, 1.0] and out["b"] == [0.5, 2.5, 8.3]


def test_round_up_never_below_the_value():
    for x in (0.2573, 0.0185, 0.739, 1.0, 0.18831):
        r = round_up_sig(x)
        assert r >= x and (r - x) / x < 0.1
