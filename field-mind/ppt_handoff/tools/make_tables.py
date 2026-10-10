#!/usr/bin/env python3
"""Builds the CSV tables in ppt_handoff/data/ from the saved board runs.

    .venv/bin/python ppt_handoff/tools/make_tables.py

Read-only on results/. Every number in the CSVs comes from a run file or a
summary file written by the benchmark tools; nothing is estimated. Scoring
follows bench/evaluator.py (group top-1 from fault onset onward, look-alike
groups from data/kb/case_groups.json)."""
from __future__ import annotations

import csv
import glob
import gzip
import json
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bench.stage_monitor_multi import episode_dir, load_truth  # noqa: E402

OUT = ROOT / "ppt_handoff/data"
B = ROOT / "results/benchmarks"
P = ROOT / "results/presentation_benchmark/single_agent_llama32-3b"
FAULT = ["ep_A01_fcv_seize", "ep_A02_fcv_seize_fast", "ep_A03_bfp_suction", "ep_B01_tube_leak",
         "ep_B02_tube_leak_fast", "ep_B03_tube_leak_slow", "ep_C01_wet_coal", "ep_C02_feeder_trip",
         "ep_C03_wet_coal_mild", "ep_D01_high_cv_coal"]
FIRST4 = ["ep_A01_fcv_seize", "ep_B01_tube_leak", "ep_C01_wet_coal", "ep_D01_high_cv_coal"]
SIX = FIRST4 + ["ep_E01_fouling_drift", "ep_N01_normal"]
TICK = 30.0

# (config label, description, where each episode's files are)
CONFIGS = [
    ("single", "single agent, Llama 3.2 3B on NPU, 1 diagnostician + verifier, no grammar, 256-token answers",
     {**{e: ("presentation", e) for e in SIX}, "ep_A01_fcv_seize": ("single_6mark", None),
      "ep_N01_normal": ("single_6mark", None)}),
    ("multi_model", "multi-agent v2 (fast.yaml, group letters ON, Gemma note reader), merge rule model",
     {e: ("multi_v2_6mark", None) for e in SIX}),
    ("multi_tiebreak", "multi-agent v2 prompts, merge rule tiebreak",
     {e: ("multi_tiebreak_6mark", None) for e in SIX}),
    ("multi_nudge", "multi-agent v2 prompts, merge rule nudge",
     {**{e: ("multi_nudge_6mark", None) for e in SIX},
      **{e: ("multi_nudge_6.1mark", None) for e in FAULT if e not in FIRST4}}),
    ("multi_v3", "multi-agent v3 (v3.yaml: no group letters, raw notes, merge rule hybrid)",
     {e: ("multi_v3", None) for e in FAULT + ["ep_E01_fouling_drift", "ep_N01_normal"]}),
]


def paths(folder: str, ep: str, arch: str):
    if folder == "presentation":
        return P / f"{ep}_single.run.json.gz", P / f"{ep}_single.summary.json"
    return B / folder / f"{ep}_{arch}.run.json.gz", B / folder / f"{ep}_{arch}.summary.json"


def load_run(p: Path) -> dict:
    d = json.load(gzip.open(p))
    return d[0] if isinstance(d, list) else d


def sig(x): return 1 / (1 + math.exp(-x))


def shown_belief(a):
    br = a.get("belief_ranking") or []
    out = []
    for i, b in enumerate(br[:3]):
        rival = max((x["log_odds"] for j, x in enumerate(br) if j != i), default=None)
        out.append((b["case_ref"], b["confidence"] if rival is None else min(b["confidence"], sig(b["log_odds"] - rival))))
    return out


def auroc(p, n):
    if not p or not n:
        return None
    return sum((x > y) + 0.5 * (x == y) for x in p for y in n) / (len(p) * len(n))


def r3(x):
    return None if x is None else round(x, 3)


def episode_row(cfg: str, ep: str, folder: str) -> dict:
    arch = "single" if cfg == "single" else "multi"
    rp, sp = paths(folder, ep, arch)
    run, summ = load_run(rp), json.load(open(sp))
    ev = summ["evaluation"]
    A = run["assessments"]
    truth = load_truth(episode_dir(ep))
    row = {"config": cfg, "episode": ep, "family": ep[3], "source_folder": folder,
           "ticks": len(A)}
    # ---- cost and speed (every config)
    envs = [e for a in A for e in a.get("envelopes", [])]
    envs += [t["envelope"] for a in A for t in (a.get("multi") or {}).get("text", [])]
    calls = [c for e in envs for c in e.get("calls", [])]
    nonquiet = [a for a in A if a.get("triage") != "QUIET"]
    tl = [a["tick_latency_ms"] / 1000 for a in nonquiet if a.get("tick_latency_ms") is not None]
    lat = [e["latency_ms"] / 1000 for e in envs if e.get("latency_ms")]
    row.update(model_calls=len(envs), unusable_answers=sum(e.get("status") != "ok" for e in envs),
               tokens_in=sum((e.get("tokens") or {}).get("prefill") or 0 for e in envs),
               tokens_out=sum((e.get("tokens") or {}).get("decode") or 0 for e in envs),
               median_call_s=r3(st.median(lat)) if lat else None,
               nonquiet_ticks=len(nonquiet),
               median_nonquiet_tick_s=r3(st.median(tl)) if tl else None,
               max_tick_s=r3(max(tl)) if tl else None,
               ticks_over_30s=sum(x > 30 for x in tl),
               wall_s=summ.get("meta", {}).get("wall_s") or run.get("wall_clock_s"),
               chip_c_start=summ.get("meta", {}).get("chip_c_before"),
               chip_c_end=summ.get("meta", {}).get("chip_c_after"),
               faithfulness=ev["T3_faithfulness"].get("faithfulness"),
               action_precision=ev["T4_actions"].get("precision") if ev["T4_actions"].get("applicable") else None,
               action_recall=ev["T4_actions"].get("recall") if ev["T4_actions"].get("applicable") else None,
               state_macro_f1=ev["T1_state"]["macro_f1"],
               false_alarms_per_h=ev["T5_false_positives"].get("fp_per_hour") if ev["T5_false_positives"].get("applicable") else None)
    if not truth or not truth["scored"]:
        return row
    # ---- diagnosis quality, scored ticks
    g, tg, tgt, held = truth["gmap"], truth["tgroup"], truth["target"], truth["heldout"]
    right = lambda c: c is not None and g.get(c) == tg
    rows = []
    for a in A:
        if a["tick"] * TICK < truth["onset"] or not a["hypotheses"]:
            continue
        refs = [h.get("case_ref") for h in a["hypotheses"][:3]]
        conf = a["hypotheses"][0].get("confidence_shown", a["hypotheses"][0].get("confidence"))
        rank = next((i + 1 for i, c in enumerate(refs) if right(c)), None)
        b1 = shown_belief(a)[0][0] if a.get("belief_ranking") else None
        rows.append({"tick": a["tick"], "r1": refs[0], "ok": right(refs[0]), "exact": refs[0] == tgt,
                     "top3": rank is not None, "rr": 1 / rank if rank else 0.0, "conf": conf,
                     "bel_ok": right(b1)})
    n = len(rows)
    ok = [r["ok"] for r in rows]
    first = next((r["tick"] for r in rows if r["ok"]), None)
    flips = sum(rows[i]["r1"] != rows[i - 1]["r1"] for i in range(1, n))
    cr = [r["conf"] for r in rows if r["ok"] and r["conf"] is not None]
    cw = [r["conf"] for r in rows if not r["ok"] and r["conf"] is not None]
    trip = truth["gt"].get("trip_t")
    pre = [r for r in rows if trip and r["tick"] * TICK < trip]
    row.update(true_cause=tgt, true_group=tg, heldout=held, scored_ticks=n,
               group_top1=r3(sum(ok) / n), belief_group_top1=r3(sum(r["bel_ok"] for r in rows) / n),
               exact_top1=None if held else r3(sum(r["exact"] for r in rows) / n),
               true_group_in_top3=r3(sum(r["top3"] for r in rows) / n), mrr=r3(sum(r["rr"] for r in rows) / n),
               ticks_helped_vs_belief=sum(r["ok"] and not r["bel_ok"] for r in rows),
               ticks_harmed_vs_belief=sum(r["bel_ok"] and not r["ok"] for r in rows),
               first_right_min_after_onset=None if first is None else r3((first * TICK - truth["onset"]) / 60),
               right_before_trip=r3(sum(r["ok"] for r in pre) / len(pre)) if pre else None,
               lead_time_min_exact=ev["T6_lead_time"].get("lead_time_min") if ev["T6_lead_time"].get("applicable") else None,
               rank1_changes_per_100=r3(100 * flips / max(1, n - 1)),
               conf_when_right=r3(st.mean(cr)) if cr else None, conf_when_wrong=r3(st.mean(cw)) if cw else None,
               conf_auroc=r3(auroc(cr, cw)))
    return row


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for cfg, _, eps in CONFIGS:
        for ep, (folder, _) in eps.items():
            rows.append(episode_row(cfg, ep, folder))
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with open(OUT / "episode_metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(rows)

    # ---- config means on common episode sets
    summ_rows = []
    keys = ["group_top1", "belief_group_top1", "exact_top1", "true_group_in_top3", "mrr",
            "ticks_helped_vs_belief", "ticks_harmed_vs_belief", "first_right_min_after_onset",
            "right_before_trip", "rank1_changes_per_100", "conf_when_right", "conf_when_wrong",
            "conf_auroc", "faithfulness", "unusable_answers", "model_calls", "tokens_in",
            "median_call_s", "median_nonquiet_tick_s", "ticks_over_30s", "wall_s"]
    for label, eps in (("first_4_fault_episodes (A01,B01,C01,D01)", FIRST4), ("all_10_fault_episodes", FAULT)):
        for cfg, desc, _ in CONFIGS:
            sel = [r for r in rows if r["config"] == cfg and r["episode"] in eps]
            if len(sel) != len(eps):
                continue
            out = {"episode_set": label, "config": cfg, "description": desc, "n_episodes": len(sel)}
            for k in keys:
                v = [r[k] for r in sel if r.get(k) is not None]
                if not v:
                    out[k] = None
                elif k in ("ticks_helped_vs_belief", "ticks_harmed_vs_belief", "unusable_answers",
                           "model_calls", "tokens_in", "ticks_over_30s", "wall_s"):
                    out[k] = round(sum(v), 1)
                else:
                    out[k] = r3(st.mean(v))
                out[k + "_n"] = len(v)
            summ_rows.append(out)
    cols = list(dict.fromkeys(k for r in summ_rows for k in r))
    with open(OUT / "config_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(summ_rows)

    # ---- NPU screening of the four candidate models
    scr = []
    for m in ("llama32-3b", "qwen3-1.7b", "gemma3-1b-qat", "qwen25-0.5b"):
        d = json.load(open(ROOT / f"results/board/A/{m}/screen.summary.json"))
        scr.append({"model": m, "file": d["model_file"], "bytes": None,
                    "tensor_types": json.dumps(d["gguf"].get("type_counts") if isinstance(d["gguf"], dict) else d["gguf"]),
                    "layers_on_npu": f"{d['offload']['layers_offloaded']}/{d['offload']['layers_total']}",
                    "prefill_tok_s": d["server_prefill_tok_s"], "decode_tok_s": d["server_decode_tok_s"],
                    "verified_diagnosis_s (700-token prompt + 60-token answer, plus 350 + 30)": d["measured_verified_s"],
                    "screening_calls": len(d["calls"])})
    sizes = {"llama32-3b": 2012612832, "qwen3-1.7b": 1460395904, "gemma3-1b-qat": 720425280, "qwen25-0.5b": 352154624}
    for r in scr:
        r["bytes"] = sizes[r["model"]]
    with open(OUT / "npu_model_screening.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(scr[0])); w.writeheader(); w.writerows(scr)

    # ---- lane speeds measured inside the benchmark runs
    agg = {}
    def add(key, c):
        g = agg.setdefault(key, {"pt": 0, "pms": 0.0, "dt": 0, "dms": 0.0, "n": 0, "pk": [], "lat": []})
        g["pt"] += c["prefill"]; g["pms"] += c["prefill_ms"]; g["dt"] += c["decode"]; g["dms"] += c["decode_ms"]
        g["n"] += 1; g["pk"].append(c["prefill"]); g["lat"].append(c["latency_ms"])
    for f in glob.glob(str(B / "multi_*/*_multi*.run.json.gz")):
        for a in load_run(Path(f))["assessments"]:
            envs = list(a.get("envelopes", [])) + [t["envelope"] for t in (a.get("multi") or {}).get("text", [])]
            for e in envs:
                for c in e.get("calls", []):
                    if c.get("prefill_ms") and c.get("decode_ms") and c.get("prefill") and c.get("decode"):
                        model = "Llama 3.2 3B" if e.get("backend") == "npu" else "Gemma 3 1B"
                        add((f"multi-agent {e.get('backend').upper()} lane ({model}), answer grammar on", e["agent"]), c)
    for f in list(glob.glob(str(B / "single_6mark/*_single.run.json.gz"))) + list(glob.glob(str(P / "*_single.run.json.gz"))):
        for a in load_run(Path(f))["assessments"]:
            for e in a.get("envelopes", []):
                for c in e.get("calls", []):
                    if c.get("prefill_ms") and c.get("decode_ms") and c.get("prefill") and c.get("decode"):
                        add(("single agent NPU lane (Llama 3.2 3B), no grammar", e["agent"]), c)
    lanes = []
    for (lane, agent), g in sorted(agg.items()):
        lanes.append({"lane": lane, "agent": agent, "calls": g["n"], "prompt_tokens_median": st.median(g["pk"]),
                      "answer_tokens_mean": round(g["dt"] / g["n"], 1),
                      "prefill_tok_s": round(g["pt"] / g["pms"] * 1000), "decode_tok_s": round(g["dt"] / g["dms"] * 1000, 1),
                      "decode_share_of_model_time": round(g["dms"] / (g["pms"] + g["dms"]), 3),
                      "call_ms_median": round(st.median(g["lat"]))})
    with open(OUT / "lane_speeds_in_runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, list(lanes[0])); w.writeheader(); w.writerows(lanes)
    print("wrote", sorted(p.name for p in OUT.glob("*.csv")))


if __name__ == "__main__":
    main()
