#!/usr/bin/env python3
"""
=============================================================================
 FieldMind Boiler Agent  --  entry point
=============================================================================

  python run_demo.py --episode ep_A01_fcv_seize --verbose
  python run_demo.py --all
  python run_demo.py --all --backend gemini          # needs GOOGLE_API_KEY
  python run_demo.py --all --backend litert          # needs the QIDK attached
  python run_demo.py --all --ablate-text             # the T8 modality check

The backend flag overrides configs/base.yaml. Nothing else about the pipeline
changes between backends -- same prompts, same parsing, same gate, same
metrics. That is the point.
=============================================================================
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from bench.evaluator import aggregate, evaluate          # noqa: E402
from bench.board import chip_temperature                 # noqa: E402
from bench.harness import Episode, run_episode           # noqa: E402


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: str, backend: str | None, overlay: str | None = None) -> dict:
    """The config, with an optional overlay file merged over it key by key
    (e.g. configs/fast.yaml); `backend` overrides llm.backend last."""
    cfg = yaml.safe_load(Path(path).read_text())
    if overlay:
        _deep_merge(cfg, yaml.safe_load(Path(overlay).read_text()))
    if backend:
        cfg["llm"]["backend"] = backend
    return cfg


class LiveTickWriter:
    """--live-ticks: every tick's assessment appended to a JSONL file as it is
    published, so a run that stops part-way keeps its ticks. It is the
    harness's `on_assessment` hook (telemetry only): the tick thread only
    serialises the dict and queues the line; a writer thread does the file
    I/O and flushes every line."""

    def __init__(self, path: str, show: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.show = show                    # --live-print: one line per tick
        self.episode = ""
        self.q: queue.Queue = queue.Queue()
        self.f = self.path.open("a")
        self.t = threading.Thread(target=self._run, daemon=True, name="live-ticks")
        self.t.start()

    def __call__(self, d: dict) -> None:
        self.q.put((json.dumps({"episode": self.episode, **d}),
                    self.line(d) if self.show else None))

    @staticmethod
    def line(d: dict) -> str:
        """One tick for the screen, QUIET ticks too: laptop time, tick, state,
        triage, headline, rank-1 cause, model jobs sent and answers arrived."""
        hyp = d.get("hypotheses") or []
        m = d.get("multi") or {}
        sent = [f"{j['agent']}" for j in m.get("submitted", [])]
        got = [f"{r['agent']}@{r['lane']} {r['finish_s'] - r['start_s']:.1f}s"
               f"{' STALE' if r.get('stale') else ''}"
               for r in m.get("results", [])
               if r.get("start_s") is not None and r.get("finish_s") is not None]
        return (f"{time.strftime('%H:%M:%S')} t{d['tick']:4d} {d['state']:13s} "
                f"{d['triage']:11s} {d['headline'][:48]:48s} | "
                f"{(hyp[0]['cause'][:36] if hyp else '-'):36s}"
                f"{'  sent: ' + ', '.join(sent) if sent else ''}"
                f"{'  got: ' + ', '.join(got) if got else ''}")

    def _run(self) -> None:
        while True:
            item = self.q.get()
            if item is None:
                return
            line, shown = item
            self.f.write(line + "\n")
            self.f.flush()
            if shown is not None:
                print(shown, flush=True)

    def close(self) -> None:
        self.q.put(None)
        self.t.join()
        self.f.close()


def main():
    ap = argparse.ArgumentParser(description="FieldMind boiler agent")
    ap.add_argument("--config", default="configs/base.yaml")
    ap.add_argument("--overlay", default=None,
                    help="a YAML file merged over --config key by key "
                         "(e.g. configs/fast.yaml, the demonstration setup)")
    ap.add_argument("--episode", default=None, help="one episode id")
    ap.add_argument("--all", action="store_true", help="run every episode")
    ap.add_argument("--backend", default=None,
                    choices=["mock", "gemini", "litert", "llamaserver"],
                    help="overrides llm.backend in the config")
    ap.add_argument("--ablate-text", action="store_true",
                    help="remove all notes (T8 modality ablation)")
    ap.add_argument("--log-prompts", action="store_true",
                    help="store the rendered prompt and raw model reply in "
                         "every AgentEnvelope (large output; for debugging)")
    ap.add_argument("--episodes", default=None,
                    help="comma-separated episode ids to run (subset)")
    ap.add_argument("--url", default=None,
                    help="overrides llm.llamaserver.url (e.g. the CPU lane, :8081)")
    ap.add_argument("--lane", default=None,
                    help="overrides llm.llamaserver.lane (npu|cpu label)")
    ap.add_argument("--cooldown-s", type=float, default=0.0,
                    help="board runs: idle gap between episodes (chip cooling)")
    ap.add_argument("--arch", default="single", choices=["single", "multi"],
                    help="single = fieldmind/agent (baseline); multi = "
                         "fieldmind/multi (blackboard agents, scheduler, lanes)")
    ap.add_argument("--mode", default="lockstep", choices=["lockstep", "realtime"],
                    help="lockstep: every job finishes inside its tick. realtime: "
                         "ticks on the wall clock, model calls in the background on "
                         "both lanes, the tick never waits (needs --split --on-change "
                         "or --overlay configs/fast.yaml)")
    ap.add_argument("--tick-s", type=float, default=None,
                    help="real time: wall-clock seconds per tick (default the agent's "
                         "30 s; shorter for tests and quick demonstrations)")
    ap.add_argument("--merge-rule", default=None,
                    choices=["model", "belief_only", "tiebreak", "nudge", "hybrid"],
                    help="overrides multi.merge_rule (--arch multi only)")
    ap.add_argument("--placement", default=None,
                    choices=["fixed", "earliest_finish"],
                    help="overrides multi.placement (--arch multi only)")
    ap.add_argument("--compact", default=None,
                    help="compact prompt sections for this run (--arch "
                         "multi): all | none | comma list of schema,rules,"
                         "cases,notes,world,ver_schema,ver_rules,ver_claims "
                         "(the rest off); overrides multi.compact")
    ap.add_argument("--split", action="store_true",
                    help="Phase 3: water and heat diagnosticians (sets multi.split; "
                         "needs the compact schema, e.g. --compact all)")
    ap.add_argument("--on-change", action="store_true",
                    help="Phase 3: with --split, call a side only when its evidence "
                         "changed (sets multi.split_on_change)")
    ap.add_argument("--case-order", default=None, choices=["score", "shuffled"],
                    help="overrides multi.compact.case_order")
    ap.add_argument("--record-prompts", default=None,
                    help="append every backend call (role, max_tokens, prompt, "
                         "mock-hint hash) to this JSONL file")
    ap.add_argument("--live-ticks", default=None,
                    help="append every tick's assessment to this JSONL file as "
                         "it is published (a run that stops part-way keeps them)")
    ap.add_argument("--live-print", action="store_true",
                    help="with --live-ticks: also print one line per tick, QUIET "
                         "ticks included (--verbose prints only the others)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--out", default="results")
    ap.add_argument("--episodes-dir", default=None,
                    help="overrides paths.episodes (e.g. data/episodes_dev)")
    ap.add_argument("--tag", default="",
                    help="suffix for the results files, so a dev run never "
                         "overwrites a reporting run")
    args = ap.parse_args()

    if args.mode == "realtime" and args.arch != "multi":
        ap.error("--mode realtime is multi-agent only (--arch multi)")
    if args.live_print and not args.live_ticks:
        ap.error("--live-print needs --live-ticks <file>")
    cfg = load_config(args.config, args.backend, args.overlay)
    ls = cfg["llm"].setdefault("llamaserver", {})
    if args.url:
        ls["url"] = args.url
    if args.lane:
        ls["lane"] = args.lane
    cfg.setdefault("agent", {})["log_prompts"] = args.log_prompts
    if args.placement:
        cfg["multi"]["placement"] = args.placement
    if args.merge_rule:
        cfg["multi"]["merge_rule"] = args.merge_rule
    if args.compact is not None:
        from fieldmind.multi.compact import ALL_SECTIONS
        on = (set(ALL_SECTIONS) if args.compact == "all" else set() if
              args.compact == "none" else set(filter(None, args.compact.split(","))))
        if on - set(ALL_SECTIONS):
            ap.error(f"--compact: unknown section(s) {sorted(on - set(ALL_SECTIONS))}")
        cfg["multi"]["compact"].update({s: s in on for s in ALL_SECTIONS})
    if args.split:
        cfg["multi"]["split"] = True
    if args.on_change:
        cfg["multi"]["split_on_change"] = True
    if args.case_order:
        cfg["multi"]["compact"]["case_order"] = args.case_order
    ep_dir = Path(args.episodes_dir or cfg["paths"]["episodes"])
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.episode:
        targets = [ep_dir / args.episode]
    elif args.episodes:
        targets = [ep_dir / e.strip() for e in args.episodes.split(",") if e.strip()]
    elif args.all:
        targets = sorted(p for p in ep_dir.iterdir() if p.is_dir())
    else:
        ap.error("give --episode <id>, --episodes a,b,c, or --all")

    print(f"backend = {cfg['llm']['backend']}"
          f"{'  [TEXT ABLATED]' if args.ablate_text else ''}")
    if cfg["llm"]["backend"] == "mock":
        # Say this every single run. A mock number that leaks into a table is
        # the most likely way this project reports something untrue.
        print("NOTE: mock backend. These numbers measure the deterministic and "
              "retrieval layers only.\n      They are NOT an agent result and "
              "must not be reported as one.")
    print()

    live = LiveTickWriter(args.live_ticks, args.live_print) if args.live_ticks else None
    evals, runs = [], []
    for path in targets:
        if not (path / "ground_truth.json").exists():
            continue
        ep = Episode(path)
        if args.verbose:
            print(f"--- {ep.id}  ({ep.ground_truth['family']}/"
                  f"tier {ep.ground_truth['tier']}) "
                  f"truth: {ep.ground_truth['root_cause_text']}")
        on_board = cfg["llm"]["backend"] in ("llamaserver", "litert")
        if on_board and runs and args.cooldown_s > 0:
            time.sleep(args.cooldown_s)            # chip cooling between episodes
        temp0 = chip_temperature() if on_board else None
        if live is not None:
            live.episode = ep.id
        try:
            run = run_episode(ep, cfg, ablate_text=args.ablate_text,
                              verbose=args.verbose, arch=args.arch,
                              record_prompts=args.record_prompts,
                              mode=args.mode, tick_s=args.tick_s,
                              on_assessment=live)
        except BaseException:
            if live is not None:
                live.close()                   # Ctrl-C or a crash: keep the ticks so far
            raise
        if on_board:
            run["chip_temp_start"] = temp0
            run["chip_temp_end"] = chip_temperature()
            run["cooldown_s_before"] = args.cooldown_s if len(runs) else 0.0
            print(f"  chip temp start {temp0}  end {run['chip_temp_end']}")
        ev = evaluate(run)
        runs.append(run)
        evals.append(ev)

        t2 = ev["T2_root_cause"]
        t6 = ev["T6_lead_time"]
        print(f"{ep.id:30s} F1={ev['T1_state']['macro_f1']:.2f} "
              f"top1={t2.get('top1', '-'):>5} top3={t2.get('top3', '-'):>5} "
              f"faith={ev['T3_faithfulness']['faithfulness']:.2f} "
              f"llm={ev['S4_llm_invocation_rate']:.2f} "
              f"lead={t6.get('lead_time_min', '-')} "
              f"p95={ev['S1_tick_latency_ms_p95']:.0f}ms")

    if live is not None:
        live.close()
    summary = aggregate(evals)
    tag = (f"{cfg['llm']['backend']}{'_ablated' if args.ablate_text else ''}"
           f"{'_' + args.tag if args.tag else ''}")
    (out_dir / f"summary_{tag}.json").write_text(json.dumps(
        {"config_backend": cfg["llm"]["backend"], "summary": summary,
         "per_episode": evals}, indent=2))
    (out_dir / f"runs_{tag}.json").write_text(json.dumps(runs, indent=2))

    print("\n" + "=" * 66)
    print("AGGREGATE")
    print("=" * 66)
    for k, v in summary.items():
        if k == "constraints":
            continue
        print(f"  {k:34s} {v}")
    print("  constraints:")
    for k, v in summary["constraints"].items():
        print(f"    {k:32s} {'PASS' if v else 'FAIL'}")
    print(f"\nwritten to {out_dir}/summary_{tag}.json")


if __name__ == "__main__":
    main()
