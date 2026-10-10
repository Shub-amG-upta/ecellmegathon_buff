"""
=============================================================================
 L6  --  THE ACTION GATE  (Plan §2 stage L6, §13.1 action contract)
=============================================================================

The model does not have the last word. L1 turns numbers into facts; L6 turns
the model's answer into an APPROVED ACTION.

Four jobs:
  1. Validate the output schema. Reject anything malformed.
  2. Check every cited fact ID exists and the quoted detail matches.
     -> this IS the evidence-faithfulness metric (T3), for free.
  3. Look up actions from the FIXED CATALOGUE. The model selects; it never
     writes a procedure.
  4. Decide escalation.

Deterministic code. ~ms. Always runs, even at degradation ladder rung 5.
=============================================================================
"""

from __future__ import annotations

import json
from pathlib import Path

from ..schemas import Action, Fact


class ActionCatalogue:
    def __init__(self, path: Path):
        raw = json.loads(Path(path).read_text())["actions"]
        self.by_id = {a["id"]: a for a in raw}
        self.all = raw

    def lookup(self, action_id: str) -> dict | None:
        return self.by_id.get(action_id)

    def for_case(self, case: dict) -> list[dict]:
        return [self.by_id[a] for a in case.get("actions", []) if a in self.by_id]

    def by_preconditions(self, fact_details: str) -> list[dict]:
        """Rule-derived fallback used when no LLM ran (rung 5 of the ladder).

        Crude substring matching against the precondition strings. It is not
        clever, and it does not need to be -- it only has to be SAFE and
        deterministic, which a lookup table is and a model is not.
        """
        hits = []
        low = fact_details.lower()
        for a in self.all:
            pre = a.get("preconditions", [])
            if pre and all(any(w in low for w in p.lower().split()[:2]) for p in pre):
                hits.append(a)
        return hits[:3]


class Gate:
    def __init__(self, catalogue: ActionCatalogue, cfg: dict):
        self.cat = catalogue
        self.cfg = cfg

    # -------------------------------------------------------------------
    def check_citations(self, cited: list[str], facts: list[Fact]) -> dict:
        """Does every cited fact ID exist? Returns the T3 numerator/denominator.

        A hallucinated citation is not an error to be swallowed -- it is a
        measurement. Both the count and the offending ids are returned so the
        evaluator can report faithfulness without re-deriving it.
        """
        ids = {f.id for f in facts}
        good = [c for c in cited if c in ids]
        bad = [c for c in cited if c not in ids]
        return {"n_cited": len(cited), "n_valid": len(good), "invalid": bad,
                "faithfulness": (len(good) / len(cited)) if cited else 1.0}

    # -------------------------------------------------------------------
    def approve(self, hypotheses: list[dict], facts: list[Fact],
                retrieved: dict, state: str) -> tuple[list[Action], bool, list[str]]:
        """Select actions, decide escalation, and return anything unexplained."""
        actions: list[Action] = []
        rejected: list[str] = []

        # --- actions come from the retrieved cases, filtered by the catalogue ---
        for h in hypotheses[:2]:
            case = next((c for c in retrieved.get("cases", [])
                         if c.get("case_id") == h.get("case_ref")), None)
            if not case:
                continue

            # ENFORCED HERE, not in the store: an unverified agent-authored
            # record may never be the sole support for a top-ranked hypothesis
            # (Plan §8.3).
            if case.get("provenance") == "agent_unverified" and h.get("rank") == 1:
                rejected.append(f"{h.get('case_ref')}: unverified sole support")
                continue

            for a in self.cat.for_case(case):
                if a["id"] not in [x.id for x in actions]:
                    actions.append(Action(id=a["id"], text=a["text"],
                                          urgency=a["urgency"], source="catalogue"))

        # --- safe floor: if no model ran or nothing matched, derive from facts ---
        if not actions:
            blob = " ".join(f.detail for f in facts)
            for a in self.cat.by_preconditions(blob):
                actions.append(Action(id=a["id"], text=a["text"],
                                      urgency=a["urgency"], source="rule"))

        # --- escalation is decided from STATE, which is derived from FACTS ---
        escalate = state in ("ALARM", "TRIP_IMMINENT") or \
            any(a.urgency == "NOW" for a in actions)

        # --- residuals no hypothesis explains stay visible to the operator ---
        explained = {t for h in hypotheses for t in h.get("supports", [])}
        unexplained = [f.detail for f in facts
                       if f.severity in ("ALARM", "CRITICAL") and f.id not in explained]

        return actions[:self.cfg.get("max_actions", 3)], escalate, unexplained + rejected
