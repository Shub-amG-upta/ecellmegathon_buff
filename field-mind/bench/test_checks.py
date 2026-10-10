"""
Unit tests for the deterministic layer (Plan phase 0/2: "generate l1_checks.py
with unit tests per check").

These are the tests that matter most, because L1 is the layer everything else
is forbidden from contradicting. If a check is wrong, the model is reasoning
over false premises and no amount of prompt work will fix it.

    python bench/test_checks.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml                                                  # noqa: E402

from fieldmind.agent.l0_ingest import SensorWindow           # noqa: E402
from fieldmind.agent.l1_checks import CheckLayer, _slope_per_min  # noqa: E402
from fieldmind.agent.l1_symbolize import direction_and_band  # noqa: E402
from fieldmind.schemas import TAGS                           # noqa: E402

CFG = yaml.safe_load(open(Path(__file__).parent.parent / "configs/base.yaml"))["checks"]
NOM = {"drum_level": 50.0, "feed_water_flow": 68.0, "steam_flow": 67.0,
       "drum_pressure": 66.0, "bed_temp_avg": 850.0, "ms_temperature": 495.0}
PASSED = FAILED = 0


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name} {extra}")


# Measurement noise is REQUIRED in these fixtures, not cosmetic. A perfectly
# noiseless trace has zero variance, which correctly trips the VALIDITY
# "stuck instrument" check and suspends every downstream check over that tag.
# That ordering is the behaviour we want; a noiseless fixture just cannot
# exercise anything past it.
# Matches sim.EMIT_NOISE after the stage-4 regeneration (bed/ms ~20x smaller
# than the old ad-hoc values). Only needs to exceed stuck_mad_eps so the
# VALIDITY stuck check does not suspend everything downstream.
NOISE = {"drum_level": 0.15, "feed_water_flow": 0.109, "steam_flow": 0.109,
         "drum_pressure": 0.019, "bed_temp_avg": 0.057, "ms_temperature": 0.026}


def make_window(minutes=50.0, seed=7, **ramps):
    """Build a window with optional per-tag linear ramps in units per minute."""
    import random
    rng = random.Random(seed)
    w = SensorWindow(window_min=60.0, sample_period_s=5.0)
    n = int(minutes * 60 / 5)
    for i in range(n):
        t, m = i * 5.0, i * 5.0 / 60.0
        w.append(t, {tag: NOM[tag] + ramps.get(tag, 0.0) * m
                     + rng.gauss(0, NOISE[tag]) for tag in TAGS})
    return w


def _teleport(w, tag, value):
    """Append one physically impossible sample."""
    w.append(3000.0, {**NOM, tag: value})
    return w


def run(w, trusted=None):
    return CheckLayer(CFG).run(w, tick=100, trusted=set(trusted or TAGS))


print("slope helper")
check("least-squares slope recovers a known ramp",
      abs(_slope_per_min([i * 0.5 for i in range(60)], 5.0) - 6.0) < 1e-6)

print("\nbanding")
BANDS = CFG["bands"]
from fieldmind.agent.l1_symbolize import BAND_EDGES as _MIRROR   # noqa: E402
# Band VALUES are the Stage-5 set (Stage 6 moved them to config but reverted
# the criterion-based re-derivation after mock Q2 regressed -- see the config
# comment). These check the banding FUNCTION against those values.
check("small movement is SLOW, not FLAT (slow drifts must still register)",
      direction_and_band("bed_temp_avg", 0.08, BANDS) == ("UP", "SLOW"))
check("true stillness is FLAT",
      direction_and_band("bed_temp_avg", 0.001, BANDS) == ("FLAT", "-"))
check("a fast drum-level fall (-0.9 %/min) is FAST",
      direction_and_band("drum_level", -0.9, BANDS) == ("DOWN", "FAST"))
# The mirror in l1_symbolize.py is the device fallback for a test-only
# direction_and_band(bands=None) call. It MUST equal the config or it silently
# rots -- which is how BAND_EDGES went stale before Stage 6.
check("l1_symbolize.BAND_EDGES mirror equals configs/base.yaml checks.bands",
      {t: tuple(v) for t, v in BANDS.items()}
      == {t: tuple(v) for t, v in _MIRROR.items()})
check("direction_and_band raises on a bands dict missing the tag "
      "(no silent fallback to the mirror)",
      _raises(lambda: direction_and_band("bed_temp_avg", 1.0, {"drum_level": [0.03, 0.15, 0.5]})))

print("\nLIMIT")
# Ramp INTO the condition. Teleporting a tag to its alarm value in one sample
# is correctly caught by the VALIDITY step check first (no real process moves
# 100 degC in five seconds), which suspends LIMIT over that tag -- so a
# teleporting fixture tests the wrong thing.
f = run(make_window(bed_temp_avg=2.0))          # 850 -> ~950 over 50 min
check("bed over 940 emits CRITICAL",
      any(x.check == "LIMIT" and x.severity == "CRITICAL"
          and "bed_temp_avg" in x.tags for x in f))
f = run(make_window(drum_level=-0.7))           # 50 -> ~15 over 50 min
check("drum level under 20 emits ALARM",
      any(x.check == "LIMIT" and x.severity == "ALARM"
          and "drum_level" in x.tags for x in f))
check("an impossible one-sample jump is an INSTRUMENT fault, not a plant fault",
      any(x.check == "VALIDITY" for x in
          run(_teleport(make_window(), "bed_temp_avg", 950.0))))
check("nothing fires on a steady healthy plant",
      not any(x.check == "LIMIT" for x in run(make_window())))

print("\nRATE")
f = run(make_window(drum_level=-0.8))
check("sustained level fall emits a RATE fact",
      any(x.check == "RATE" and "drum_level" in x.tags for x in f))
check("RATE confidence is discounted below 1.0 (slopes are inferred)",
      all(x.confidence < 1.0 for x in f if x.check == "RATE"))

print("\nVALIDITY")
w = SensorWindow(window_min=60.0, sample_period_s=5.0)
for i in range(600):
    w.append(i * 5.0, dict(NOM))               # perfectly frozen
f = run(w)
check("a frozen instrument is flagged", any(x.check == "VALIDITY" for x in f))
w = make_window(); w.append(3000.0, {**NOM, "drum_level": 250.0})
check("a physically impossible value is flagged",
      any(x.check == "VALIDITY" and "drum_level" in x.tags for x in run(w)))

print("\nBALANCE")
# Leak: feed exceeds steam by 5 TPH yet the level holds -> water unaccounted.
w = make_window(feed_water_flow=0.0)
import random as _r
_rng = _r.Random(11)
for i in range(600):                      # feed 5 TPH above steam, level held
    w.append(3000.0 + i * 5.0,
             {**{t: NOM[t] + _rng.gauss(0, NOISE[t]) for t in TAGS},
              "feed_water_flow": 73.0 + _rng.gauss(0, 0.35)})
f = [x for x in run(w) if x.check == "BALANCE" and "SUSPENDED" not in x.detail]
check("water balance fires when feed exceeds steam but level holds", bool(f))
check("residual is positive for an unaccounted loss",
      bool(f) and f[0].value > 0, extra=f"got {f[0].value if f else None}")
check("residual text says water is being LOST, not that feed is short",
      bool(f) and "lost" in f[0].detail.lower())

# Untrusted tag must SUSPEND the balance, not silently skip it.
f = run(make_window(), trusted=[t for t in TAGS if t != "feed_water_flow"])
check("balance is reported SUSPENDED when a tag is untrusted",
      any(x.check == "BALANCE" and "SUSPENDED" in x.detail for x in f))

print("\nfact contract")
f = run(make_window(drum_level=-0.8))
check("fact ids are unique within a tick", len({x.id for x in f}) == len(f))
check("every fact carries a window and a severity",
      all(x.window and x.severity for x in f))

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
