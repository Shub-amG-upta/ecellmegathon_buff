"""
Belief saturation report (Session 1b, step 4). Read-only: reads a runs_*.json,
changes nothing. Mock-backend numbers; not agent results.

Definitions follow the Phase 0a Check 1 table (reports/phase0a_single_agent_fixes.md):
  scored tick  = post-onset tick with a non-empty hypothesis list (fault episodes
                 whose true cause is a library case / a held-out case), or, for
                 no-fault episodes, every tick with a non-empty belief ranking
  at clamp     = belief rank-1 log-odds >= world_model.CLAMP (+4.0, conf 0.982)
  tie          = belief rank-1 and rank-2 have exactly equal log-odds
  shown conf   = hypotheses[0].confidence_shown when the run carries it (what the engineer
                 sees; fix (c)), else hypotheses[0].confidence; --decision-conf forces the latter
  confident-and-wrong-group = shown conf > 0.5 and the shown rank-1 case is not in
                 the true case's group (fault classes only)

ECE (Session 1b): 5 equal-width bins of shown rank-1 confidence; correctness = the
shown rank-1 case lies in the TRUE case's group (the case-level figure is a
secondary diagnostic); scored ticks as above; decided on LIBRARY ticks.

Usage: .venv/bin/python -m bench.belief_saturation results/dev/runs_mock_dev_before.json \
           [--trajectory dev_N03_normal,dev_N09_normal]
"""
from __future__ import annotations

import argparse
import json
import statistics

from bench.evaluator import _groups
from fieldmind.agent.world_model import CLAMP

CONF = 0.5
ECE_BINS = 5          # equal width on [0, 1]; the last bin is closed at 1.0


def ece(pairs: list[tuple[float, bool]], bins: int = ECE_BINS) -> dict:
    """Expected calibration error: sum over bins of (n_bin / N) * |mean conf -
    fraction correct|. `pairs` = (shown rank-1 confidence, rank-1 correct)."""
    n = len(pairs)
    rows = []
    err = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(c, ok) for c, ok in pairs
               if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if not sel:
            rows.append([b, 0, None, None])
            continue
        mc = sum(c for c, _ in sel) / len(sel)
        acc = sum(ok for _, ok in sel) / len(sel)
        err += len(sel) / n * abs(mc - acc)
        rows.append([b, len(sel), round(mc, 3), round(acc, 3)])
    return {"ece": round(err, 4) if n else None, "n": n, "bins": rows}


def conf_of(h: dict, decision: bool = False) -> float:
    """Shown confidence if the run carries one (`confidence_shown`), else the
    decision value. `decision=True` forces the decision value (`confidence`)."""
    if not decision and "confidence_shown" in h:
        return h["confidence_shown"]
    return h.get("confidence", 0.0)


def classify(gt: dict, held_ids) -> str | None:
    if gt["family"] == "N":
        return "no-fault"
    t = gt["root_cause_id"]
    if t in (None, "NONE"):
        return None                      # fault with no case: not scored
    return "held-out" if t in held_ids else "library"


def scored_ticks(run: dict, cls: str):
    onset = run["ground_truth"].get("fault_onset_t") or 0
    for a in run["assessments"]:
        if cls == "no-fault":
            if a.get("belief_ranking"):
                yield a
        elif a["tick"] * 30.0 >= onset and a["hypotheses"] and a.get("belief_ranking"):
            yield a


def summarise(runs: list[dict], decision: bool = False) -> dict:
    gmap, held, held_ids = _groups()
    acc: dict[str, dict] = {}
    for run in runs:
        gt = run["ground_truth"]
        cls = classify(gt, held_ids)
        if cls is None:
            continue
        target = gt["root_cause_id"]
        tgroup = (held.get(target) if cls == "held-out" else gmap.get(target, target))
        d = acc.setdefault(cls, {"n": 0, "clamp1": 0, "ties": 0, "ties_clamp": 0,
                                 "conf": [], "cw": 0, "grp": [], "case": []})
        for a in scored_ticks(run, cls):
            brk = a["belief_ranking"]
            d["n"] += 1
            top = brk[0]["log_odds"]
            d["clamp1"] += top >= CLAMP - 1e-9
            if len(brk) > 1 and top == brk[1]["log_odds"]:
                d["ties"] += 1
                d["ties_clamp"] += top >= CLAMP - 1e-9
            if a["hypotheses"]:
                h = a["hypotheses"][0]
                c = conf_of(h, decision)
                d["conf"].append(c)
                if cls != "no-fault":
                    gok = gmap.get(h.get("case_ref")) == tgroup
                    d["grp"].append((c, gok))
                    d["case"].append((c, h.get("case_ref") == target))
                    if c > CONF:
                        d["cw"] += not gok
    out = {}
    both = {"grp": [], "case": []}
    for cls in ("library", "held-out"):
        if cls in acc:
            both["grp"] += acc[cls]["grp"]
            both["case"] += acc[cls]["case"]
    for cls, d in acc.items():
        n, cf = d["n"], d["conf"]
        dec = [0] * 10
        for c in cf:
            dec[min(9, int(c * 10))] += 1
        out[cls] = {
            "ticks": n,
            "rank1_at_clamp": d["clamp1"], "rank1_at_clamp_share": round(d["clamp1"] / n, 3),
            "ties": d["ties"], "tie_share": round(d["ties"] / n, 3),
            "ties_at_clamp": d["ties_clamp"],
            "ties_at_clamp_share_of_ties": round(d["ties_clamp"] / d["ties"], 3) if d["ties"] else None,
            "shown_conf_mean": round(statistics.mean(cf), 3) if cf else None,
            "shown_conf_median": round(statistics.median(cf), 3) if cf else None,
            "shown_conf_ge_0.9_share": round(sum(c >= 0.9 for c in cf) / len(cf), 3) if cf else None,
            "shown_conf_deciles": dec,
            "confident_and_wrong_group": d["cw"],
            "confident_and_wrong_group_share": round(d["cw"] / n, 3) if cls != "no-fault" else None,
            "low_conf_share": round(sum(c <= CONF for c in cf) / len(cf), 3) if cf else None,
            # correctness for the DECISION ECE = shown rank-1 case lies in the TRUE
            # case's group; the case-level ECE is a secondary diagnostic
            "ece_group": ece(d["grp"])["ece"] if d["grp"] else None,
            "ece_case": ece(d["case"])["ece"] if d["case"] else None,
            "ece_group_bins": ece(d["grp"])["bins"] if d["grp"] else None,
        }
    out["library+held-out"] = {"ece_group": ece(both["grp"])["ece"],
                               "ece_case": ece(both["case"])["ece"],
                               "ticks": len(both["grp"])}
    return out


def trajectories(runs: list[dict], ids: list[str]) -> dict:
    out = {}
    for run in runs:
        if run["episode_id"] not in ids:
            continue
        rows = []
        for a in run["assessments"]:
            brk = a.get("belief_ranking") or []
            if brk and a["hypotheses"]:
                h = a["hypotheses"][0]
                rows.append((a["tick"], brk[0]["case_ref"], brk[0]["log_odds"],
                             h.get("confidence"), conf_of(h)))
        out[run["episode_id"]] = rows
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("--trajectory", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--decision-conf", action="store_true",
                    help="read the decision confidence even if the run carries "
                         "confidence_shown")
    args = ap.parse_args()
    runs = json.load(open(args.runs))
    s = summarise(runs, decision=args.decision_conf)
    keys = ["ticks", "rank1_at_clamp", "rank1_at_clamp_share", "ties", "tie_share",
            "ties_at_clamp", "ties_at_clamp_share_of_ties", "shown_conf_mean",
            "shown_conf_median", "shown_conf_ge_0.9_share", "confident_and_wrong_group",
            "confident_and_wrong_group_share", "low_conf_share", "ece_group", "ece_case"]
    cls = [c for c in ("library", "held-out", "no-fault") if c in s]
    print(f"{'':36s}" + "".join(f"{c:>12s}" for c in cls))
    for k in keys:
        print(f"{k:36s}" + "".join(f"{str(s[c][k]):>12s}" for c in cls))
    print(f"{'shown conf deciles 0.0-1.0':36s}")
    for c in cls:
        print(f"  {c:10s} {s[c]['shown_conf_deciles']}")
    print(f"library+held-out ECE (group / case): {s['library+held-out']['ece_group']} / "
          f"{s['library+held-out']['ece_case']}  ({s['library+held-out']['ticks']} ticks)")
    for c in ("library", "held-out"):
        if c in s:
            print(f"  {c} ECE bins [bin, n, mean conf, group-correct]: {s[c]['ece_group_bins']}")
    if args.trajectory:
        for ep, rows in trajectories(runs, args.trajectory.split(",")).items():
            print(f"\n{ep}: tick, belief rank-1, log-odds, decision conf, shown conf")
            for r in rows:
                print(f"  t{r[0]:<4d} {r[1]:8s} {r[2]:+.3f}  {r[3]:.3f}  {r[4]:.3f}")
    if args.json:
        json.dump(s, open(args.json, "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
