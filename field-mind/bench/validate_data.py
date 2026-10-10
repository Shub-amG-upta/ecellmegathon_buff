"""
=============================================================================
 VALIDATION GATE  --  no episode is written unless it passes  (stage 4)
=============================================================================

An episode is a physics artefact. If the mass balance does not close, or the
pressure disagrees with the steam table, or family E crosses a limit, the file
is wrong and every benchmark number computed from it is wrong. This module is
the gate: `episode_build.build_episode` calls `validate_episode` and writes
nothing on a FAIL.

Checks (each yields FAIL / WARN findings):

  C1  mass closes      slope(collapsed-liquid level) == K_level(p)*net_inflow,
                       recomputed from the EMITTED columns + G_sw + K_level --
                       independent of the simulator's own integrator.
  C2  energy closes    the diag dp/dt re-integrates to the emitted pressure;
                       no-fault pressure is stationary; per-family direction.
  C3  p <-> Tsat       Tsat(drum_pressure) sane, and bed & main steam both sit
                       above saturation (no wet-steam, no sign flip).
  C4  noise stats      emitted per-sample white-noise estimate within band of
                       fingerprint.json; --suite also checks steam<->bed.
  C5  physical range   every tag inside configs/base.yaml physical_range.
  C6  family E         crosses NO limit and its timeline is NORMAL/DEVIATION.
  C7  timeline sane    contiguous, covers [0,dur]; trip_t iff TRIP_IMMINENT;
                       a non-caught fault actually develops.
  C8  deterministic    a second run of the same spec is byte-identical.

Run:
  python -m bench.validate_data --episodes data/episodes      validate on disk
  python -m bench.validate_data --suite                       long-run stats
  python -m bench.validate_data --selftest                    gate mutation test
=============================================================================
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml  # noqa: E402

from data.generator.sim import (  # noqa: E402
    BoilerSim, Drivers, EpisodeSpec, Schedule, DT_S, NOMINAL,
    G_SW_PCT_PER_KGFCM2, P_NOM_KGFCM2G,
)
from data.physics.geometry import K_level, NOMINAL_BLOWDOWN_TPH  # noqa: E402
from data.physics.steam_tables import Tsat_K, kgfcm2g_to_Pa  # noqa: E402

_ROOT = Path(__file__).parent.parent
FINGERPRINT = json.loads((_ROOT / "data/reference/fingerprint.json").read_text())
_CFG = yaml.safe_load((_ROOT / "configs/base.yaml").read_text())
PHYS_RANGE = _CFG["checks"]["validity"]["physical_range"]

REF_TAGS = ["steam_flow", "bed_temp_avg", "ms_temperature", "drum_pressure"]
STATES = ["NORMAL", "DEVIATION", "DEGRADED", "ALARM", "TRIP_IMMINENT"]

# Operating envelope -- outside physical_range is a FAIL, outside this is a WARN.
ENVELOPE = {"drum_level": (0.0, 100.0), "feed_water_flow": (0.0, 100.0),
            "steam_flow": (25.0, 95.0), "drum_pressure": (40.0, 80.0),
            "bed_temp_avg": (650.0, 1050.0), "ms_temperature": (380.0, 600.0)}

# Hard limits family E must never touch (configs/base.yaml checks.limits + trips).
E_LIMITS = [("bed_temp_avg", 880.0, ">"), ("bed_temp_avg", 940.0, ">"),
            ("drum_level", 20.0, "<"), ("drum_level", 10.0, "<"),
            ("drum_pressure", 55.0, "<"), ("ms_temperature", 550.0, ">")]


# =======================================================================
@dataclass
class Finding:
    check: str
    level: str        # "FAIL" | "WARN"
    msg: str

    def __str__(self) -> str:
        return f"[{self.level:4s}] {self.check}: {self.msg}"


@dataclass
class ValidationReport:
    episode_id: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, check: str, level: str, msg: str) -> None:
        self.findings.append(Finding(check, level, msg))

    @property
    def failed(self) -> bool:
        return any(f.level == "FAIL" for f in self.findings)

    @property
    def n_warn(self) -> int:
        return sum(1 for f in self.findings if f.level == "WARN")

    def text(self) -> str:
        head = f"{self.episode_id}: {'FAIL' if self.failed else 'PASS'}"
        if self.n_warn:
            head += f" ({self.n_warn} warn)"
        return "\n".join([head] + [f"    {f}" for f in self.findings])


# =======================================================================
#  helpers
# =======================================================================
def _series(rows, key):
    return [r[key] for r in rows]


def _slope_per_min(xs: list[float]) -> float:
    """Least-squares slope in units per minute (samples DT_S apart)."""
    n = len(xs)
    if n < 3:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(xs) / n
    num = sum((i - mx) * (y - my) for i, y in enumerate(xs))
    den = sum((i - mx) ** 2 for i in range(n))
    return (num / den) * (60.0 / DT_S) if den else 0.0


def _hf_noise(xs: list[float]) -> float:
    """White-noise sd from the 2nd difference: std(d2) / sqrt(6). Matches the
    fingerprint's `measurement_noise.hf_noise_est_2nd_diff`."""
    d2 = [xs[i] - 2 * xs[i + 1] + xs[i + 2] for i in range(len(xs) - 2)]
    return st.pstdev(d2) / (6.0 ** 0.5) if len(d2) > 2 else 0.0


def _pearson(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sx = sum((a - mx) ** 2 for a in x) ** 0.5
    sy = sum((b - my) ** 2 for b in y) ** 0.5
    return (sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)
            if sx and sy else 0.0)


def _driver_at(spec: EpisodeSpec, driver: str, t_s: float) -> float:
    """Effective driver value at time t under the episode's ramped schedule."""
    base = getattr(Drivers(), driver)
    val = base
    for s in spec.schedules:
        if s.driver != driver or t_s < s.t_start_s:
            continue
        frac = min(1.0, (t_s - s.t_start_s) / max(s.ramp_min * 60.0, 1e-9))
        val = base + (s.target - base) * frac
    return val


def _ramp_windows(spec: EpisodeSpec) -> list[tuple[float, float]]:
    """Time spans where a schedule is actively ramping (mass balance is in
    transient and windowed slope-vs-mean-flow comparison is unreliable)."""
    out = []
    for s in spec.schedules:
        out.append((s.t_start_s - 60.0, s.t_start_s + s.ramp_min * 60.0 + 120.0))
    return out


# =======================================================================
#  the checks
# =======================================================================
def _c1_mass_closes(spec, rows, rep: ValidationReport) -> None:
    """slope(level - swell) == K_level(p) * (feed - steam - bd - leak).

    Recomputed from the emitted columns. Split into steady windows (tight FAIL
    band) and transient windows (sign-agreement WARN)."""
    win = int(5 * 60 / DT_S)          # 5-minute window
    step = int(60 / DT_S)             # 1-minute stride
    ramps = _ramp_windows(spec)
    # An instrument-dropout injection freezes one tag from ~55 % through the
    # episode. If it is a water-balance tag the emitted column no longer equals
    # what the physics used, so mass closure cannot be checked past that point.
    drop_t = (rows[int(len(rows) * 0.55)]["t"]
              if spec.dropout_tag in ("feed_water_flow", "steam_flow", "drum_level")
              else 1e18)
    worst_steady = 0.0
    steady_n = bad_steady = 0
    sign_bad = 0
    for i in range(0, len(rows) - win, step):
        w = rows[i:i + win]
        t_mid = w[len(w) // 2]["t"]
        if t_mid >= drop_t or any(a <= t_mid <= b for a, b in ramps):
            continue
        lvl = _series(w, "drum_level")
        if min(lvl) < 1.0 or max(lvl) > 99.0:          # transmitter pinned
            continue
        p = _series(w, "drum_pressure")
        p_mid = sum(p) / len(p)
        # collapsed-liquid level = indicated - swell(p)
        liq = [l - (-G_SW_PCT_PER_KGFCM2 * (pp - P_NOM_KGFCM2G))
               for l, pp in zip(lvl, p)]
        slope_liq = _slope_per_min(liq)
        feed = sum(_series(w, "feed_water_flow")) / len(w)
        steam = sum(_series(w, "steam_flow")) / len(w)
        leak = _driver_at(spec, "leak_tph", t_mid)
        bd = _driver_at(spec, "blowdown_tph", t_mid) * NOMINAL_BLOWDOWN_TPH
        net = feed - steam - bd - leak
        pred = K_level(kgfcm2g_to_Pa(p_mid)) * net
        resid = slope_liq - pred
        if abs(net) < 2.5 and abs(slope_liq) < 0.25:   # near-steady
            steady_n += 1
            worst_steady = max(worst_steady, abs(resid))
            if abs(resid) > 0.20:
                bad_steady += 1
        elif abs(net) > 3.0:                            # transient
            if slope_liq * pred < 0 and abs(slope_liq) > 0.15:
                sign_bad += 1
    if steady_n and bad_steady > max(2, 0.05 * steady_n):
        rep.add("C1", "FAIL",
                f"water balance does not close in {bad_steady}/{steady_n} steady "
                f"windows (worst residual {worst_steady:.2f} %/min > 0.20)")
    elif steady_n and worst_steady > 0.20:
        rep.add("C1", "WARN",
                f"water-balance residual up to {worst_steady:.2f} %/min in "
                f"{bad_steady}/{steady_n} steady windows")
    if sign_bad > 3:
        rep.add("C1", "FAIL",
                f"level slope and net inflow disagree in sign in {sign_bad} "
                f"transient windows -- a driver->level sign error")


def _c2_energy_closes(spec, rows, diag, rep: ValidationReport) -> None:
    fam = spec.family
    p = _series(rows, "drum_pressure")
    p0 = sum(p[:60]) / 60
    p1 = sum(p[-60:]) / 60

    if diag is not None:
        # re-integrate dp/dt outside step()
        pa_per = kgfcm2g_to_Pa(1.0) - kgfcm2g_to_Pa(0.0)
        pp = diag[0]["p_clean"]
        worst = 0.0
        for d in diag:
            worst = max(worst, abs(pp - d["p_clean"]))
            pp = max(5.0, pp + d["dp_dt_Pa_s"] * DT_S / pa_per)
        if worst > 0.15:
            rep.add("C2", "FAIL",
                    f"diag dp/dt re-integrates to a pressure trace {worst:.2f} "
                    f"kg/cm2 off the emitted one -- integrator wiring bug")

    # no-fault stationarity
    if fam in ("N",):
        sl = _slope_per_min(p)
        if abs(sl) > 0.03:
            rep.add("C2", "FAIL",
                    f"no-fault pressure drifts {sl:+.3f} kg/cm2/min over the "
                    f"episode (expect ~0)")
    # per-family direction of the pressure move
    dP = p1 - p0
    if fam == "C" and not spec.caught_in_time and dP > -1.0:
        rep.add("C2", "FAIL",
                f"family C pressure moved {dP:+.2f} kg/cm2 -- a combustion "
                f"deficit must sag the pressure")
    if fam == "D" and abs(dP) > 3.0:
        rep.add("C2", "WARN",
                f"family D pressure moved {dP:+.2f} kg/cm2 -- a bed-temperature "
                f"fault should leave pressure ~flat")
    if fam in ("N", "E") and abs(dP) > 1.5:
        rep.add("C2", "WARN",
                f"family {fam} pressure moved {dP:+.2f} kg/cm2 (expect < 1.5)")


def _c3_p_tsat(spec, rows, rep: ValidationReport) -> None:
    bad_sat = bad_bed = bad_ms = 0
    tmin, tmax = 1e9, -1e9
    for r in rows:
        pr = r["drum_pressure"]
        if not (ENVELOPE["drum_pressure"][0] <= pr <= ENVELOPE["drum_pressure"][1]):
            continue
        tsat = Tsat_K(kgfcm2g_to_Pa(pr)) - 273.15
        tmin, tmax = min(tmin, tsat), max(tmax, tsat)
        if not (240.0 <= tsat <= 315.0):
            bad_sat += 1
        if r["bed_temp_avg"] <= tsat + 5.0:
            bad_bed += 1
        if r["ms_temperature"] <= tsat + 5.0:
            bad_ms += 1
    if bad_sat:
        rep.add("C3", "FAIL",
                f"Tsat(drum_pressure) outside 240-315 degC on {bad_sat} rows "
                f"(range {tmin:.0f}-{tmax:.0f})")
    if bad_bed:
        rep.add("C3", "FAIL",
                f"bed_temp_avg at or below saturation on {bad_bed} rows "
                f"(pressure/temperature sign or calibration error)")
    if bad_ms:
        rep.add("C3", "FAIL",
                f"ms_temperature at or below saturation on {bad_ms} rows "
                f"(superheater delivering wet steam)")


def _c4_noise_stats(spec, rows, rep: ValidationReport) -> None:
    for tag in REF_TAGS:
        got = _hf_noise(_series(rows, tag))
        ref = FINGERPRINT["tags"][tag]["measurement_noise"]["hf_noise_est_2nd_diff"]
        if tag == "drum_pressure":
            ref *= 67.0 / 99.6                         # span scaling (DERIVATIONS 7.2)
        ratio = got / ref if ref else 0.0
        if not (0.2 <= ratio <= 4.0):
            rep.add("C4", "FAIL",
                    f"{tag} white-noise estimate {got:.3f} is {ratio:.1f}x the "
                    f"fingerprint's {ref:.3f}")
        elif not (0.4 <= ratio <= 2.6):
            rep.add("C4", "WARN",
                    f"{tag} white-noise estimate {got:.3f} is {ratio:.1f}x the "
                    f"fingerprint's {ref:.3f}")


def _c5_physical_range(spec, rows, rep: ValidationReport) -> None:
    for tag, (lo, hi) in PHYS_RANGE.items():
        s = _series(rows, tag)
        if min(s) < lo or max(s) > hi:
            rep.add("C5", "FAIL",
                    f"{tag} leaves physical_range [{lo}, {hi}]: "
                    f"{min(s):.1f}..{max(s):.1f}")
        elo, ehi = ENVELOPE[tag]
        if min(s) < elo or max(s) > ehi:
            # a real trip drives level < 20 etc -- only WARN, and not for the
            # tag the fault family targets.
            rep.add("C5", "WARN",
                    f"{tag} leaves the operating envelope [{elo}, {ehi}]: "
                    f"{min(s):.1f}..{max(s):.1f}")


def _c6_family_e(spec, rows, gt, rep: ValidationReport) -> None:
    if spec.family != "E":
        return
    for tag, thr, op in E_LIMITS:
        s = _series(rows, tag)
        hit = (max(s) > thr) if op == ">" else (min(s) < thr)
        if hit:
            rep.add("C6", "FAIL",
                    f"family E {tag} crosses {op} {thr} "
                    f"({min(s):.1f}..{max(s):.1f}) -- mislabelled, not a "
                    f"family-E episode")
    states = {seg[2] for seg in gt["state_timeline"]}
    if not states <= {"NORMAL", "DEVIATION"}:
        rep.add("C6", "FAIL",
                f"family E timeline contains {sorted(states - {'NORMAL', 'DEVIATION'})} "
                f"-- family E crosses nothing by definition")


def _c7_timeline(spec, rows, gt, rep: ValidationReport) -> None:
    tl = gt.get("state_timeline") or []
    dur = rows[-1]["t"]
    if not tl:
        rep.add("C7", "FAIL", "state_timeline is empty")
        return
    if tl[0][0] != 0.0:
        rep.add("C7", "FAIL", f"state_timeline starts at {tl[0][0]}, not 0")
    for a, b in zip(tl, tl[1:]):
        if abs(a[1] - b[0]) > 1e-6:
            rep.add("C7", "FAIL",
                    f"state_timeline gap/overlap at {a[1]} -> {b[0]}")
    if abs(tl[-1][1] - dur) > DT_S:
        rep.add("C7", "FAIL",
                f"state_timeline ends at {tl[-1][1]}, episode is {dur}s")
    for seg in tl:
        if seg[2] not in STATES:
            rep.add("C7", "FAIL", f"unknown state {seg[2]!r} in timeline")
    has_trip = any(seg[2] == "TRIP_IMMINENT" for seg in tl)
    if bool(gt.get("trip_t")) != has_trip:
        rep.add("C7", "FAIL",
                f"trip_t={gt.get('trip_t')} but TRIP_IMMINENT "
                f"{'present' if has_trip else 'absent'} in timeline")
    states = [seg[2] for seg in tl]
    if spec.family not in ("N",) and not spec.caught_in_time \
            and spec.family != "E" and set(states) == {"NORMAL"}:
        rep.add("C7", "FAIL",
                f"family {spec.family} fault never develops -- timeline is all "
                f"NORMAL (severity too low, or onset past episode end)")
    if spec.caught_in_time and states[-1] not in ("NORMAL", "DEVIATION"):
        rep.add("C7", "WARN",
                f"caught_in_time episode ends in {states[-1]}, not recovered")
    if spec.family == "N" and set(states) != {"NORMAL"}:
        nn = [s for s in states if s != "NORMAL"]
        rep.add("C7", "WARN",
                f"normal episode timeline contains {sorted(set(nn))} "
                f"(load-normalised DEVIATION band vs OU swing)")


def _c8_determinism(spec, rows, rep: ValidationReport) -> None:
    rows2 = BoilerSim(spec).run()
    if len(rows2) != len(rows):
        rep.add("C8", "FAIL", f"re-run length {len(rows2)} != {len(rows)}")
        return
    for i, (a, b) in enumerate(zip(rows, rows2)):
        for k in a:
            if abs(a[k] - b[k]) > 1e-9:
                rep.add("C8", "FAIL",
                        f"re-run differs at row {i} tag {k}: {a[k]} vs {b[k]}")
                return


# =======================================================================
def validate_episode(spec: EpisodeSpec, rows: list[dict], gt: dict,
                     diag: list[dict] | None = None,
                     check_determinism: bool = True) -> ValidationReport:
    """The gate. `diag` is BoilerSim.diag (enables the C2 integrator check).
    Returns a ValidationReport; `.failed` is True if any finding is FAIL."""
    rep = ValidationReport(spec.episode_id)
    _c1_mass_closes(spec, rows, rep)
    _c2_energy_closes(spec, rows, diag, rep)
    _c3_p_tsat(spec, rows, rep)
    _c4_noise_stats(spec, rows, rep)
    _c5_physical_range(spec, rows, rep)
    _c6_family_e(spec, rows, gt, rep)
    _c7_timeline(spec, rows, gt, rep)
    if check_determinism:
        _c8_determinism(spec, rows, rep)
    return rep


# =======================================================================
#  suite: long no-fault statistical checks (promoted from sim._ou_recheck)
# =======================================================================
def run_suite(n_runs: int = 3, hours: float = 30.0) -> ValidationReport:
    rep = ValidationReport(f"SUITE ({n_runs} x {hours:.0f}h no-fault)")
    sds, acf30, xbeds = [], [], []
    dt_min = DT_S / 60.0
    lag30 = int(30 / dt_min)
    for seed in range(n_runs):
        rows = BoilerSim(EpisodeSpec(episode_id=f"suite_{seed}", family="N",
                                     tier="A", duration_min=hours * 60.0,
                                     seed=seed)).run()
        sf = _series(rows, "steam_flow")
        bd = _series(rows, "bed_temp_avg")
        sds.append(st.pstdev(sf))
        m = sum(bd) / len(bd)
        d = [v - m for v in bd]
        num = sum(d[i] * d[i + lag30] for i in range(len(d) - lag30))
        den = sum(v * v for v in d)
        acf30.append(num / den if den else 0.0)
        xbeds.append(_pearson(sf, bd))
    ssd = sum(sds) / len(sds)
    a30 = sum(acf30) / len(acf30)
    xb = sum(xbeds) / len(xbeds)
    fp_sd = FINGERPRINT["tags"]["steam_flow"]["level"]["std_full_5day"]
    print(f"  steam_flow sd        {ssd:.2f}  (fp {fp_sd:.2f})")
    print(f"  bed 30-min acf       {a30:.2f}  (fp 0.57)")
    print(f"  steam<->bed pearson  {xb:+.2f}  (fp +0.87)")
    if not (1.8 <= ssd <= 3.2):
        rep.add("C4", "FAIL", f"steam_flow sd {ssd:.2f} outside 1.8-3.2")
    if not (0.40 <= a30 <= 0.85):
        rep.add("C4", "FAIL", f"bed 30-min acf {a30:.2f} outside 0.40-0.85")
    if not (0.77 <= xb <= 0.95):
        rep.add("C4", "FAIL",
                f"steam<->bed pearson_level {xb:+.2f} outside 0.77-0.95 "
                f"(fingerprint 0.87) -- process disturbances mis-tuned")
    return rep


# =======================================================================
#  CLI + selftest
# =======================================================================
def _load_disk(ep_dir: Path):
    with open(ep_dir / "timeseries.csv") as fh:
        rows = [{k: float(v) for k, v in r.items()} for r in csv.DictReader(fh)]
    gt = json.loads((ep_dir / "ground_truth.json").read_text())
    return rows, gt


def _spec_by_id(ep_id: str) -> EpisodeSpec | None:
    from data.generator.episode_build import catalogue, dev_catalogue
    return next((s for s in catalogue() + dev_catalogue()
                 if s.episode_id == ep_id), None)


def _validate_on_disk(root: Path) -> int:
    dirs = sorted(p for p in root.iterdir() if (p / "timeseries.csv").exists())
    if not dirs:
        print(f"no episodes under {root}")
        return 1
    n_fail = 0
    by_fam: dict[str, list[str]] = {}
    for d in dirs:
        spec = _spec_by_id(d.name)
        if spec is None:
            print(f"{d.name}: FAIL  no matching spec in catalogue()")
            n_fail += 1
            continue
        rows, gt = _load_disk(d)
        sim = BoilerSim(spec)
        srows = sim.run()
        rep = validate_episode(spec, srows, gt, diag=sim.diag,
                               check_determinism=True)
        # cross-check that what is on disk matches a fresh run
        if len(srows) != len(rows) or any(
                abs(a[k] - b[k]) > 1e-6 for a, b in zip(srows, rows) for k in a):
            rep.add("C8", "FAIL", "on-disk timeseries != a fresh run of the spec")
        print(rep.text())
        by_fam.setdefault(spec.family, []).append("F" if rep.failed else ".")
        n_fail += rep.failed
    print("\nby family:", {k: "".join(v) for k, v in sorted(by_fam.items())})
    print(f"{len(dirs) - n_fail}/{len(dirs)} passed")
    return 1 if n_fail else 0


def _selftest() -> int:
    """Break a known-good episode in each dimension a check owns; confirm the
    matching check FAILs. A gate that passes a broken episode is not a gate."""
    from data.generator.episode_build import catalogue, build_gt_and_timeline

    ok_spec = _spec_by_id("ep_C01_wet_coal") or catalogue()[7]
    e_spec = _spec_by_id("ep_E01_fouling_drift")
    passed = failed = 0

    def expect(label, rep, check, want_fail=True):
        nonlocal passed, failed
        hit = any(f.check == check and f.level == "FAIL" for f in rep.findings)
        good = hit == want_fail
        passed += good
        failed += not good
        print(f"  {'PASS' if good else 'FAIL'}  {label} "
              f"(-> {check} {'FAIL' if hit else 'clean'})")

    # baseline: a real episode passes
    sim = BoilerSim(ok_spec)
    rows = sim.run()
    gt = build_gt_and_timeline(ok_spec, rows)
    rep = validate_episode(ok_spec, rows, gt, diag=sim.diag)
    print(f"  {'PASS' if not rep.failed else 'FAIL'}  baseline ep_C01 passes clean")
    passed += not rep.failed
    failed += rep.failed

    # C3: invert bed vs saturation -> bed below Tsat
    bad = [dict(r, bed_temp_avg=260.0) for r in rows]
    expect("bed driven below saturation", validate_episode(
        ok_spec, bad, gt, diag=None, check_determinism=False), "C3")

    # C5: push a tag out of physical_range
    bad = [dict(r) for r in rows]
    bad[500]["drum_pressure"] = 999.0
    expect("pressure out of physical_range", validate_episode(
        ok_spec, bad, gt, diag=None, check_determinism=False), "C5")

    # C1: inject a spurious +0.4 %/min level drift with no matching change in
    # feed/steam -> slope(level) no longer equals K*net_inflow.
    bad = [dict(r, drum_level=min(99.0, r["drum_level"] + 0.4 * r["t"] / 60.0))
           for r in rows]
    expect("spurious level drift breaks the water balance", validate_episode(
        ok_spec, bad, gt, diag=None, check_determinism=False), "C1")

    # C6: family E with the bed pushed over the 880 alarm
    if e_spec is not None:
        esim = BoilerSim(e_spec)
        erows = esim.run()
        egt = build_gt_and_timeline(e_spec, erows)
        ebad = [dict(r, bed_temp_avg=r["bed_temp_avg"] + 40.0) for r in erows]
        expect("family E bed +40 degC over the alarm", validate_episode(
            e_spec, ebad, egt, diag=None, check_determinism=False), "C6")

    # C7: trip_t / timeline mismatch
    gt_bad = dict(gt, trip_t=1234.0)
    expect("trip_t set with no TRIP_IMMINENT segment", validate_episode(
        ok_spec, rows, gt_bad, diag=None, check_determinism=False), "C7")

    # C7: fault that never develops
    flat_spec = EpisodeSpec(episode_id="flat", family="C", tier="A",
                            duration_min=40.0, schedules=[], fault_onset_s=600.0)
    frows = BoilerSim(flat_spec).run()
    fgt = build_gt_and_timeline(flat_spec, frows)
    expect("family C with no schedule (never develops)", validate_episode(
        flat_spec, frows, fgt, diag=None, check_determinism=False), "C7")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default="data/episodes")
    ap.add_argument("--suite", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.suite:
        rep = run_suite()
        print(rep.text())
        return 1 if rep.failed else 0
    return _validate_on_disk(Path(args.episodes))


if __name__ == "__main__":
    raise SystemExit(main())
