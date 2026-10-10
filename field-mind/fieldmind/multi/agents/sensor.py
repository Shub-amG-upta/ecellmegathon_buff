"""
Sensor agent (code, every tick): L0/L1/L1b.

Runs the five check families, refreshes trust and the cached last values,
updates findings and baselines, and (on a non-QUIET tick) builds the
signature. Writes: facts (tick-stamped index), trust, residuals, findings,
baselines, signature, outbox.sensor. Every call below is the single agent's
own function, imported.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from ...agent import world_model as wmod
from ...agent.l1_symbolize import build_signature
from ...agent.orchestrator import refresh_trust_and_residuals, slopes_10min

NAME = "sensor"


class SensorAgent:
    def __init__(self, checks):
        self.checks = checks

    def run(self, bb, window, tick: int) -> tuple[list, float]:
        """Returns (facts, L1 check time in ms -- the single agent's hard_ms)."""
        t0 = time.perf_counter()
        facts = self.checks.run(window, tick, bb.read("trust"))
        check_ms = (time.perf_counter() - t0) * 1000

        bb.mutable("facts", NAME).add(tick, facts)

        # The reused world-model functions mutate a WorldModel in place; give
        # them a view holding ONLY this agent's sections (live) and a fresh
        # event outbox -- the event log belongs to gate+memory.
        events: list = []
        view = SimpleNamespace(trusted_tags=bb.read("trust"),
                               residuals=bb.mutable("residuals", NAME),
                               open_findings=bb.mutable("findings", NAME),
                               baselines=bb.mutable("baselines", NAME),
                               timeline=events)
        refresh_trust_and_residuals(view, facts, window)
        bb.write("trust", view.trusted_tags, NAME)
        wmod.update_findings(view, facts, tick)
        wmod.update_baselines(view, window, tick)
        bb.write("outbox.sensor", (tick, events), NAME)
        return facts, check_ms

    def signature(self, bb, window, facts) -> dict:
        slopes = slopes_10min(window)
        sig = build_signature(facts, slopes, self.checks.bands)
        bb.write("signature", {"signature": sig, "slopes": slopes}, NAME)
        return sig
