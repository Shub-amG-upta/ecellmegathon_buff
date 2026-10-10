#!/usr/bin/env python3
"""
Known-wrong-answer test of the verifier (multi-agent Phase 3, PROMPTS.md
session 5: "feed the verifier one episode where rank 1 is known to be wrong and
show whether it disagrees").

    python bench/verifier_wrong_answer.py --episodes dev_A01_fcv_seize,... \
        [--backend mock|llamaserver] [--split] [--out results/x/verifier_wrong.json]

For each episode it runs the multi-agent system twice, verifier forced on
every model tick (agent.verifier: always):
  forced   the diagnostician's answer is rewritten on its way back from the
           model so that rank 1 is a case SHOWN in the prompt but OUTSIDE the
           episode's true look-alike group (a known-wrong answer); the
           citations of the model's own rank 1 are kept;
  control  the same run without the rewrite.
It reports, per run, how often the verifier failed the rank-1 claim
(detection on `forced`, false alarm on `control`), passed it, or left it not
judged, and how often the known-wrong case was still published at rank 1.

The rewrite sits between the model and the agent (harness wrap_backend); the
agent, the gate and the verifier code are unchanged. On the mock backend the
verifier always agrees, so detection there is 0 by construction: a mock run
proves the plumbing, never the verifier. The measurement is for the board.
Host-side tool: bench/ only.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from bench.evaluator import _groups  # noqa: E402
from bench.harness import Episode, run_episode  # noqa: E402
from fieldmind.agent.l4_diagnose import extract_json  # noqa: E402
from fieldmind.multi.compact import ALL_SECTIONS, check_diag_answer_shape  # noqa: E402


def true_group(gt: dict) -> set[str] | None:
    """The case ids of the episode's true look-alike group (None: no target)."""
    target = gt.get("root_cause_id")
    if target in (None, "NONE"):
        return None
    gmap, held, held_ids = _groups()
    grp = held.get(target) if target in held_ids else gmap.get(target, target)
    return set(str(grp).split("+")) | {target} if grp else {target}


class WrongRankOne:
    """Backend wrapper: rewrites a diagnostician answer so rank 1 is a shown
    case outside `truth`. Every rewrite (or why none was possible) is logged
    with the case id."""

    def __init__(self, inner, truth: set[str]):
        self.inner = inner
        self.name = getattr(inner, "name", "wrong-rank-one")
        self.truth = truth
        self.log: list[dict] = []

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        reply = self.inner.generate(prompt, role=role, max_tokens=max_tokens,
                                    mock_hint=mock_hint)
        if role != "diagnostician" or reply.status != "ok":
            return reply
        hint = mock_hint or {}
        payload = extract_json(reply.text)
        if not isinstance(payload, dict):
            self.log.append({"forced": None, "why": "unparsable answer"})
            return reply
        new = (self._compact(payload, hint) if hint.get("compact")
               else self._full(payload, hint))
        if new is None:
            return reply
        return dataclasses.replace(reply, text=json.dumps(new))

    def _wrong(self, ids):
        return next((c for c in ids if c and c not in self.truth), None)

    def _compact(self, p, hint):
        lines = hint.get("case_lines", [])
        # Only a well-formed answer with every case line in range is rewritten
        # (the gate's own shape check); anything else goes through untouched,
        # so the forced run keeps the model's own parse and citation failures.
        ok, _ = check_diag_answer_shape(p)
        if not ok or any(not 1 <= e[0] <= len(lines) for e in p["r"]):
            self.log.append({"forced": None, "why": "malformed answer, not rewritten"})
            return None
        wrong = self._wrong(lines)
        if wrong is None:
            self.log.append({"forced": None, "why": "no shown case outside the true group"})
            return None
        line = lines.index(wrong) + 1
        cites = p["r"][0][1] if p["r"] else []
        p = copy.deepcopy(p)
        p["r"] = [[line, list(cites)]] + [e for e in p["r"] if e and e[0] != line][:2]
        self.log.append({"forced": wrong})
        return p

    def _full(self, p, hint):
        cases = hint.get("cases", [])
        hyps = p.get("hypotheses")
        if not (isinstance(hyps, list) and all(isinstance(h, dict) for h in hyps)):
            self.log.append({"forced": None, "why": "malformed answer, not rewritten"})
            return None
        wrong = self._wrong([c.get("case_id") for c in cases])
        if wrong is None:
            self.log.append({"forced": None, "why": "no shown case outside the true group"})
            return None
        c = next(c for c in cases if c.get("case_id") == wrong)
        h = {"rank": 1, "cause": c.get("root_cause", "unknown"),
             "confidence": hyps[0].get("confidence", 0.5) if hyps else 0.5,
             "supports": list(hyps[0].get("supports", [])) if hyps else [],
             "case_ref": wrong, "discriminator": c.get("discriminating_evidence", "")}
        p = copy.deepcopy(p)
        p["hypotheses"] = [h] + [x for x in hyps if x.get("case_ref") != wrong][:2]
        self.log.append({"forced": wrong})
        return p


def rank1_verdicts(run: dict, onset_s: float | None = None, tick_s: float = 30.0) -> dict:
    """Per tick where the verifier answered: its verdict on the claim published
    at rank 1 (the diagnosis' rank 1: nothing after the verifier reorders it).
    Counted for all such ticks and, separately, for ticks after the fault onset
    (the window the evaluator's T2 scores). A verifier call that failed is
    counted as `verifier_call_failed`, never as a verdict."""
    def blank():
        return {"verifier_ran": 0, "failed": 0, "passed": 0, "not_judged": 0}
    out = dict(blank(), verifier_call_failed=0, post_onset=blank(), published_rank1=[])
    for a in run["assessments"]:
        envs = [e for e in a.get("envelopes", []) if e.get("agent") == "verifier"]
        if not envs or not a.get("hypotheses"):
            continue
        if envs[-1].get("status") != "ok":
            out["verifier_call_failed"] += 1
            continue
        rank1 = a["hypotheses"][0]
        out["published_rank1"].append(rank1.get("case_ref"))
        checks = {c.get("claim"): c.get("verdict") for c in envs[-1]["payload"].get("checks", [])}
        v = checks.get(rank1.get("cause"))
        key = "failed" if v == "fail" else "passed" if v == "pass" else "not_judged"
        rows = [out]
        if onset_s is not None and a["tick"] * tick_s >= onset_s:   # bench/evaluator.py T2
            rows.append(out["post_onset"])
        for r in rows:
            r["verifier_ran"] += 1
            r[key] += 1
    return out


def run_one(ep: Episode, cfg: dict, force: bool) -> dict:
    truth = true_group(ep.ground_truth)
    holder = {}

    def wrap(b):
        holder["w"] = WrongRankOne(b, truth or set())
        return holder["w"]

    run = run_episode(ep, cfg, arch="multi", wrap_backend=wrap if force else None)
    onset = ep.ground_truth.get("fault_onset_t")      # the key T2 reads
    v = rank1_verdicts(run, onset, cfg["agent"]["tick_period_s"])
    log = holder["w"].log if "w" in holder else []     # what the wrapper really did
    v["forced_answers"] = sum(1 for x in log if x["forced"])       # calls (two per tick with split)
    v["not_rewritten"] = {w: sum(1 for x in log if x.get("why") == w)
                          for w in {x["why"] for x in log if not x["forced"]}}
    v["forced_cases"] = sorted({x["forced"] for x in log if x["forced"]})
    # a real case outside the true group at rank 1 (a "model idea" without a case is not counted)
    v["rank1_outside_true_group"] = sum(1 for c in v.pop("published_rank1")
                                        if c and truth is not None and c not in truth)
    ran = v["verifier_ran"]
    v["fail_rate"] = round(v["failed"] / ran, 3) if ran else None
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", required=True)
    ap.add_argument("--episodes-dir", default="data/episodes_dev")
    ap.add_argument("--backend", default="mock")
    ap.add_argument("--config", default="configs/base.yaml")
    ap.add_argument("--split", action="store_true")
    ap.add_argument("--overlay", default=None,
                    help="a config overlay merged over --config (e.g. configs/fast.yaml: "
                         "two models, both diagnosticians on the NPU)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    if args.overlay:
        from run_demo import _deep_merge
        _deep_merge(cfg, yaml.safe_load(open(args.overlay)))
    cfg["llm"]["backend"] = args.backend
    cfg["agent"]["verifier"] = "always"                 # every model tick is checked
    cfg["multi"]["compact"].update({s: True for s in ALL_SECTIONS})
    cfg["multi"]["split"] = args.split or bool(cfg["multi"].get("split"))
    # every diagnosis must be a fresh call to be rewritten: a cached side answer
    # (split_on_change) would be an earlier rewritten one, re-used
    cfg["multi"]["split_on_change"] = False
    rows = {}
    for eid in filter(None, args.episodes.split(",")):
        ep = Episode(Path(args.episodes_dir) / eid)
        if true_group(ep.ground_truth) is None:
            rows[eid] = {"skipped": "no true case"}
            continue
        rows[eid] = {"forced": run_one(ep, copy.deepcopy(cfg), True),
                     "control": run_one(ep, copy.deepcopy(cfg), False)}
        f, c = rows[eid]["forced"], rows[eid]["control"]
        print(f"{eid}: forced answers {f['forced_answers']} (not rewritten {f['not_rewritten']}), "
              f"verifier answered {f['verifier_ran']} ticks (call failed {f['verifier_call_failed']}): "
              f"detected {f['failed']} ({f['fail_rate']}), passed {f['passed']}, not judged "
              f"{f['not_judged']}; post-onset {f['post_onset']}; rank 1 outside the true group "
              f"{f['rank1_outside_true_group']} | control false alarms {c['failed']} of "
              f"{c['verifier_ran']}")
    if args.backend == "mock":
        print("NOTE: mock backend -- its verifier always agrees; detection 0 here proves "
              "the plumbing only, not the verifier.")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"backend": args.backend, "split": args.split,
                                              "episodes": rows}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
