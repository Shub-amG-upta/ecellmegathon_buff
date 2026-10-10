"""
Triage agent (code, every tick): L2 plus the fact-derived plant state.

Reads findings, residuals, trust and status; writes `triage` (level, reason,
state). The plan folds triage into the scheduler; it is its own agent here so
the scheduler stays a pure job/lane component.
"""

from __future__ import annotations

from types import SimpleNamespace

from ...agent import world_model as wmod
from ...agent.l2_triage import triage

NAME = "triage"


class TriageAgent:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def run(self, bb, facts) -> tuple[str, str, str]:
        status = bb.read("status")
        view = SimpleNamespace(open_findings=bb.read("findings"),
                               residuals=bb.read("residuals"),
                               trusted_tags=bb.read("trust"),
                               degraded_mode=status["degraded_mode"])
        level, reason = triage(facts, view, self.cfg)
        state = wmod.derive_state(facts, level, view)
        bb.write("triage", {"level": level, "reason": reason, "state": state}, NAME)
        return level, reason, state
