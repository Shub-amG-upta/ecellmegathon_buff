"""
Unit tests for the orchestrator's two structural bugs (CLAUDE.md known bugs
1 and 2). Mock backend cannot trigger either -- it never fails and its
hypotheses ARE the deterministic ranking -- so these inject the conditions
directly.

    python bench/test_orchestrator.py

  bug 1  DEGRADED latch:  one non-ok LLM reply set wm.degraded_mode and nothing
         ever cleared it; derive_state then returned DEGRADED for the rest of
         the episode. trusted_tags had the same latch via VALIDITY.
  bug 2  _merge discarded the belief accumulator: out = dict(model) replaced
         the deterministic hypothesis list wholesale, throwing away the
         cross-tick log-odds confidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml                                                       # noqa: E402

from fieldmind.agent.l0_ingest import SensorWindow                # noqa: E402
from fieldmind.agent.orchestrator import Orchestrator             # noqa: E402
from fieldmind.agent.world_model import new_world_model, derive_state  # noqa: E402
from fieldmind.schemas import Fact, TAGS                          # noqa: E402
from bench.harness import Episode, build_agent                    # noqa: E402

ROOT = Path(__file__).parent.parent
CFG = yaml.safe_load((ROOT / "configs/base.yaml").read_text())
PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name} {extra}")


def _agent_and_window(ep_name="ep_N01_normal", warm_min=12.0):
    """Build a real agent and a window warmed with the episode's first samples."""
    ep = Episode(ROOT / "data/episodes" / ep_name)
    orch, asset, *_ = build_agent(CFG, ep.notes, ep.records)
    wm = new_world_model(ep.id, asset.equipment)
    w = SensorWindow(window_min=60.0,
                     sample_period_s=CFG["checks"]["sample_period_s"])
    for r in ep.rows:
        if r["t"] > warm_min * 60:
            break
        w.append(r["t"], r)
    return ep, orch, wm, w


# =====================================================================
print("bug 1a -- a stale degraded marker is cleared at the top of tick()")
# =====================================================================
ep, orch, wm, w = _agent_and_window()
# Simulate the latch the old code left behind: an earlier tick's LLM timeout.
wm.degraded_mode = "llm_timeout"
wm.trusted_tags = set(TAGS)
asmt = orch.tick(wm, w, tick_no=50, timestamp="2026-08-21T06:00:00", now_s=w._t[-1])
check("a healthy tick does NOT report DEGRADED just because a prior tick's "
      "LLM call failed",
      asmt.state != "DEGRADED", f"(state={asmt.state})")
check("the stale 'llm_timeout' marker is gone after the tick",
      wm.degraded_mode in (None, "") or wm.degraded_mode.startswith("rung"),
      f"(degraded_mode={wm.degraded_mode!r})")

# and prove the marker WAS the thing that used to force DEGRADED:
wm2 = new_world_model("x", {})
wm2.trusted_tags = set(TAGS)
wm2.degraded_mode = "llm_timeout"
check("derive_state still maps a set degraded_mode to DEGRADED "
      "(so the tick()-level reset is what fixes the latch)",
      derive_state([], "QUIET", wm2) == "DEGRADED")


# =====================================================================
print("\nbug 1b -- trusted_tags is recomputed per tick, not latched")
# =====================================================================
ep, orch, wm, w = _agent_and_window()
real_run = orch.checks.run
inject = {"on": True}


def run_with_injected_validity(window, tick, trusted, blowdown_tph=1.0):
    facts = real_run(window, tick, trusted, blowdown_tph)
    if inject["on"]:
        facts.append(Fact(id="FZ", check="VALIDITY", tags=["ms_temperature"],
                          window=(tick, tick), value=0.0,
                          detail="ms_temperature stuck (injected)",
                          severity="ALARM"))
    return facts


# the latch mechanism: a tag missing from trusted_tags, with NOTHING firing,
# is what derive_state reads as DEGRADED
wm_x = new_world_model("x", {})
wm_x.trusted_tags = set(TAGS) - {"ms_temperature"}
check("derive_state maps a missing trusted tag to DEGRADED (the latch "
      "mechanism the per-tick recompute defuses)",
      derive_state([], "QUIET", wm_x) == "DEGRADED")

orch.checks.run = run_with_injected_validity
a1 = orch.tick(wm, w, 60, "t", now_s=w._t[-1])
check("the tick with a VALIDITY fact drops the tag from trusted",
      "ms_temperature" not in wm.trusted_tags)
inject["on"] = False                       # instrument fault clears
a2 = orch.tick(wm, w, 61, "t", now_s=w._t[-1])
check("once the VALIDITY fact stops firing the tag is trusted again "
      "(no latch)",
      wm.trusted_tags == set(TAGS), f"(trusted={sorted(wm.trusted_tags)})")
check("and the state is not stuck DEGRADED after the instrument recovers",
      a2.state != "DEGRADED", f"(state={a2.state})")
orch.checks.run = real_run


# =====================================================================
print("\nbug 1 -- FULL-EPISODE recovery: one injected fault must not change "
      "any OTHER tick's state")
# =====================================================================
from fieldmind.agent.l4_diagnose import Diagnostician                 # noqa: E402
from fieldmind.agent.l1_checks import CheckLayer                      # noqa: E402
from datetime import datetime, timedelta                             # noqa: E402

EP_NAME = "ep_A05_fcv_caught"          # develops a deviation, then recovers
_EPOCH = datetime(2026, 8, 21, 6, 0, 0)


def replay_states(inject_tick=None, kind=None):
    """Replay EP_NAME through a fresh real agent; optionally inject a one-tick
    fault. Returns the list of per-tick state strings."""
    ep = Episode(ROOT / "data/episodes" / EP_NAME)
    orch, asset, *_ = build_agent(CFG, ep.notes, ep.records)
    wm = new_world_model(ep.id, asset.equipment)
    tick_s = CFG["agent"]["tick_period_s"]
    win = SensorWindow(window_min=60.0,
                       sample_period_s=CFG["checks"]["sample_period_s"])

    real_diag = orch.diag.run
    real_checks = orch.checks.run

    def diag_maybe_timeout(tick, *a, **k):
        env = real_diag(tick, *a, **k)
        if kind == "llm" and tick == inject_tick:
            env.status = "timeout"
            env.payload = {"error": "injected"}
            env.cited_facts = []
        return env

    def checks_maybe_validity(window, tick, trusted, blowdown_tph=1.0):
        facts = real_checks(window, tick, trusted, blowdown_tph)
        if kind == "validity" and tick == inject_tick:
            facts.append(Fact(id="FZ", check="VALIDITY", tags=["drum_pressure"],
                              window=(tick, tick), value=0.0,
                              detail="drum_pressure stuck (injected one tick)",
                              severity="ALARM"))
        return facts

    orch.diag.run = diag_maybe_timeout
    orch.checks.run = checks_maybe_validity

    states = []
    n = int(ep.duration_s // tick_s)
    for kk in range(n):
        for row in ep.samples_between(kk * tick_s, (kk + 1) * tick_s):
            win.append(row["t"], row)
        if not win.ready(5.0):
            continue
        ts = (_EPOCH + timedelta(seconds=(kk + 1) * tick_s)).isoformat()
        states.append((kk, orch.tick(wm, win, kk, ts, now_s=(kk + 1) * tick_s).state))
    return states, wm


base_states, _ = replay_states()
base_map = dict(base_states)
# Inject at the FIRST DEVIATION tick: the diagnostician is running there, and
# there are DEVIATION and (post-recovery) NORMAL ticks AFTER it -- those are
# the ticks the latch used to swallow. ALARM ticks mask DEGRADED in
# derive_state, so an injection point followed only by ALARM would not test
# anything.
dev_ticks = [k for k, s in base_states if s == "DEVIATION"]
assert dev_ticks, "ep has no DEVIATION tick -- pick another episode"
inj = dev_ticks[0]
after = [(k, s) for k, s in base_states if k > inj]
recoverable_after = [(k, s) for k, s in after if s in ("NORMAL", "DEVIATION")]
assert recoverable_after, "no NORMAL/DEVIATION tick after injection -- weak test"

llm_states, llm_wm = replay_states(inject_tick=inj, kind="llm")
val_states, val_wm = replay_states(inject_tick=inj, kind="validity")

llm_diff = [(k, base_map[k], s) for k, s in llm_states if base_map.get(k) != s]
# the VALIDITY injection legitimately changes its OWN tick (VALIDITY fact is
# severity ALARM), so allow a difference at exactly the injection tick.
val_diff = [(k, base_map[k], s) for k, s in val_states
            if base_map.get(k) != s and k != inj]

check(f"one injected LLM timeout at tick {inj} (DEVIATION) changes NO other "
      f"tick's state -- {len(recoverable_after)} NORMAL/DEVIATION ticks follow "
      f"it that the latch would flip to DEGRADED",
      llm_diff == [], f"diffs: {llm_diff[:8]}")
check("...and wm.degraded_mode is clear at end of episode",
      llm_wm.degraded_mode is None, f"({llm_wm.degraded_mode!r})")
check(f"one transient VALIDITY fact at tick {inj} changes only that tick",
      val_diff == [], f"diffs: {val_diff[:8]}")
check("...and drum_pressure is back in trusted_tags at end of episode",
      "drum_pressure" in val_wm.trusted_tags,
      f"({sorted(val_wm.trusted_tags)})")
check("...and no post-injection NORMAL/DEVIATION tick reads DEGRADED",
      not any(s == "DEGRADED" for k, s in val_states if k > inj),
      f"{[(k, s) for k, s in val_states if k > inj and s == 'DEGRADED'][:8]}")


# =====================================================================
print("\nbug 2 -- _merge keeps the deterministic (accumulated) confidence and "
      "does not drop carried hypotheses")
# =====================================================================
det = {
    "headline": "det headline",
    "unexplained": ["u-det"],
    "hypotheses": [
        {"rank": 1, "cause": "A", "confidence": 0.90, "supports": ["F1", "F2"],
         "case_ref": "RCA-01", "discriminator": "dA"},
        {"rank": 2, "cause": "B", "confidence": 0.40, "supports": ["F3"],
         "case_ref": "RCA-16", "discriminator": "dB"},
    ],
}
model = {
    "headline": "model headline",
    "unexplained": ["u-model"],
    "hypotheses": [
        # model reorders: puts B first, rewords, cites a different current fact
        {"cause": "B", "confidence": 0.75, "supports": ["F9"],
         "case_ref": "RCA-16", "discriminator": "model-dB"},
        # model raises a brand-new idea with a big confidence
        {"cause": "Z", "confidence": 0.95, "supports": ["F4"], "case_ref": None},
    ],
}
out = Orchestrator._merge(det, model)
causes = [h["cause"] for h in out["hypotheses"]]
by_cause = {h["cause"]: h for h in out["hypotheses"]}

check("model ranking wins: B is now first", causes[0] == "B", f"({causes})")
check("deterministic confidence wins: B keeps 0.40, not the model's 0.75",
      by_cause["B"]["confidence"] == 0.40,
      f"({by_cause['B']['confidence']})")
check("model's current-tick citation wins: B.supports == ['F9']",
      by_cause["B"]["supports"] == ["F9"])
check("a hypothesis the model dropped (A) is NOT lost -- the accumulator "
      "carries it",
      "A" in causes and by_cause["A"]["confidence"] == 0.90)
check("a carried hypothesis cites no stale fact ids",
      by_cause["A"]["supports"] == [])
check("a model-only idea (Z) is capped at 0.5, not its claimed 0.95",
      by_cause["Z"]["confidence"] == 0.5,
      f"({by_cause['Z']['confidence']})")
check("ranks are renumbered 1..n", [h["rank"] for h in out["hypotheses"]]
      == list(range(1, len(out["hypotheses"]) + 1)))
check("unexplained lists are concatenated (model first)",
      out["unexplained"] == ["u-model", "u-det"])

# --- the old bug, made explicit: dict(model) would have used 0.75 and 0.95
#     and dropped A entirely ---
check("regression guard: B's confidence is the accumulator's, so it is NOT "
      "the model value",
      by_cause["B"]["confidence"] != 0.75)

# --- empty model reply -> deterministic list survives intact ---
out2 = Orchestrator._merge(det, {"hypotheses": []})
check("an empty model reply leaves the deterministic hypotheses in place",
      [h["cause"] for h in out2["hypotheses"]] == ["A", "B"]
      and out2["hypotheses"][0]["confidence"] == 0.90)


print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
