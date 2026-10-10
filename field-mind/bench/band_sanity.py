"""
Band sanity check (Session 1b, step 3). Run AFTER the edges are fixed; it reads
fault episodes, so it must never feed back into band_calibration.py.

For each fault family, the share of post-onset ticks on which the family's
headline tag reads as moving (not FLAT) under the edges in the config, next to
the same figure under the previous (Stage-5) edges. Headline tags are the Stage 6
set (reports/stage6_band_edges.md, table 2). B's headline there is the pseudo-tag
water_balance, which comes from BALANCE facts and does not depend on checks.bands;
the banded tag reported for B is feed_water_flow.

"Goes FLAT" = family moving share <= the healthy moving share under the same
edges (the 15% that the criterion leaves). That verdict is only meaningful for
edges that meet the 85%-FLAT criterion; under the Stage-5 edges healthy ticks read
as moving 76-95% of the time, so every family shows "moving" for free.

Usage: .venv/bin/python -m bench.band_sanity [--episodes data/episodes_dev] [--bands-yaml F]

On main the config holds the Stage-5 edges, so to see the p85 edges extract them:
  git show exp/band-edges-p85:configs/base.yaml > /tmp/p85.yaml
  .venv/bin/python -m bench.band_sanity --bands-yaml /tmp/p85.yaml
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from bench.harness import Episode
from fieldmind.agent.l0_ingest import SensorWindow
from fieldmind.agent.l1_symbolize import direction_and_band
from fieldmind.agent.orchestrator import Orchestrator

HEADLINE = {"A": "drum_level", "B": "feed_water_flow", "C": "drum_pressure",
            "D": "bed_temp_avg", "E": "bed_temp_avg"}
OLD = {"drum_level": [0.03, 0.15, 0.50], "feed_water_flow": [0.05, 0.30, 1.00],
       "steam_flow": [0.05, 0.30, 1.00], "drum_pressure": [0.004, 0.02, 0.08],
       "bed_temp_avg": [0.03, 0.15, 0.50], "ms_temperature": [0.04, 0.20, 0.80]}
HEALTHY_MOVING = 0.15


def moving_share(path: Path, tag: str, bands_list: list[dict], post_onset: bool):
    ep = Episode(path)
    onset = ep.ground_truth.get("fault_onset_t") or 0.0
    win = SensorWindow(window_min=60.0, sample_period_s=5.0)
    n = 0
    mv = [0] * len(bands_list)
    for k in range(int(ep.duration_s // 30)):
        for row in ep.samples_between(k * 30.0, (k + 1) * 30.0):
            win.append(row["t"], row)
        if len(win._t) * win.dt_s < 600.0:
            continue
        if post_onset and (k + 1) * 30.0 < onset:
            continue
        s = Orchestrator._slopes(win)[tag]
        n += 1
        for i, b in enumerate(bands_list):
            mv[i] += direction_and_band(tag, s, b)[0] != "FLAT"
    return n, mv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default="data/episodes_dev")
    ap.add_argument("--bands-yaml", default="configs/base.yaml",
                    help="config whose checks.bands is compared with the Stage-5 "
                         "edges (e.g. one extracted from exp/band-edges-p85)")
    args = ap.parse_args()
    new = yaml.safe_load(Path(args.bands_yaml).read_text())["checks"]["bands"]
    root = Path(args.episodes)
    print(f"{'episode':28s} {'tag':16s} {'ticks':>5s} {'moving NEW':>11s} {'moving OLD':>11s}")
    fam: dict[str, list] = {}
    for p in sorted(root.iterdir()):
        gt = json.loads((p / "ground_truth.json").read_text())
        f = gt["family"]
        if f == "N":
            continue
        tag = HEADLINE[f]
        n, (mn, mo) = moving_share(p, tag, [new, OLD], True)
        print(f"{p.name:28s} {tag:16s} {n:5d} {mn / n:11.2f} {mo / n:11.2f}")
        a = fam.setdefault(f, [0, 0, 0])
        a[0] += n; a[1] += mn; a[2] += mo
    print(f"\nfamily   headline          moving NEW  moving OLD   (healthy moving NEW <= {HEALTHY_MOVING})")
    for f, (n, mn, mo) in sorted(fam.items()):
        share = mn / n
        verdict = "FLAT  <-- STOP" if share <= HEALTHY_MOVING else "moving"
        if f == "E" and share <= HEALTHY_MOVING:
            verdict = "FLAT (E: reported, not a stop)"
        print(f"{f:8s} {HEADLINE[f]:16s} {share:10.2f} {mo / n:11.2f}   {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
