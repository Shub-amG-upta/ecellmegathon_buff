"""
Gate and memory agent (code, every tick): L6 + L7.

The ONLY agent that puts a model answer on the board. For every Result it
  1. drops it if stale (older than one tick -- Phase 1 stub; the "unless that
     side's evidence is unchanged" clause needs sides, Phase 3),
  2. checks its cited fact IDs against the facts of the answer's EVIDENCE
     tick (not the current tick), then folds it in with the single agent's
     own merge / verifier-apply,
and then approves actions, decides escalation, stamps the display-only shown
confidence, publishes the assessment and appends this tick's events.

Writes: status, diagnosis, verdict, events, assessment.

It also publishes a DETERMINISTIC assessment at the end of the hard path
(P0), before any model job: the same approve() on belief's claims. That is
the plan's rule 1 ("the tick never waits for a model"); in Phase 1 it is
timed and recorded as telemetry, and the assessment that is compared and
scored is still the final one, so decisions match the single agent.
"""

from __future__ import annotations

import copy
import dataclasses
import time
from types import SimpleNamespace

from ...agent.l1_symbolize import headline
from ...agent.orchestrator import (fold_diagnosis, initial_claims, merge,
                                   quiet_deadline_miss, rung_marker,
                                   stamp_shown_confidence, tick_deadline_miss)
from ...schemas import AgentEnvelope, Assessment
from .. import compact, merge_rules, sides
from .diagnostician import DiagnosticianAgent

NAME = "gate"


class GateMemoryAgent:
    def __init__(self, gate, ver, cfg: dict):
        self.gate = gate
        self.ver = ver
        self.cfg = cfg
        self.hard_deadline_ms = cfg.get("hard_stage_deadline_ms", 200)
        self.tick_budget_ms = cfg.get("tick_period_s", 30) * 1000
        self.stale_dropped: list[dict] = []
        self.raw_notes = False      # note_source raw: set by the orchestrator
        self.flat_ids: frozenset = frozenset()    # hybrid: set by the orchestrator
        # Compact-answer expansion records of the current tick (telemetry;
        # the orchestrator takes and clears them when it records the tick).
        self.compact_log: list[dict] = []
        # Run-level compact-verifier counts (telemetry): verdicts with an
        # unjudged claim, and prompts flagged over the limit.
        self.ver_incomplete = 0
        self.accepted_late = 0      # answers > 1 tick old accepted on unchanged evidence
        self.ver_over_limit = 0
        # accuracy-fix decision 1: how a checked answer reaches the published
        # ranking (fieldmind/multi/merge_rules.py). "model" = the single
        # agent's merge (Phases 1-4). Set by the orchestrator.
        self.merge_rule = "model"
        self._tick_payload = None       # all accepted side answers, this tick
        self._fresh_payload = None      # the ones answered THIS tick (not reused)

    # ------------------------------------------------------------------
    def begin_tick(self, bb, rung: int) -> None:
        """Only the degradation ladder persists across ticks (known bug 1)."""
        self._tick_payload = self._fresh_payload = None
        bb.write("status", {"degraded_mode": rung_marker(rung)}, NAME)

    def open_assessment(self, bb, tick: int, timestamp: str, state: str,
                        facts: list, level: str) -> Assessment:
        asmt = Assessment(tick=tick, timestamp=timestamp, state=state,
                          headline=headline(facts),
                          facts=[f.to_dict() for f in facts],
                          triage=level,
                          degraded_mode=bb.read("status")["degraded_mode"])
        bb.write("assessment", asmt, NAME)
        return asmt

    def _log_events(self, bb, tick: int, outboxes: tuple) -> None:
        log = bb.mutable("events", NAME)
        for box in outboxes:
            t, events = bb.read(box)
            if t == tick:
                log.extend(events)

    def publish_quiet(self, bb, asmt: Assessment, tick: int, state: str,
                      check_ms: float, t_tick: float) -> None:
        asmt.actions = []
        asmt.tick_latency_ms = (time.perf_counter() - t_tick) * 1000
        asmt.deadline_miss = quiet_deadline_miss(check_ms, self.hard_deadline_ms)
        self._log_events(bb, tick, ("outbox.sensor",))
        bb.write("status", {"tick": tick, "state": state}, NAME)

    # ------------------------------------------------------------------
    def deterministic(self, bb, asmt: Assessment, ranked, ranking, facts,
                      retrieved, state: str) -> tuple[dict, dict]:
        """Belief's claims into the diagnosis slot, and the P0 deterministic
        assessment (telemetry)."""
        asmt.belief_ranking = ranking
        claims = initial_claims(asmt.headline, ranked)
        bb.write("diagnosis", claims, NAME)
        actions, escalate, _ = self.gate.approve(claims["hypotheses"], facts,
                                                 retrieved, state)
        p0 = {"rank1": claims["hypotheses"][0]["case_ref"] if claims["hypotheses"] else None,
              "actions": [a.id for a in actions], "escalate": escalate}
        return claims, p0

    # ------------------------------------------------------------------
    def is_stale(self, result, now_tick: int) -> bool:
        return now_tick - result.evidence_tick > 1

    def _drop_if_stale(self, result, now_tick: int) -> bool:
        if self.is_stale(result, now_tick):
            result.stale = True
            self.stale_dropped.append({"job_id": result.job.job_id,
                                       "agent": result.job.agent,
                                       "evidence_tick": result.evidence_tick,
                                       "now_tick": now_tick})
            return True
        return False

    def fold_diagnosis(self, bb, asmt: Assessment, claims: dict, result,
                       now_tick: int) -> dict:
        lm = getattr(result.job, "line_map", None)
        if lm and lm.get("kind") == "diagnosis":
            # Compact answer (B'): expanded through the line map the prompt
            # was built with, BEFORE anything else reads it.
            result.envelope = self._expand(result.envelope, lm)
        env = result.envelope
        asmt.envelopes.append(env.to_dict())
        if self._drop_if_stale(result, now_tick):
            return claims
        evidence_facts = bb.read("facts").facts_of(result.evidence_tick)
        if result.evidence_tick != now_tick:
            # Fact ids are tick-local in the prompt. An answer from an earlier
            # tick is checked, merged and published with STAMPED ids
            # (t84.F3), so L6 can never take it for the current tick's F3.
            env, evidence_facts = self._stamped(env, evidence_facts,
                                                result.evidence_tick)
        claims, degraded = fold_diagnosis(claims, env, evidence_facts,
                                          self.gate, self.cfg)
        bb.write("diagnosis", claims, NAME)
        if degraded is not None:
            bb.write("status", {"degraded_mode": degraded}, NAME)
            asmt.degraded_mode = degraded
        return claims

    def fold_sides(self, bb, asmt: Assessment, claims: dict, results,
                   now_tick: int, reuse: bool = False) -> dict:
        """Phase 3: the water and heat answers of one tick. Each side's answer is
        expanded, checked for staleness, for a failed call and for invented
        citations ON ITS OWN (one bad side does not sink the other); the
        accepted ones are combined (decision 1: the side with the more severe
        evidence first, a case named by both once) and folded by the single
        agent's `merge`, unchanged, once. The citation rule is NOT applied a
        second time to the union: a fact both sides cite counts once there
        while each side's invented ids still count, so two answers that each
        passed could fail together (phase review, commit 2).

        `reuse` (multi.split_on_change, commit 3): an accepted answer is kept in
        `side_answers` with the fingerprint of the evidence it answered, its
        fact ids stamped with its evidence tick; a side to run that made no call
        this tick takes its cached answer when the fingerprint still matches;
        a side that is no longer to run, or whose new answer failed, loses it."""
        accepted, degraded = [], None
        for r in results:
            lm = r.job.line_map
            r.envelope = self._expand(r.envelope, lm)
            env = r.envelope
            asmt.envelopes.append(env.to_dict())
            # Plan p.10: an answer is accepted if it is at most one tick old, OR
            # if that side's evidence has not changed since (real time; with
            # `reuse`, i.e. split_on_change). Its citations are checked against
            # the facts of ITS evidence tick, which the board must still hold.
            late = self.is_stale(r, now_tick)
            if late and not (reuse and self._unchanged_since(bb, r)):
                self._drop_if_stale(r, now_tick)
                continue
            if late:
                self.accepted_late += 1
            try:
                ev = bb.read("facts").facts_of(r.evidence_tick)
            except KeyError:                       # older than the fact history
                r.stale = True
                self.stale_dropped.append({"job_id": r.job.job_id, "agent": r.job.agent,
                                           "evidence_tick": r.evidence_tick,
                                           "now_tick": now_tick, "why": "facts no longer held"})
                continue
            if r.evidence_tick != now_tick:
                env, ev = self._stamped(env, ev, r.evidence_tick)
            if env.status != "ok":
                degraded = f"llm_{env.status}"
                continue
            cit = self.gate.check_citations(env.cited_facts, ev)
            if cit["faithfulness"] < self.cfg.get("min_faithfulness", 0.5):
                # the single agent's rule, applied per side
                claims["unexplained"].append(
                    f"{lm['side']} diagnostician cited non-existent facts "
                    f"{cit['invalid']}; its answer was not used")
                continue
            accepted.append((lm["side"], env, ev))
        reused = []
        if reuse:
            accepted, reused = self._reuse(bb, accepted, results, now_tick)
        if accepted:
            primary = sides.primary_side(bb.read("facts").facts_of(now_tick),
                                         bb.read("findings"))
            accepted.sort(key=lambda a: a[0] != primary)
            payload = compact.combine_side_payloads([e.payload for _, e, _ in accepted])
            if self.merge_rule == "model":
                claims = merge(claims, payload)
            else:                           # applied in apply_rule, every tick
                self._tick_payload = payload
                self._fresh_payload = compact.combine_side_payloads(
                    [e.payload for s, e, _ in accepted if s not in reused])
            self.compact_log.append({"agent": "merge", "now_tick": now_tick,
                                     "evidence_ticks": [e.tick for _, e, _ in accepted],
                                     "order": [a[0] for a in accepted],
                                     "reused": reused,
                                     "reused_shown": dict(getattr(self, "_reused_shown", {}))
                                     if reused else {}})
        bb.write("diagnosis", claims, NAME)
        if degraded is not None:
            bb.write("status", {"degraded_mode": degraded}, NAME)
            asmt.degraded_mode = degraded
        return claims

    def apply_rule(self, bb, claims: dict, now_tick: int) -> dict:
        """Accuracy-fix decision 1: publish belief's order, changed by the
        model only as the rule allows. Runs on EVERY non-QUIET tick (a nudge
        persists on ticks with no answer). Logs whether the published top 3
        differs from belief's and why."""
        live = [h for h in bb.read("belief") if not h.retired]
        model = (self._tick_payload or {}).get("hypotheses", [])
        if self.merge_rule == "belief_only":
            hyps, info = merge_rules.belief_only(live)
        elif self.merge_rule == "tiebreak":
            hyps, info = merge_rules.tiebreak(live, model)
        elif self.merge_rule == "nudge":
            offsets = dict(bb.read("model_evidence"))
            fresh = (self._fresh_payload or {}).get("hypotheses", [])
            if fresh:
                offsets = merge_rules.add_nudges(offsets, fresh)
                bb.write("model_evidence", offsets, NAME)
            hyps, info = merge_rules.nudge(live, offsets)
        elif self.merge_rule == "hybrid":
            offsets = dict(bb.read("model_evidence"))
            fresh = [m for m in (self._fresh_payload or {}).get("hypotheses", [])
                     if m.get("case_ref") not in self.flat_ids]
            if fresh:
                offsets = merge_rules.add_nudges(offsets, fresh)
                bb.write("model_evidence", offsets, NAME)
            hyps, info = merge_rules.hybrid(live, model, offsets, self.flat_ids)
        else:
            raise ValueError(f"unknown merge_rule {self.merge_rule!r}")
        claims = dict(claims, hypotheses=hyps,
                      unexplained=list((self._tick_payload or {}).get("unexplained", []))
                      + list(claims.get("unexplained", [])))
        self.compact_log.append({"agent": "rule", "now_tick": now_tick,
                                 "model_ranked": [m.get("case_ref") for m in model], **info})
        bb.write("diagnosis", claims, NAME)
        return claims

    def _unchanged_since(self, bb, result) -> bool:
        """The side's evidence now equals the evidence its job was built on."""
        fp = (result.job.line_map or {}).get("fingerprint")
        return fp is not None and fp == DiagnosticianAgent.fingerprint(
            bb, result.job.line_map["side"], self.raw_notes)

    def _reuse(self, bb, accepted, results, now_tick):
        """Keep `side_answers` and add the cached answers of sides that made no
        call. Returns (accepted + reused entries, the reused sides)."""
        facts_now = bb.read("facts").facts_of(now_tick)
        run = sides.sides_to_run(facts_now, bb.read("findings"))
        # a side whose answer was dropped as stale did not really answer this
        # tick: it may still use its cached answer (real time; never in lockstep)
        called = {r.job.line_map["side"] for r in results if not r.stale}
        cache = {s: v for s, v in bb.read("side_answers").items()
                 if s in run and s not in called}          # stale sides dropped
        by_side = {r.job.line_map["side"]: r for r in results if not r.stale}
        for side, env, _ in accepted:
            tick = env.tick
            stamp = lambda x: x if x.startswith("t") and "." in x else f"t{tick}.{x}"
            payload = copy.deepcopy(env.payload)
            for h in payload.get("hypotheses", []):
                h["supports"] = [stamp(x) for x in h.get("supports", [])]
            cache[side] = {"fingerprint": by_side[side].job.line_map["fingerprint"],
                           "payload": payload, "evidence_tick": tick}
        reused = []
        self._reused_shown = {}
        for side in run:
            if side in called or side not in cache:
                continue
            fp = DiagnosticianAgent.fingerprint(bb, side, self.raw_notes)
            if cache[side]["fingerprint"] != fp:
                del cache[side]                             # evidence moved on
                continue
            self._reused_shown[side] = sorted(fp[3])        # cases shown now
            env = dataclasses.replace(
                AgentEnvelope(agent="diagnostician", tick=cache[side]["evidence_tick"]),
                payload=cache[side]["payload"])
            accepted.append((side, env, []))
            reused.append(side)
        bb.write("side_answers", cache, NAME)
        return accepted, reused

    def _expand(self, env, lm: dict):
        """B' -> the single agent's payload (compact.expand_diag_answer), with
        cited facts and cases set the way Diagnostician.run sets them. A failed
        call is left as it is (merge never sees it). The expansion record goes
        to telemetry only."""
        record = {"agent": "diagnostician", "evidence_tick": lm["evidence_tick"],
                  **lm.get("info", {})}
        if env.status == "ok":
            payload, info = compact.expand_diag_answer(env.payload, lm)
            record.update(info, answer=env.payload)
            env = dataclasses.replace(
                env, payload=payload,
                cited_facts=sorted({s for h in payload["hypotheses"]
                                    for s in h["supports"]}),
                cited_cases=sorted({h["case_ref"] for h in payload["hypotheses"]
                                    if h.get("case_ref")}))
        self.compact_log.append(record)
        return env

    def _expand_ver(self, env, lm: dict):
        """Line-number verdicts -> the single agent's verifier payload
        (compact.expand_ver_answer). Counts a disagreement when a claim fails,
        as Verifier.run does for its own answer. A failed call is left as is."""
        record = {"agent": "verifier", "evidence_tick": lm["evidence_tick"],
                  "claims_shown": len(lm["claims"]),
                  "over_limit": lm.get("over_limit", False)}
        self.ver_over_limit += bool(lm.get("over_limit"))
        if env.status == "ok":
            payload, info = compact.expand_ver_answer(env.payload, lm)
            record.update(info, answer=env.payload)
            if payload["agree"] is False:
                self.ver.disagreements += 1
            # run-level: a verdict that left a shown claim unjudged is NOT an
            # agreement, whatever the disagreement rate's denominator says
            self.ver_incomplete += bool(info["not_judged"])
            env = dataclasses.replace(env, payload=payload)
        self.compact_log.append(record)
        return env

    @staticmethod
    def _stamped(env, facts, tick: int):
        stamp = lambda i: f"t{tick}.{i}"
        payload = copy.deepcopy(env.payload)
        for h in payload.get("hypotheses", []) or []:
            h["supports"] = [stamp(s) for s in h.get("supports", [])]
        env = dataclasses.replace(env, payload=payload,
                                  cited_facts=[stamp(c) for c in env.cited_facts])
        return env, [dataclasses.replace(f, id=stamp(f.id)) for f in facts]

    def check_notefact(self, result, vocab) -> tuple[bool, str]:
        """A text-reader answer may reach the board only if the call succeeded
        and every value is from the vocabulary. Returns (ok, why)."""
        env = result.envelope
        if env.status != "ok":
            return False, f"{env.status}: {env.error}"
        return compact.check_note_answer(env.payload, vocab)

    def apply_verdict(self, bb, asmt: Assessment, claims: dict, result,
                      now_tick: int) -> dict:
        lm = getattr(result.job, "line_map", None)
        if lm and lm.get("kind") == "verification":
            # Compact verdicts: expanded through the line map, then folded by
            # the single agent's Verifier.apply below, unchanged.
            result.envelope = self._expand_ver(result.envelope, lm)
        env = result.envelope
        asmt.envelopes.append(env.to_dict())
        if self._drop_if_stale(result, now_tick):
            return claims
        if env.status == "ok":
            claims = self.ver.apply(claims, env.payload)
        bb.write("verdict", claims, NAME)
        return claims

    # ------------------------------------------------------------------
    def finalize(self, bb, asmt: Assessment, claims: dict, facts, retrieved,
                 state: str, tick: int, check_ms: float, llm_used: bool,
                 t_tick: float) -> float:
        """L6 approve, shown confidence, assemble, L7 write. Returns hard_ms."""
        t0 = time.perf_counter()
        actions, escalate, unexplained = self.gate.approve(
            claims["hypotheses"], facts, retrieved, state)
        hard_ms = check_ms + (time.perf_counter() - t0) * 1000

        shown = stamp_shown_confidence(
            claims, SimpleNamespace(hypotheses=bb.read("belief")))

        asmt.hypotheses = claims["hypotheses"]
        asmt.actions = [a.__dict__ for a in actions]
        asmt.escalate = escalate
        asmt.unexplained = claims.get("unexplained", []) + unexplained
        asmt.confidence = shown[0] if shown else 0.0
        asmt.headline = claims.get("headline") or asmt.headline
        asmt.llm_invoked = llm_used
        asmt.tick_latency_ms = (time.perf_counter() - t_tick) * 1000
        asmt.deadline_miss = tick_deadline_miss(
            hard_ms, asmt.tick_latency_ms, self.hard_deadline_ms,
            self.tick_budget_ms)

        self._log_events(bb, tick, ("outbox.sensor", "outbox.retriever"))
        bb.write("status", {"tick": tick, "state": state}, NAME)
        return hard_ms
