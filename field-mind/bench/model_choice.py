"""
Phase 0b model-choice table and the ranking question. Read-only: reads runs_*.json
files written by run_demo.py, changes nothing.

  .venv/bin/python -m bench.model_choice results/phase0b/runs_mock_dev3.json \
        results/phase0b/runs_llamaserver_dev3_<model>.json [--json out.json]

One column per runs file. Rows: library top-1 / group / sep_named, Q3 (after the
gate) and raw model citation faithfulness, broken-JSON rates, envelope status
counts, ECE (group) and confident-and-wrong in BOTH confidence views (decision
and shown), low-confidence share in both views, Q1 / Q4 / S4, prompt and answer
tokens, diagnosis latency, chip temperature, and the ranking statistic.

RANKING QUESTION (pre-registered in reports/phase0b_lanes_model_choice.md before
any real-model run). Population: post-onset LIBRARY ticks with a non-empty
belief ranking where the diagnostician envelope is ok and the gate kept its order
(raw citation faithfulness >= min_faithfulness), and where the model's own rank-1
group is NOT the group of any case in belief's tied top set. Scoring: model
correct = its rank-1 case is in the true group (a model-only idea with no case is
incorrect); belief correct is tie-fair = share of the tied top set in the true
group. Rule: n < 30 -> INCONCLUSIVE; p_belief - p_model >= 0.10 and belief >= model
in every episode with >= 10 such ticks -> BELIEF_DECIDES; the mirror image ->
MODEL_DECIDES; anything else -> INCONCLUSIVE (status quo kept).
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from bench.belief_saturation import summarise as saturation
from bench.evaluator import _groups, aggregate, evaluate

MIN_FAITH = 0.5          # configs/base.yaml agent.min_faithfulness (the gate's rule)
ANSWER_CAP = 256         # configs/base.yaml agent.max_tokens (share of answers AT the cap)

# MODEL-CHOICE RULE (human-approved, reports/phase0b_lanes_model_choice.md). Hard
# constraints, then rank. All constants below are human decisions, not derived.
MAX_BROKEN_FIRST = 0.10  # first reply unparseable
MAX_BROKEN_AFTER = 0.02  # still invalid after the one repair
MAX_PROJECTED_S = 10.0   # projected verified-diagnosis time on the NPU lane (PROJECTION)
GROUP_TIE = 0.05         # library group top-1 tie band
FAITH_TIE = 0.02         # model citation faithfulness tie band
# Deterministic floor: library group top-1 of the MOCK run of the same 3 dev
# episodes (results/phase0b/runs_mock_dev3.json, Session 2). Used only when no
# mock column is in the table; a mock column, if given, supplies it.
FLOOR_DEFAULT = 0.501
# Target multi-agent call shape (multi_agent_plan.pdf: prompts < 1280 tokens,
# 60-token diagnostician answers, 30-token verifier answers, ~10 s verified diagnosis)
PROJ_DIAG = (700, 60)    # (prompt tokens, answer tokens)
PROJ_VER = (350, 30)
RULE_MARGIN = 0.10       # pre-registered decision constant (design choice)
RULE_MIN_N = 30          # pre-registered
RULE_EP_MIN = 10         # an episode joins the consistency clause at >= 10 ticks


def _diag_env(a: dict) -> dict | None:
    for e in a.get("envelopes", []) or []:
        if e.get("agent") == "diagnostician":
            return e
    return None


def _raw_faith(env: dict, fact_ids: set) -> float:
    cited = env.get("cited_facts") or []
    return sum(c in fact_ids for c in cited) / len(cited) if cited else 1.0


def model_rank1_case(env: dict, brk: list[dict], gmap: dict) -> str | None:
    """The model's own rank-1, resolved the way orchestrator._merge resolves it:
    by cause against the deterministic hypotheses first, then by case_ref.
    None = a model-only idea with no case."""
    hyps = (env.get("payload") or {}).get("hypotheses") or []
    if not hyps:
        return None
    h = hyps[0]
    by_cause = {b.get("cause"): b.get("case_ref") for b in brk}
    if h.get("cause") in by_cause and by_cause[h.get("cause")]:
        return by_cause[h.get("cause")]
    ref = h.get("case_ref")
    return ref if ref in gmap else None


def ranking_question(runs: list[dict], use_shown: bool = False) -> dict:
    """use_shown=True replaces the model's own rank-1 with the SHOWN rank-1
    (hypotheses[0]), which is the population definition the mock prior used."""
    gmap, held, held_ids = _groups()
    per_ep, pooled = {}, {"n": 0, "belief": 0.0, "model": 0.0, "model_only": 0}
    for run in runs:
        gt = run["ground_truth"]
        target = gt.get("root_cause_id")
        if gt.get("family") == "N" or target in (None, "NONE") or target in held_ids:
            continue
        tgroup = gmap.get(target, target)
        onset = gt.get("fault_onset_t") or 0
        d = {"n": 0, "belief": 0.0, "model": 0.0, "model_only": 0}
        for a in run["assessments"]:
            brk = a.get("belief_ranking") or []
            if a["tick"] * 30.0 < onset or not brk or not a["hypotheses"]:
                continue
            if use_shown:
                m = a["hypotheses"][0].get("case_ref")
                m = m if m in gmap else None
            else:
                env = _diag_env(a)
                if env is None or env.get("status") != "ok":
                    continue
                if _raw_faith(env, {f["id"] for f in a["facts"]}) < MIN_FAITH:
                    continue
                m = model_rank1_case(env, brk, gmap)
            top = [b for b in brk if b["log_odds"] == brk[0]["log_odds"]]
            top_groups = {gmap.get(b.get("case_ref")) for b in top}
            mg = gmap.get(m) if m else None
            if m is not None and mg in top_groups:
                continue                                   # agrees with a leader
            d["n"] += 1
            d["model_only"] += m is None
            d["model"] += (mg == tgroup) if m is not None else 0.0
            d["belief"] += sum(gmap.get(b.get("case_ref")) == tgroup for b in top) / len(top)
        per_ep[run["episode_id"]] = d
        for k in pooled:
            pooled[k] += d[k]

    def rates(d):
        n = d["n"]
        return {"n": n, "model_only": d["model_only"],
                "p_belief": round(d["belief"] / n, 3) if n else None,
                "p_model": round(d["model"] / n, 3) if n else None}
    out = {"pooled": rates(pooled), "per_episode": {e: rates(d) for e, d in per_ep.items()}}
    out["verdict"] = verdict(out)
    return out


def verdict(r: dict) -> str:
    p = r["pooled"]
    if p["n"] < RULE_MIN_N:
        return "INCONCLUSIVE (n < 30)"
    # rates are rounded to 3 d.p.; round the difference too, so an exact 0.10
    # margin is not lost to float error (0.50 - 0.40 = 0.0999...)
    diff = round(p["p_belief"] - p["p_model"], 3)
    eps = [e for e in r["per_episode"].values() if e["n"] >= RULE_EP_MIN]
    if diff >= RULE_MARGIN and all(e["p_belief"] >= e["p_model"] for e in eps):
        return "BELIEF_DECIDES"
    if -diff >= RULE_MARGIN and all(e["p_model"] >= e["p_belief"] for e in eps):
        return "MODEL_DECIDES"
    return "INCONCLUSIVE"


def _known(envs: list[dict], key: str) -> list:
    return [e["tokens"][key] for e in envs
            if (e.get("tokens") or {}).get(key) is not None]


def projected_s(prefill_rate: float | None, decode_rate: float | None) -> float | None:
    """PROJECTION, not a measurement: the target multi-agent shape (diagnostician
    700 in / 60 out, then verifier 350 in / 30 out) at this lane's measured
    server rates. Rates measured at the single-agent prompt size (~2,000 tokens);
    prefill rate at 700 tokens may differ."""
    if not prefill_rate or not decode_rate:
        return None
    t = sum(p / prefill_rate + a / decode_rate for p, a in (PROJ_DIAG, PROJ_VER))
    return round(t, 2)


def call_stats(runs: list[dict]) -> dict:
    envs = [e for r in runs for a in r["assessments"] for e in a.get("envelopes", []) or []]
    diag = [e for e in envs if e.get("agent") == "diagnostician"]
    calls = sum(r.get("diag_calls", 0) for r in runs)
    first_fail = sum(round(r.get("parse_failure_rate", 0) * r.get("diag_calls", 0)) for r in runs)
    status = {}
    for e in envs:
        status[f"{e['agent']}:{e['status']}"] = status.get(f"{e['agent']}:{e['status']}", 0) + 1
    ok_diag = [e for e in diag if e.get("status") == "ok"]
    lat = sorted(e["latency_ms"] for e in ok_diag if e.get("latency_ms") is not None)
    raw_c = raw_v = 0
    for r in runs:
        for a in r["assessments"]:
            ids = {f["id"] for f in a["facts"]}
            for e in a.get("envelopes", []) or []:
                if e.get("agent") == "diagnostician" and e.get("status") == "ok":
                    raw_c += len(e.get("cited_facts") or [])
                    raw_v += sum(c in ids for c in e.get("cited_facts") or [])

    def mean(xs):
        return round(statistics.mean(xs), 1) if xs else None

    def p95(xs):
        xs = sorted(xs)
        return xs[int(0.95 * (len(xs) - 1))] if xs else None

    # per backend call (envelope `calls`): server rates and answer lengths
    calls_ok = [c for e in envs for c in (e.get("calls") or []) if c.get("status") == "ok"]
    pre = [c for c in calls_ok if c.get("prefill") is not None and c.get("prefill_ms")]
    dec = [c for c in calls_ok if c.get("decode") is not None and c.get("decode_ms")]
    pr = (sum(c["prefill"] for c in pre) / (sum(c["prefill_ms"] for c in pre) / 1000)
          if pre else None)
    dr = (sum(c["decode"] for c in dec) / (sum(c["decode_ms"] for c in dec) / 1000)
          if dec else None)
    diag_calls_ok = [c for e in diag for c in (e.get("calls") or []) if c.get("status") == "ok"]
    ans = [c["decode"] for c in diag_calls_ok if c.get("decode") is not None]
    ticks = [a for r in runs for a in r["assessments"]]
    return {
        "server_prefill_tok_s": round(pr, 1) if pr else None,
        "server_decode_tok_s": round(dr, 2) if dr else None,
        "rate_calls_missing": len(calls_ok) - min(len(pre), len(dec)),
        "projected_verified_s": projected_s(pr, dr),
        "answer_tokens_per_call_mean": mean(ans),
        "answer_tokens_per_call_p95": p95(ans),
        "answer_share_at_cap": round(sum(a >= ANSWER_CAP for a in ans) / len(ans), 3) if ans else None,
        "tick_deadline_misses": sum(bool(a.get("deadline_miss")) for a in ticks),
        "tick_deadline_miss_rate": round(sum(bool(a.get("deadline_miss")) for a in ticks)
                                         / len(ticks), 4) if ticks else None,
        "diag_calls": calls,
        "broken_json_first_reply": round(first_fail / calls, 3) if calls else None,
        "broken_json_after_repair": (round(sum(e["status"] == "invalid_schema" for e in diag)
                                           / len(diag), 3) if diag else None),
        "envelope_status": status,
        "model_citation_faithfulness_raw": round(raw_v / raw_c, 3) if raw_c else None,
        "n_model_citations": raw_c,
        # server-reported counts only; None (not reported) is excluded and counted.
        # (envelope prompt_tokens falls back to an est_tokens estimate, so it is not used)
        "prompt_tokens_mean": mean(_known(diag, "prefill")),
        "prompt_tokens_missing": len(diag) - len(_known(diag, "prefill")),
        "answer_tokens_mean": mean(_known(ok_diag, "decode")),
        "answer_tokens_missing": len(ok_diag) - len(_known(ok_diag, "decode")),
        "diag_latency_ms_mean": mean(lat),
        "diag_latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
        "models": sorted({e.get("model") for e in diag}),
        "lanes": sorted({e.get("backend") for e in diag}),
    }


def column(path: str) -> dict:
    runs = json.load(open(path))
    s = aggregate([evaluate(r) for r in runs])
    lib = s["Q2_library"]
    sat_d, sat_s = saturation(runs, decision=True), saturation(runs, decision=False)
    col = {
        "file": path, "n_episodes": len(runs),
        "library_top1": lib.get("top1"), "library_group_top1": lib.get("group_top1"),
        "library_sep_named": lib.get("sep_named"),
        "library_belief_top1_tiefair": lib.get("belief_top1_tiefair"),
        "library_belief_group": lib.get("belief_group"),
        "Q3_faithfulness_after_gate": s["Q3_faithfulness"],
        "Q1_macro_f1": s["Q1_macro_f1"], "Q4_precision": s["Q4_action_precision"],
        "Q4_recall": s["Q4_action_recall"], "S4_llm_invocation": s["S4_llm_invocation_rate"],
    }
    for view, sat in (("decision", sat_d), ("shown", sat_s)):
        L = sat.get("library", {})
        col[f"ece_group_{view}"] = L.get("ece_group")
        col[f"confident_wrong_{view}"] = L.get("confident_and_wrong_group")
        col[f"confident_wrong_share_{view}"] = L.get("confident_and_wrong_group_share")
        col[f"low_conf_share_{view}"] = L.get("low_conf_share")
    col["scored_library_ticks"] = sat_d.get("library", {}).get("ticks")
    col.update(call_stats(runs))
    col["chip_temp"] = {r["episode_id"]: [r.get("chip_temp_start"), r.get("chip_temp_end")]
                        for r in runs if "chip_temp_start" in r}
    col["group_top1_by_episode"] = {
        e["episode_id"]: e["T2_root_cause"].get("group_top1")
        for e in (evaluate(r) for r in runs)
        if e["T2_root_cause"].get("applicable") and not e["T2_root_cause"].get("heldout")}
    col["is_mock"] = col["lanes"] == ["mock"]
    if col["is_mock"]:     # mock timings are synthetic: no lane rate, no projection
        for k in ("server_prefill_tok_s", "server_decode_tok_s", "projected_verified_s",
                  "answer_tokens_per_call_mean", "answer_tokens_per_call_p95",
                  "answer_share_at_cap"):
            col[k] = None
    col["ranking_model_rank1"] = ranking_question(runs)
    col["ranking_shown_rank1"] = ranking_question(runs, use_shown=True)
    return col


def selection(cols: dict, checks: dict) -> dict:
    """Apply the human-approved model-choice rule. `cols`: name -> column();
    `checks`: name -> {"htp0_all_layers": bool, "gguf_all_q4_0_q8_0": bool} from
    the board-up log and bench.gguf_types (a missing entry is UNVERIFIED = fail).
    Returns per-model gate results, the order, the pick (or None = stop and ask),
    and flags (below floor; a lead that comes from one episode)."""
    mock = [c for c in cols.values() if c.get("is_mock")]
    floor = mock[0]["library_group_top1"] if mock else FLOOR_DEFAULT
    gates, flags = {}, []
    for name, c in cols.items():
        if c.get("is_mock"):
            continue
        ck = checks.get(name, {})
        g = {"broken_json_first_reply": _le(c.get("broken_json_first_reply"), MAX_BROKEN_FIRST),
             "broken_json_after_repair": _le(c.get("broken_json_after_repair"), MAX_BROKEN_AFTER),
             "projected_verified_s": _le(c.get("projected_verified_s"), MAX_PROJECTED_S),
             "htp0_all_layers": ck.get("htp0_all_layers") is True or "UNVERIFIED",
             "gguf_all_q4_0_q8_0": ck.get("gguf_all_q4_0_q8_0") is True or "UNVERIFIED"}
        g["pass"] = all(v is True for v in g.values())
        gates[name] = g
        gt = c.get("library_group_top1")
        if gt is not None and gt < floor:
            flags.append(f"{name}: library group top-1 {gt} is BELOW the deterministic floor "
                         f"{floor} (evidence for the ranking question: the model's order "
                         f"loses to the no-reasoning order)")
    ok = [n for n, g in gates.items() if g["pass"]]
    # lead from one episode: any pair ranked apart by more than the tie band where
    # the higher model is ahead on at most one episode
    for a in gates:
        for b in gates:
            ga, gb = cols[a].get("library_group_top1"), cols[b].get("library_group_top1")
            if a == b or ga is None or gb is None or ga - gb <= GROUP_TIE:
                continue
            ea, eb = cols[a]["group_top1_by_episode"], cols[b]["group_top1_by_episode"]
            ahead = [e for e in ea if ea[e] is not None and eb.get(e) is not None and ea[e] > eb[e]]
            if len(ahead) <= 1:
                flags.append(f"{a} over {b}: lead of {round(ga - gb, 3)} comes from "
                             f"{'one episode ' + ahead[0] if ahead else 'no episode'}")
    pick, order = None, []
    if ok:
        best = max(cols[n]["library_group_top1"] or 0.0 for n in ok)
        tied = [n for n in ok if (cols[n]["library_group_top1"] or 0.0) >= round(best - GROUP_TIE, 3)]
        fbest = max(cols[n].get("model_citation_faithfulness_raw") or 0.0 for n in tied)
        tied2 = [n for n in tied
                 if (cols[n].get("model_citation_faithfulness_raw") or 0.0) >= round(fbest - FAITH_TIE, 3)]
        order = sorted(tied2, key=lambda n: cols[n].get("diag_latency_ms_mean") or float("inf"))
        pick = order[0]
    return {"floor": floor, "gates": gates, "passing": ok,
            "group_tied": tied if ok else [], "faith_tied": tied2 if ok else [],
            "pick": pick, "pick_reason": (
                "mean call time (stand-in for energy, not measured energy)" if len(order) > 1 else
                "faithfulness" if ok and len(tied) > 1 else
                "library group top-1" if ok else "NO MODEL PASSES: stop and ask"),
            "flags": flags}


def _le(v, limit):
    return "MISSING" if v is None else v <= limit


ROWS = ["n_episodes", "library_top1", "library_group_top1", "library_sep_named",
        "library_belief_top1_tiefair", "library_belief_group",
        "Q3_faithfulness_after_gate", "model_citation_faithfulness_raw", "n_model_citations",
        "diag_calls", "broken_json_first_reply", "broken_json_after_repair",
        "ece_group_decision", "ece_group_shown", "confident_wrong_decision",
        "confident_wrong_shown", "confident_wrong_share_decision", "confident_wrong_share_shown",
        "low_conf_share_decision", "low_conf_share_shown", "scored_library_ticks",
        "Q1_macro_f1", "Q4_precision", "Q4_recall", "S4_llm_invocation",
        "prompt_tokens_mean", "prompt_tokens_missing", "answer_tokens_mean",
        "answer_tokens_missing", "answer_tokens_per_call_mean", "answer_tokens_per_call_p95",
        "answer_share_at_cap", "diag_latency_ms_mean", "diag_latency_ms_p95",
        "tick_deadline_misses", "tick_deadline_miss_rate", "server_prefill_tok_s",
        "server_decode_tok_s", "rate_calls_missing", "projected_verified_s"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--json", default="")
    ap.add_argument("--checks", default="",
                    help="JSON: name -> {htp0_all_layers, gguf_all_q4_0_q8_0}, from the "
                         "board-up log and bench.gguf_types")
    args = ap.parse_args()
    cols = [column(p) for p in args.runs]
    names = [Path(p).stem.replace("runs_", "") for p in args.runs]
    checks = json.load(open(args.checks)) if args.checks else {}
    print(f"{'':34s}" + "".join(f"{n[:22]:>24s}" for n in names))
    for k in ROWS:
        print(f"{k:34s}" + "".join(f"{str(c.get(k)):>24s}" for c in cols))
    for n, c in zip(names, cols):
        print(f"\n[{n}] models={c['models']} lanes={c['lanes']} status={c['envelope_status']}")
        for key in ("ranking_model_rank1", "ranking_shown_rank1"):
            r = c[key]
            print(f"  {key}: pooled {r['pooled']}  verdict={r['verdict']}")
            for e, d in r["per_episode"].items():
                print(f"      {e:24s} {d}")
        if c["chip_temp"]:
            print(f"  chip temp [start, end]: {c['chip_temp']}")
        print(f"  group top-1 by episode: {c['group_top1_by_episode']}")
    sel = selection(dict(zip(names, cols)), checks)
    print("\nMODEL-CHOICE RULE (projected time is a PROJECTION; call time is a stand-in for "
          "energy, not measured energy)")
    print(f"  floor (deterministic, mock): {sel['floor']}")
    for n, g in sel["gates"].items():
        print(f"  {n:30s} {g}")
    print(f"  passing: {sel['passing']}  group-tied: {sel['group_tied']}  "
          f"faith-tied: {sel['faith_tied']}")
    print(f"  pick: {sel['pick']}  (decided by {sel['pick_reason']})")
    for f in sel["flags"]:
        print(f"  FLAG: {f}")
    if args.json:
        json.dump({"columns": dict(zip(names, cols)), "selection": sel},
                  open(args.json, "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
