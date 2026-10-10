"""
=============================================================================
 L3  --  RETRIEVAL  (Plan §6)
=============================================================================
Signature-match the fact list against past cases. Search the engineer notes.
Pull the relevant asset facts. Runs at WATCH and above.

Retrieval depth is a TUNED PARAMETER, not a constant, because it directly sets
prompt length and therefore prefill cost -- which on the NPU is the dominant
term (Plan §6.3).

  k_cases   3-5
  k_notes   3-8 after filtering
  k_records topology query, capped at 5
  hard cap  2048 tokens at WATCH, 3072 at INVESTIGATE

If retrieval overflows the cap, drop the LOWEST-SCORING ITEMS WHOLE. Never
truncate mid-record: half a case record is worse than no case record, because
the model cannot tell it is reading half.
=============================================================================
"""

from __future__ import annotations

from ..schemas import Fact


# Rough token estimate. 4 chars/token is close enough for budget enforcement;
# the exact count comes back from the backend afterwards for the cost model.
def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class Retriever:
    """Not an LLM. Signature matcher + lexical/vector note search."""

    def __init__(self, asset, cases, notes, experience, cfg: dict,
                 records: dict | None = None):
        self.asset = asset
        self.cases = cases
        self.notes = notes
        self.experience = experience
        self.cfg = cfg
        # records.json for this episode (known bug 3). Static for the episode;
        # time-varying parts (the conductivity log) are filtered to now_s at
        # retrieval so nothing from the future is exposed.
        self.records = records or {}

    def _records_view(self, now_s: float) -> dict:
        """The slice of records.json visible at now_s, as compact text-ready
        fields. The conductivity trend is the leak-vs-blowdown discriminator
        (RCA Case 1 / Case 11), so it is summarised as a direction, not dumped."""
        r = self.records
        if not r:
            return {}
        out = {}
        lab = r.get("coal_lab_report")
        if lab:
            out["coal_lab_report"] = (
                f"GCV {lab.get('gcv_kcal_kg','?')} kcal/kg, moisture "
                f"{lab.get('moisture_pct','?')}%, ash {lab.get('ash_pct','?')}% "
                f"(sampled {abs(lab.get('sample_date_offset_days', 0))} d ago). "
                f"{lab.get('note','')}").strip()
        mh = [m for m in r.get("maintenance_history", [])
              if m.get("offset_days", -1) <= 0]
        if mh:
            out["maintenance_history"] = [
                f"{abs(m['offset_days'])} d ago: {m.get('equipment','?')} - "
                f"{m.get('work','?')}" for m in mh]
        wcl = [e for e in r.get("water_chemistry_log", [])
               if e.get("t", 0.0) <= now_s]
        if len(wcl) >= 2:
            first, last = wcl[0]["conductivity_uS_cm"], wcl[-1]["conductivity_uS_cm"]
            d = last - first
            trend = ("FALLING" if d < -15 else "RISING" if d > 15 else "flat")
            out["boiler_water_conductivity"] = (
                f"{last} uS/cm now, {trend} over the last "
                f"{len(wcl)} samples (from {first})")
        alarms = [a for a in r.get("alarm_log", []) if a.get("t", 0.0) <= now_s]
        if alarms:
            out["alarm_log"] = [str(a) for a in alarms[-5:]]
        return out

    def retrieve(self, facts: list[Fact], signature: dict, now_s: float,
                 triage_level: str, operator_query: str = "") -> dict:
        r = self.cfg["retrieval"]
        k_cases = r["k_cases"]
        k_notes = r["k_notes"]
        budget = r["context_cap_tokens"].get(triage_level, 2048)

        active_tags = sorted({t for f in facts for t in f.tags
                              if f.severity != "INFO"})

        # ---- 1. curated cases, by signature ----
        cases = self.cases.match(signature, k=k_cases)

        # ---- 2. the agent's own past episodes, weighted below curated ----
        past = self.experience.match(signature, k=r.get("k_experience", 2))
        for p in past:
            p["unverified"] = p.get("provenance") == "agent_unverified"

        # ---- 3. notes: filter by time and tag first, then rank ----
        query_terms = []
        for f in facts:
            if f.severity != "INFO":
                query_terms += f.detail.replace(",", " ").split()
        # The operator's own question (query.txt) is a retrieval cue too -- it
        # is what the engineer is actually asking about (known bug 4).
        if operator_query:
            query_terms += operator_query.replace("?", " ").replace(",", " ").split()
        notes = self.notes.search(query_terms, now_s, active_tags, k=k_notes)

        # ---- 4. candidate causes from plant topology (bounds the model) ----
        candidates = self.asset.candidates_for(active_tags,
                                               depth=r.get("topology_depth", 3))
        candidates = candidates[:r.get("k_records", 5)]

        packet = {"cases": cases, "experience": past, "notes": notes,
                  "candidates": candidates, "records": self._records_view(now_s),
                  "operator_query": operator_query, "dropped": []}
        return self._enforce_budget(packet, budget)

    @staticmethod
    def _enforce_budget(packet: dict, budget: int) -> dict:
        """Drop lowest-scoring items whole until the assembled context fits.

        Order of sacrifice: unverified experience, then notes, then cases.
        Candidates are never dropped -- without them the model has no bounded
        list to choose from and the hallucination guard is gone.
        """
        def size(p):
            n = 0
            for c in p["cases"]:
                n += est_tokens(str(c.get("title", "")) + str(c.get("root_cause", ""))
                                + str(c.get("discriminating_evidence", "")))
            for e in p["experience"]:
                n += est_tokens(str(e.get("root_cause", "")))
            for nt in p["notes"]:
                n += est_tokens(nt["text"])
            n += est_tokens(" ".join(p["candidates"]))
            n += est_tokens(str(p.get("records", "")) + str(p.get("operator_query", "")))
            return n

        # records and operator_query are never dropped -- like candidates, they
        # are a required modality for some episodes and small.
        for key in ("experience", "notes", "cases"):
            while size(packet) > budget and packet[key]:
                dropped = packet[key].pop()          # already score-sorted
                packet["dropped"].append(
                    f"{key}:{dropped.get('case_id') or dropped.get('id')}")
        return packet
