"""
Band calibration (Session 1b, step 3).

CRITERION, fixed before looking at any number: the deadband of each tag is set
so that at least 85% of healthy 10-minute windows read FLAT, i.e. the deadband
is at least the 85th percentile of the healthy |10-min slope|. A tag that
already meets the criterion keeps its current deadband (deadband = max(current,
p85)). The slow/med and med/fast edges keep the tag's existing multiples of its
deadband.

DATA: the no-fault dev episodes (data/episodes_dev/dev_N*) only. No fault
episode is read here. The reporting episodes are not read either.

WINDOWS: exactly the agent's: SensorWindow fed tick by tick, slopes from
Orchestrator._slopes (raw least-squares slope over the last 10 minutes), counted
only once the buffer holds a full 10 minutes.

Usage:  .venv/bin/python -m bench.band_calibration [--episodes data/episodes_dev]
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import yaml

from bench.harness import Episode
from fieldmind.agent.l0_ingest import SensorWindow
from fieldmind.agent.orchestrator import Orchestrator
from fieldmind.schemas import TAGS

TARGET_FLAT = 0.85
WINDOW_S = 600.0


def round_up_sig(x: float, sig: int = 2) -> float:
    """Round UP to `sig` significant figures (never below x)."""
    if x <= 0:
        return 0.0
    e = math.floor(math.log10(x))
    q = 10.0 ** (e - sig + 1)
    return round(math.ceil(x / q - 1e-9) * q, 12)


def _sig3(x: float) -> float:
    return float(f"{x:.3g}")


def percentile_nearest_rank(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def calibrate(abs_slopes: dict[str, list[float]], current: dict,
              target: float = TARGET_FLAT) -> dict:
    """New band edges per tag: deadband = max(current, p_target rounded up to 2
    significant figures); the other two edges keep the current multiples."""
    out = {}
    for tag, (dead, lo, hi) in current.items():
        p = round_up_sig(percentile_nearest_rank(abs_slopes[tag], target))
        nd = max(dead, p)
        out[tag] = [nd, _sig3(nd * lo / dead), _sig3(nd * hi / dead)]
    return out


def flat_fraction(values: list[float], deadband: float) -> float:
    return sum(v < deadband for v in values) / len(values)


def collect(ep_dir: Path, prefix: str = "dev_N") -> dict[str, dict[str, list[float]]]:
    """{episode: {tag: [|slope| per tick with a full 10-min window]}}"""
    out = {}
    for path in sorted(p for p in ep_dir.iterdir() if p.name.startswith(prefix)):
        ep = Episode(path)
        win = SensorWindow(window_min=60.0, sample_period_s=5.0)
        per = {t: [] for t in TAGS}
        for k in range(int(ep.duration_s // 30)):
            for row in ep.samples_between(k * 30.0, (k + 1) * 30.0):
                win.append(row["t"], row)
            if len(win._t) * win.dt_s < WINDOW_S:
                continue
            sl = Orchestrator._slopes(win)
            for t in TAGS:
                per[t].append(abs(sl[t]))
        out[path.name] = per
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default="data/episodes_dev")
    ap.add_argument("--config", default="configs/base.yaml")
    args = ap.parse_args()
    cur = yaml.safe_load(Path(args.config).read_text())["checks"]["bands"]
    per_ep = collect(Path(args.episodes))
    pooled = {t: [v for e in per_ep.values() for v in e[t]] for t in TAGS}
    n = len(pooled[TAGS[0]])
    new = calibrate(pooled, {t: cur[t] for t in TAGS})
    print(f"{len(per_ep)} no-fault episodes, {n} full 10-min windows, "
          f"criterion: >= {TARGET_FLAT:.0%} FLAT, deadband = max(current, p85)\n")
    print(f"{'tag':16s} {'p85':>8s} {'old dead':>9s} {'new dead':>9s} "
          f"{'FLAT old':>9s} {'FLAT new':>9s}   new edges [dead, slow/med, med/fast]")
    for t in TAGS:
        p85 = percentile_nearest_rank(pooled[t], TARGET_FLAT)
        print(f"{t:16s} {p85:8.4f} {cur[t][0]:9.4f} {new[t][0]:9.4f} "
              f"{flat_fraction(pooled[t], cur[t][0]):9.3f} "
              f"{flat_fraction(pooled[t], new[t][0]):9.3f}   {new[t]}")
    print("\nper-episode FLAT fraction at the NEW deadband (min / max over episodes)")
    for t in TAGS:
        fr = [flat_fraction(e[t], new[t][0]) for e in per_ep.values()]
        print(f"  {t:16s} {min(fr):.3f} / {max(fr):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
