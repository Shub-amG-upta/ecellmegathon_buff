"""
Retriever and belief agent (code, non-QUIET ticks): L3 + the belief update.

Matches the signature against the case library, then moves each retrieved
case's log-odds by the bounded update (world_model.update_hypotheses -- the
triple fix and retirement included). Sole writer of `belief`. Model answers
never write belief: in Phase 1 they only reorder the shown claims through the
gate's merge, exactly as in the single agent (no capped model->belief step yet).
"""

from __future__ import annotations

from types import SimpleNamespace

from ...agent import world_model as wmod
from .. import compact

NAME = "retriever"


class RetrieverAgent:
    def __init__(self, retriever, cfg: dict):
        self.retriever = retriever
        self.cfg = cfg

    def run(self, bb, facts, signature, now_s: float, level: str, tick: int,
            operator_query: str) -> tuple[dict, list, list]:
        retrieved = self.retriever.retrieve(facts, signature, now_s, level,
                                            operator_query=operator_query)
        bb.write("retrieval", retrieved, NAME)
        # Record-facts: code, no model call (Phase 2 human decision a).
        bb.write("recordfacts", compact.record_facts(retrieved.get("records")), NAME)

        events: list = []
        view = SimpleNamespace(hypotheses=bb.mutable("belief", NAME),
                               timeline=events)
        wmod.update_hypotheses(view, facts, signature, retrieved["cases"], tick,
                               retire_after=self.cfg["belief"]["retire_after_ticks"])
        bb.write("outbox.retriever", (tick, events), NAME)
        ranked = wmod.rank_hypotheses(view, top=3)
        ranking = wmod.belief_ranking(view)
        return retrieved, ranked, ranking
