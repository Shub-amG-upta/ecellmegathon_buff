"""
=============================================================================
 ORCHESTRATOR  --  one tick, start to finish  (Plan §2, §7, §11.5)
=============================================================================

PLAIN PYTHON. NOT A MODEL. It decides which agents run, in what order, with
what budget.

  L0 Ingest              read samples into the rolling window          ~0
  L1 Deterministic       limit/rate/balance/validity/pattern -> Facts  ~ms
  L2 Triage              decide if the expensive stages run at all     ~ms
  L3 Retrieval           signature match + note search                 embed
  L4 Diagnosis           Diagnostician agent                           LLM
  L5 Verification        Verifier agent                                LLM
  L6 Action gate         validate, look up actions, decide escalation  ~ms
  L7 Memory write        update world model, checkpoint                ~ms

The single most important structural idea: the language model never sees raw
numbers, and never has the last word. L1 turns numbers into facts, L6 turns
the model's answer into an approved action. The model works in the middle.

THE TICK NEVER FAILS. Every model path has a deterministic fallback, and a
deadline miss is a recorded metric, not a hidden error.
=============================================================================
"""

from __future__ import annotations

import time

from ..schemas import Assessment, TAGS
from . import world_model as wmod
from .l1_symbolize import build_signature, evidence_packet, headline
from .l1_checks import _slope_per_min
from .l2_triage import triage


class Orchestrator:
    def __init__(self, checks, retriever, diagnostician, verifier, gate, cfg):
        self.checks = checks
        self.retriever = retriever
        self.diag = diagnostician
        self.ver = verifier
        self.gate = gate
        self.cfg = cfg
        self.policy = cfg.get("policy", "sequential")   # sequential|pipelined|conditional
        # The operator's question for this episode (query.txt, known bug 4).
        # Set by the harness after construction; "" when there is none.
        self.operator_query = ""
        self.tick_budget_ms = cfg.get("tick_period_s", 30) * 1000
        self.hard_deadline_ms = cfg.get("hard_stage_deadline_ms", 200)
        self.llm_budget_ms = cfg.get("llm_budget_ms", 8000)
        # Degradation ladder rung, 0 = full pipeline (Plan §11.5)
        self.rung = 0

    # ===================================================================
    def tick(self, wm, window, tick_no: int, timestamp: str,
             now_s: float) -> Assessment:
        t_tick = time.perf_counter()

        # Clear any TRANSIENT degraded marker from a previous tick. The only
        # persistent degraded state is the degradation ladder (rung >= 1),
        # which degrade()/recover() own; a one-off LLM timeout on an earlier
        # tick must NOT latch DEGRADED for the rest of the episode (known bug 1
        # -- one failed call turned ep_N01_normal into 56 false positives).
        wm.degraded_mode = rung_marker(self.rung)

        # ---------------- L1: deterministic checks (HARD stage) ----------
        t0 = time.perf_counter()
        facts = self.checks.run(window, tick_no, wm.trusted_tags)
        hard_ms = (time.perf_counter() - t0) * 1000

        # Trust recomputed from this tick's VALIDITY facts (not latched);
        # latest values cached for triage's trip extrapolation.
        refresh_trust_and_residuals(wm, facts, window)

        wmod.update_findings(wm, facts, tick_no)
        wmod.update_baselines(wm, window, tick_no)

        # ---------------- L2: triage (the cost controller) ---------------
        level, reason = triage(facts, wm, self.cfg)
        state = wmod.derive_state(facts, level, wm)

        asmt = Assessment(tick=tick_no, timestamp=timestamp, state=state,
                          headline=headline(facts),
                          facts=[f.to_dict() for f in facts],
                          triage=level, degraded_mode=wm.degraded_mode)

        # ---- QUIET: stop here. No LLM. This is what keeps average cost low.
        if level == "QUIET" or self.rung >= 5:
            asmt.actions = []
            asmt.tick_latency_ms = (time.perf_counter() - t_tick) * 1000
            asmt.deadline_miss = quiet_deadline_miss(hard_ms, self.hard_deadline_ms)
            wm.tick = tick_no
            wm.state = state
            return asmt

        # ---------------- L3: retrieval ----------------------------------
        slopes = slopes_10min(window)
        signature = build_signature(facts, slopes, self.checks.bands)
        retrieved = self.retriever.retrieve(facts, signature, now_s, level,
                                            operator_query=self.operator_query)

        # Deterministic belief update runs BEFORE the model. If the LLM never
        # answers, we still have ranked hypotheses from signature matching
        # alone -- that is the phase-3 baseline and it is the safe floor.
        wmod.update_hypotheses(wm, facts, signature, retrieved["cases"], tick_no,
                               retire_after=self.cfg["belief"]["retire_after_ticks"])
        ranked = wmod.rank_hypotheses(wm, top=3)
        # Telemetry only: the deterministic ranking by log-odds, before any model
        # reply can reorder claims. Does not feed claims or any decision.
        asmt.belief_ranking = wmod.belief_ranking(wm)
        claims = initial_claims(asmt.headline, ranked)

        # ---------------- L4: Diagnostician (SOFT stage) -----------------
        envelopes = []
        llm_used = False
        if self.rung < 5 and level in ("WATCH", "INVESTIGATE", "URGENT"):
            evidence = evidence_packet(
                facts, max_facts=evidence_max_facts(level, self.rung))
            env = self.diag.run(tick_no, evidence, retrieved, facts,
                                wm_summary(wm))
            envelopes.append(env.to_dict())
            llm_used = True

            claims, degraded = fold_diagnosis(claims, env, facts, self.gate,
                                              self.cfg)
            if degraded is not None:
                wm.degraded_mode = degraded
                asmt.degraded_mode = wm.degraded_mode

        # ---------------- L5: Verifier (SOFT stage) ----------------------
        top_conf = top_confidence(claims)
        if self.rung < 1 and self.ver.should_run(level, top_conf):
            venv = self.ver.run(tick_no, claims, facts)
            envelopes.append(venv.to_dict())
            if venv.status == "ok":
                claims = self.ver.apply(claims, venv.payload)

        # ---------------- L6: action gate (HARD stage) -------------------
        t0 = time.perf_counter()
        actions, escalate, unexplained = self.gate.approve(
            claims["hypotheses"], facts, retrieved, state)
        hard_ms += (time.perf_counter() - t0) * 1000

        # ---------------- shown confidence (DISPLAY ONLY) ----------------
        # Computed after the verifier and the gate, from the final list; nothing
        # below reads it. The decision value stays in h["confidence"].
        shown = stamp_shown_confidence(claims, wm)

        # ---------------- assemble --------------------------------------
        asmt.hypotheses = claims["hypotheses"]
        asmt.actions = [a.__dict__ for a in actions]
        asmt.escalate = escalate
        asmt.unexplained = claims.get("unexplained", []) + unexplained
        asmt.confidence = shown[0] if shown else 0.0     # display: shown value of rank 1
        asmt.headline = claims.get("headline") or asmt.headline
        asmt.llm_invoked = llm_used
        asmt.envelopes = envelopes
        asmt.tick_latency_ms = (time.perf_counter() - t_tick) * 1000

        # C1: HARD stages must meet their deadline on EVERY tick, miss rate 0.
        # C2: end-to-end p95 within the tick period.
        asmt.deadline_miss = tick_deadline_miss(
            hard_ms, asmt.tick_latency_ms, self.hard_deadline_ms, self.tick_budget_ms)

        # ---------------- L7: memory write -------------------------------
        wm.tick = tick_no
        wm.state = state
        return asmt

    # ===================================================================
    #  Degradation ladder (Plan §11.5). Each rung is a measurable
    #  operating point, and the ladder IS the mixed-criticality
    #  contribution made concrete: SOFT stages degrade by SUBSTITUTION,
    #  HARD stages (L1, L6) always survive.
    # ===================================================================
    LADDER = [
        "full pipeline, NPU, 1B + Verifier",
        "Verifier becomes conditional",
        "context 4096 -> 2048, retrieval depth reduced",
        "Diagnostician moves NPU -> CPU",
        "Diagnostician 1B -> 270M",
        "LLM off entirely; deterministic facts and rule-derived actions only",
    ]

    def degrade(self, wm, reason: str) -> str:
        """Step down one rung. Called on thermal or energy pressure."""
        if self.rung < len(self.LADDER) - 1:
            self.rung += 1
        if self.rung >= 2:
            self.cfg["retrieval"]["k_notes"] = 3
            self.cfg["retrieval"]["k_cases"] = 3
        if self.rung >= 1:
            self.ver.cfg["verifier"] = "conditional"
        wm.degraded_mode = f"rung{self.rung}:{self.LADDER[self.rung]}"
        return wm.degraded_mode

    def recover(self, wm) -> None:
        self.rung = max(0, self.rung - 1)
        wm.degraded_mode = None if self.rung == 0 else \
            f"rung{self.rung}:{self.LADDER[self.rung]}"



# =======================================================================
#  Tick glue, as module functions so fieldmind/multi reuses it by import
#  (multi-agent rule: shared pieces are imported, never copied). Moved out of
#  Orchestrator.tick unchanged; tick() calls them in the same order.
# =======================================================================

def refresh_trust_and_residuals(wm, facts, window) -> None:
    """Trust is recomputed from THIS tick's VALIDITY facts, not latched: a
    tag whose instrument fault has cleared (transient stuck / spike) is
    trusted again next tick. A genuinely frozen instrument keeps emitting
    a VALIDITY fact every tick (_validity runs over every tag regardless
    of trust), so it stays out. Any balance that needs an untrusted tag
    is still reported SUSPENDED by L1, never silently skipped."""
    validity_tags = {f.tags[0] for f in facts
                     if f.check == "VALIDITY" and f.tags}
    wm.trusted_tags = set(TAGS) - validity_tags

    # Cache latest values for the trip-prediction extrapolation in triage.
    for tag in TAGS:
        v = window.latest(tag)
        if v is not None:
            wm.residuals[f"last_{tag}"] = v


def rung_marker(rung: int) -> str | None:
    """The degraded marker a tick starts from: only the ladder persists."""
    return None if rung == 0 else f"rung{rung}:{Orchestrator.LADDER[rung]}"


def initial_claims(headline_text: str, ranked) -> dict:
    """The deterministic claims: belief's ranked hypotheses, before any model."""
    return {"headline": headline_text,
            "hypotheses": [{"rank": i + 1, "cause": h.cause,
                            "confidence": h.confidence,
                            "supports": h.supports,
                            "case_ref": h.case_ref,
                            "discriminator": h.discriminator}
                           for i, h in enumerate(ranked)],
            "unexplained": []}


def evidence_max_facts(level: str, rung: int) -> int:
    return 6 if (level == "URGENT" or rung >= 2) else 12


def fold_diagnosis(claims: dict, env, facts, gate, cfg) -> tuple[dict, str | None]:
    """Fold a diagnostician envelope into the claims. Returns (claims,
    degraded marker or None); the caller owns writing the marker."""
    if env.status == "ok":
        cit = gate.check_citations(env.cited_facts, facts)
        if cit["faithfulness"] >= cfg.get("min_faithfulness", 0.5):
            claims = merge(claims, env.payload)
        else:
            # The model cited facts that do not exist. Its ranking is
            # not trustworthy, so we keep the deterministic one and
            # record why. This is the T3 failure path.
            claims["unexplained"].append(
                f"diagnostician cited non-existent facts {cit['invalid']}; "
                f"deterministic ranking retained")
        return claims, None
    # timeout / invalid_schema -> deterministic output, marked.
    return claims, f"llm_{env.status}"


def top_confidence(claims: dict) -> float:
    return claims["hypotheses"][0]["confidence"] if claims["hypotheses"] else 0.0


def stamp_shown_confidence(claims: dict, wm) -> list[float]:
    """Computed after the verifier and the gate, from the final list; nothing
    below reads it. The decision value stays in h["confidence"]."""
    shown = wmod.shown_confidences(
        claims["hypotheses"], [h for h in wm.hypotheses if not h.retired])
    for h, v in zip(claims["hypotheses"], shown):
        h["confidence_shown"] = v
    return shown


def quiet_deadline_miss(hard_ms: float, hard_deadline_ms: float) -> bool:
    return hard_ms > hard_deadline_ms


def tick_deadline_miss(hard_ms: float, tick_ms: float, hard_deadline_ms: float,
                       tick_budget_ms: float) -> bool:
    """C1: HARD stages must meet their deadline on EVERY tick, miss rate 0.
    C2: end-to-end p95 within the tick period."""
    return hard_ms > hard_deadline_ms or tick_ms > tick_budget_ms


def slopes_10min(window) -> dict:
    """Per-tag raw slope over the last 10 minutes, for the signature.

    Stage 6 tried load-normalising bed_temp_avg here (the descriptor bands
    are then set from the normalised no-fault distribution). It is better on
    every intrinsic measure but dropped mock Q2_top1 0.334 -> 0.173, so the
    band values were reverted to the Stage-5 set and this reverted with
    them. See reports/stage6_band_edges.md "Reverted".
    """
    out = {}
    n = int(10 * 60 / window.dt_s)
    for tag in TAGS:
        s = window.series(tag)
        out[tag] = _slope_per_min(s[-n:], window.dt_s) if len(s) >= 6 else 0.0
    return out


def wm_summary(wm) -> str:
    """A few tokens of carried belief. Deliberately terse: the world model
    is large and most of it is not decision-relevant this tick."""
    open_f = [f.detail for f in wm.open_findings if not f.resolved][:3]
    hyps = [f"{h.cause}({h.confidence:.2f})"
            for h in wmod.rank_hypotheses(wm, 2)]
    untrusted = sorted(set(TAGS) - wm.trusted_tags)
    parts = []
    if open_f:
        parts.append("open findings: " + "; ".join(open_f))
    if hyps:
        parts.append("current hypotheses: " + ", ".join(hyps))
    if untrusted:
        parts.append("UNTRUSTED TAGS: " + ", ".join(untrusted))
    return " | ".join(parts) or "no prior findings this episode"


def merge(deterministic: dict, model: dict) -> dict:
    """Model ranking wins on ORDER and WORDING; the deterministic layer
    wins on CONFIDENCE and on which fact ids are cited. That split is the
    whole design: the model is allowed to reason, not to assert evidence,
    and not to overwrite the cross-tick belief accumulator.

    Known bug 2: the old body did ``out = dict(model)``, so a single LLM
    reply replaced the accumulated hypothesis list wholesale and the
    log-odds carried across ticks was thrown away -- symptom was ~9.5
    distinct actions proposed per episode from a 16-action catalogue.
    """
    det_hyps = deterministic.get("hypotheses", []) or []
    det_by_cause = {h.get("cause"): h for h in det_hyps}
    det_by_case = {h.get("case_ref"): h for h in det_hyps if h.get("case_ref")}

    merged: list[dict] = []
    matched_causes: set = set()
    for mh in model.get("hypotheses", []) or []:
        dh = (det_by_cause.get(mh.get("cause"))
              or det_by_case.get(mh.get("case_ref")))
        if dh is not None:
            matched_causes.add(dh.get("cause"))
            merged.append({
                # model wins: position in this list, wording, discriminator,
                # and which of THIS tick's fact ids it cites (fact ids are
                # tick-local; L4 already validated these via check_citations
                # or this merge would not have been called).
                "cause": mh.get("cause") or dh.get("cause"),
                "discriminator": (mh.get("discriminator")
                                  or dh.get("discriminator", "")),
                "supports": list(mh.get("supports", []))[:6],
                # deterministic wins: the cross-tick accumulated confidence.
                "confidence": float(dh.get("confidence", 0.0)),
                "case_ref": dh.get("case_ref") or mh.get("case_ref"),
            })
        else:
            # A model-only idea: allowed to be raised, but it has not
            # accumulated any belief -- cap its confidence.
            merged.append({
                "cause": mh.get("cause", "unknown"),
                "discriminator": mh.get("discriminator", ""),
                "confidence": min(float(mh.get("confidence", 0.3)), 0.5),
                "supports": list(mh.get("supports", []))[:6],
                "case_ref": mh.get("case_ref"),
                "model_only": True,
            })

    # Deterministic hypotheses the model did not mention are NOT dropped:
    # the accumulator persists across ticks and one tick where the LLM
    # omitted a hypothesis is not a reason to retire it. They carry no
    # citation for this tick (their accumulated supports are stale
    # tick-local ids) -- confidence alone keeps them ranked.
    for dh in det_hyps:
        if dh.get("cause") in matched_causes:
            continue
        if any(m["cause"] == dh.get("cause") for m in merged):
            continue
        merged.append({
            "cause": dh.get("cause"),
            "discriminator": dh.get("discriminator", ""),
            "confidence": float(dh.get("confidence", 0.0)),
            "supports": [],
            "case_ref": dh.get("case_ref"),
            "carried": True,
        })

    if not merged:
        merged = [{"cause": h.get("cause"), "confidence": float(h.get("confidence", 0.0)),
                   "supports": [], "case_ref": h.get("case_ref"),
                   "discriminator": h.get("discriminator", "")} for h in det_hyps]

    out = {
        "headline": model.get("headline") or deterministic.get("headline"),
        "unexplained": (list(model.get("unexplained", []))
                        + list(deterministic.get("unexplained", []))),
        "hypotheses": merged,
    }
    for i, h in enumerate(out["hypotheses"]):
        h["rank"] = i + 1
        h["confidence"] = float(h["confidence"])
    return out


# The helpers above used to be Orchestrator static methods; the old names stay
# as aliases for existing callers and tests.
Orchestrator._slopes = staticmethod(slopes_10min)
Orchestrator._wm_summary = staticmethod(wm_summary)
Orchestrator._merge = staticmethod(merge)
