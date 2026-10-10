"""
Look-alike case groups, computed from data/kb/case_library.json.

Two cases are look-alikes when their MOVING six-tag signatures overlap: the set
of (tag, direction) pairs with direction != FLAT, plus the balance pseudo-tags
(water_balance, energy_balance). Band is ignored on purpose -- the simulator's
noisy per-tick slopes flip bands constantly (reports/stage5_case_library.md
item 2). FLAT triples are ignored because on a healthy plant nearly everything
is flat and predicting flatness does not distinguish a case.

    jaccard(a, b) = |A & B| / |A | B|      (two empty sets: identical -> 1.0)

Groups are the connected components of the graph with an edge wherever
jaccard >= THRESHOLD. Every library case not joined to another is a singleton
group of its own.

Held-out cases (data/kb/holdout.json) have no signatures on disk, so they
cannot be grouped by computation. Their membership is CITED from the Stage 5
report and kept in a separate block, labelled as cited.

    python -m bench.case_groups            # rewrite data/kb/case_groups.json
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIBRARY = ROOT / "data/kb/case_library.json"
HOLDOUT = ROOT / "data/kb/holdout.json"
OUT = ROOT / "data/kb/case_groups.json"

# "at least half the moving pairs shared". The result is stable for every
# threshold in (0.286, 0.5] -- stability_interval in the output records the
# interval actually measured, so the choice is checkable rather than asserted.
THRESHOLD = 0.5

# CITED, not computed: reports/stage5_case_library.md "Item 2". Held-out cases
# have no signature in the library, so the Stage 5 cluster tables are the only
# source. RCA-02 is cited there only as a PARTIAL cluster (S) with RCA-05, which
# Stage 5 itself says is "partially separable on six tags"; it is left
# unassigned rather than forced into a group. Nothing in the 30 episodes has
# RCA-02, 08, 12 or 17 as truth, so only RCA-06 is ever used in scoring.
HELDOUT_CITED = {
    "RCA-06": ("RCA-14", "Cluster T (total heat loss), Stage 5 item 2"),
    "RCA-08": ("RCA-14", "Cluster T (total heat loss), Stage 5 item 2"),
    "RCA-12": ("RCA-11", "Cluster L (water ingress, level held), Stage 5 item 2"),
    "RCA-17": ("RCA-09", "Cluster empty (invisible on six tags), Stage 5 item 2"),
}
HELDOUT_UNASSIGNED = {
    "RCA-02": "Stage 5 cluster S (with RCA-05) is only PARTIAL; not forced into a group",
}


def moving_pairs(case: dict) -> set[tuple[str, str]]:
    out = set()
    for k, v in case.get("signature", {}).items():
        if isinstance(v, list):
            if v[0] != "FLAT":
                out.add((k, v[0]))
        else:                                   # "tag|DIR|BAND": weight
            tag, d, _ = k.split("|")
            out.add((tag, d))
    return out


def jaccard(a: set, b: set) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def components(ids: list[str], pairs: dict, thr: float) -> list[list[str]]:
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b), j in pairs.items():
        if j >= thr:
            parent[find(a)] = find(b)
    comps: dict[str, list[str]] = {}
    for i in ids:
        comps.setdefault(find(i), []).append(i)
    return sorted((sorted(c) for c in comps.values()), key=lambda c: c[0])


def compute(library_path: Path = LIBRARY) -> dict:
    cases = json.loads(Path(library_path).read_text())["cases"]
    ids = sorted(c["case_id"] for c in cases)
    mv = {c["case_id"]: moving_pairs(c) for c in cases}
    pairs = {(a, b): jaccard(mv[a], mv[b]) for a, b in itertools.combinations(ids, 2)}

    comps = components(ids, pairs, THRESHOLD)

    # Stability: the interval of thresholds between the two nearest distinct
    # pair values around THRESHOLD over which the components do not change.
    # Exact values (not rounded): a pair at lo must stay OUT at any t > lo.
    vals = sorted(set(pairs.values()))
    lo = max([v for v in vals if v < THRESHOLD], default=0.0)
    hi = min([v for v in vals if v >= THRESHOLD], default=1.0)
    assert components(ids, pairs, (lo + hi) / 2) == comps
    assert components(ids, pairs, hi) == comps, "grouping unstable above THRESHOLD"
    assert components(ids, {k: v for k, v in pairs.items() if v > lo}, hi) == comps

    groups = []
    for c in comps:
        if len(c) == 1:
            continue
        shared = set.intersection(*(mv[i] for i in c))
        groups.append({"id": "+".join(c), "members": c,
                       "shared_moving_pairs": sorted(map(list, shared)),
                       "identical_moving_signature": len({frozenset(mv[i]) for i in c}) == 1,
                       "pairwise_jaccard": {f"{a}|{b}": round(pairs[(a, b)], 3)
                                            for a, b in itertools.combinations(c, 2)}})
    group_id = {i: g["id"] for g in groups for i in g["members"]}
    singletons = [c[0] for c in comps if len(c) == 1]

    near = sorted(((round(j, 3), a, b) for (a, b), j in pairs.items()
                   if 0.0 < j < THRESHOLD), reverse=True)[:5]
    heldout = {k: {"group_id": group_id.get(anchor, anchor), "joins": anchor,
                   "provenance": "CITED, not computed", "source": src}
               for k, (anchor, src) in HELDOUT_CITED.items()}
    heldout.update({k: {"group_id": None, "provenance": "CITED, not computed",
                        "source": why} for k, why in HELDOUT_UNASSIGNED.items()})
    return {
        "_comment": "GENERATED by `python -m bench.case_groups` -- do not edit by hand. "
                    "Computed from data/kb/case_library.json; held-out membership is cited.",
        "method": "connected components of moving (tag, direction) Jaccard >= threshold; "
                  "FLAT triples and bands ignored; identical (incl. both empty) signatures group",
        "threshold": THRESHOLD,
        "stability_interval": [round(lo, 3), round(hi, 3)],
        "stability_note": "components identical for any threshold t with lo < t <= hi",
        "groups": groups,
        "singletons": singletons,
        "nearest_pairs_below_threshold": [{"jaccard": j, "a": a, "b": b} for j, a, b in near],
        "heldout_membership": heldout,
    }


def load_group_map(path: Path = OUT) -> tuple[dict, dict]:
    """(case_id -> group_id, heldout case_id -> group_id|None). Singletons map
    to their own id. Held-out ids are only for truth lookup, never rank-1."""
    d = json.loads(Path(path).read_text())
    m = {i: i for i in d["singletons"]}
    for g in d["groups"]:
        for i in g["members"]:
            m[i] = g["id"]
    held = {k: v["group_id"] for k, v in d["heldout_membership"].items()}
    return m, held


def heldout_ids(path: Path = HOLDOUT) -> set[str]:
    return set(json.loads(Path(path).read_text())["split"]["holdout_case_ids"])


def main() -> None:
    d = compute()
    OUT.write_text(json.dumps(d, indent=2) + "\n")
    print(json.dumps(d, indent=2))


if __name__ == "__main__":
    main()
