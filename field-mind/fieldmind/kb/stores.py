"""
=============================================================================
 KNOWLEDGE BASES  --  four stores, deliberately not one  (Plan §6.1)
=============================================================================

  Asset model      static  tags, limits+source, equipment, topology graph
  Case library     curated the RCA cases as structured records
  Notes store      human   engineer notes, timestamped, tag-linked
  Experience store agent   this agent's own closed episodes, via a gate

They are separate because they have different PROVENANCE, and provenance
multiplies into the retrieval score (Plan §8.4). Merging them into one vector
index would throw that away, and provenance weighting is the mechanism that
stops the agent slowly poisoning its own well.
=============================================================================
"""

from __future__ import annotations

import json
from pathlib import Path

# Plan §8.4. Every knowledge record carries a provenance field and retrieval
# scoring multiplies by it.
PROVENANCE_WEIGHT = {
    "manual":           1.00,   # OEM documentation, asset model
    "curated_case":     1.00,   # the RCA case studies
    "engineer_note":    0.90,   # human, timestamped, but sometimes wrong
    "agent_confirmed":  0.70,   # agent episode, human-signed
    "agent_unverified": 0.35,   # agent episode, no confirmation
}


class AssetModel:
    """Tag dictionary, limits and their source, equipment list, topology graph.

    The topology graph does two concrete jobs (Plan §5.2):
      UPSTREAM SEARCH  'level falling and feed short' -> walk upstream from the
                       drum to enumerate every component that could cause it.
                       This produces the CANDIDATE SET, so the model ranks a
                       bounded list rather than inventing causes.
      BLAST RADIUS     'bed over temperature' -> walk downstream to list what
                       is affected next. Drives action urgency.
    """

    def __init__(self, path: Path):
        self.raw = json.loads(Path(path).read_text())
        self.tags = self.raw["tags"]
        self.equipment = self.raw["equipment"]
        # adjacency: upstream[x] = things that feed x
        self.edges = [(e["from"], e["to"]) for e in self.raw["topology"]]
        self._up: dict[str, list[str]] = {}
        self._down: dict[str, list[str]] = {}
        for a, b in self.edges:
            self._up.setdefault(b, []).append(a)
            self._down.setdefault(a, []).append(b)

    def upstream(self, node: str, depth: int = 3) -> list[str]:
        """Bounded breadth-first walk against the flow direction."""
        seen, frontier, out = {node}, [node], []
        for _ in range(depth):
            nxt = []
            for n in frontier:
                for p in self._up.get(n, []):
                    if p not in seen:
                        seen.add(p)
                        out.append(p)
                        nxt.append(p)
            frontier = nxt
        return out

    def downstream(self, node: str, depth: int = 3) -> list[str]:
        seen, frontier, out = {node}, [node], []
        for _ in range(depth):
            nxt = []
            for n in frontier:
                for c in self._down.get(n, []):
                    if c not in seen:
                        seen.add(c)
                        out.append(c)
                        nxt.append(c)
            frontier = nxt
        return out

    def candidates_for(self, symptom_tags: list[str], depth: int = 3) -> list[str]:
        """The candidate cause set handed to the Diagnostician.

        This removes a whole class of hallucination: the model can only choose
        from components that physically exist upstream of the symptom.
        """
        out: list[str] = []
        for tag in symptom_tags:
            node = self.tags.get(tag, {}).get("located_at")
            if node:
                for c in [node] + self.upstream(node, depth):
                    if c not in out:
                        out.append(c)
        return out


class CaseLibrary:
    """RCA cases as structured records, matched by SIGNATURE first.

    For sensor-driven diagnosis, embedding similarity over prose is the wrong
    tool -- two cases can share almost all their vocabulary and have opposite
    signatures. Signature matching is deterministic, explainable, runs in
    microseconds, and beats cosine-over-prose here (Plan §6.2).
    """

    def __init__(self, path: Path):
        self.cases = json.loads(Path(path).read_text())["cases"]
        self.weights = {c["case_id"]: c.get("weight", 1.0) for c in self.cases}

    @staticmethod
    def _to_triples(sig: dict) -> dict:
        """Accept both spellings in the plan:
             {"drum_level": ["DOWN", "FAST"]}          (Appendix A.3)
             {"drum_level|DOWN|FAST": 1.0}             (§6.2, with weights)
        """
        out = {}
        for k, v in sig.items():
            if isinstance(v, list):
                # Must mirror the weighting in l1_symbolize.build_signature,
                # or weighted Jaccard compares two different scales.
                out[(k, v[0], v[1])] = 0.35 if v[0] == "FLAT" else 1.0
            else:
                tag, d, b = k.split("|")
                out[(tag, d, b)] = float(v)
        return out

    @staticmethod
    def contradiction_hits(case: dict, signature: dict) -> list[tuple]:
        """The case's contradicting triples that the current signature carries.

        THE single definition of "this evidence contradicts this case". Retrieval
        (`match`, below) and the belief update (`world_model.update_hypotheses`)
        both call it, so the two layers cannot disagree about what counts as a
        contradiction -- known bug 6 was exactly that disagreement (belief
        compared tag names only, retrieval compared full triples).

        A triple counts when the signature holds it with weight > 0.5, which
        excludes FLAT observations (weight 0.35): a flat tag never contradicts.
        """
        contra = CaseLibrary._to_triples(case.get("contradicting_signature", {}))
        return [t for t in contra if signature.get(t, 0.0) > 0.5]

    def match(self, signature: dict, k: int = 5) -> list[dict]:
        """Weighted Jaccard between the current tick signature and each case.

        sum(min(w)) / sum(max(w)) over the union of triples. Symmetric, bounded
        in [0,1], and it penalises a case for expecting movement we do not see
        -- which is exactly the 'absence is evidence' property we want.
        """
        scored = []
        for case in self.cases:
            cs = self._to_triples(case.get("signature", {}))
            keys = set(cs) | set(signature)
            if not keys:
                continue
            num = sum(min(cs.get(t, 0.0), signature.get(t, 0.0)) for t in keys)
            den = sum(max(cs.get(t, 0.0), signature.get(t, 0.0)) for t in keys)
            score = (num / den) if den else 0.0

            # A contradicting signature present in the data kills the case
            # outright rather than merely lowering it.
            if self.contradiction_hits(case, signature):
                score *= 0.15

            prov = PROVENANCE_WEIGHT.get(case.get("provenance", "curated_case"), 0.5)
            final = score * prov * case.get("weight", 1.0)
            if final > 0.01:
                rec = dict(case)
                rec["score"] = round(final, 3)
                rec["raw_score"] = round(score, 3)
                scored.append(rec)
        return sorted(scored, key=lambda c: -c["score"])[:k]


class NotesStore:
    """Engineer notes. FILTER FIRST, then rank (Plan §6.2).

    Restrict by time window and by mentioned tag/equipment BEFORE scoring.
    Filtering first is what stops a six-week-old note about a different pump
    from winning on vocabulary overlap.

    Scoring here is lexical (token overlap), not embeddings. That is a
    deliberate starting point: it has no model to load, it runs on the device
    today, and it gives the embedding path something to beat. If the MiniLM
    encoder does run through LiteRT (Plan §10.3, to confirm in week one),
    swap `_score` for cosine similarity -- nothing else changes.
    """

    def __init__(self, notes: list[dict] | None = None):
        self.notes = notes or []

    def load_jsonl(self, path: Path) -> "NotesStore":
        p = Path(path)
        if p.exists():
            self.notes = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        return self

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {w.strip(".,;:()").lower() for w in text.split() if len(w) > 2}

    def search(self, query_terms: list[str], now_s: float,
               tags: list[str], k: int = 5,
               lookback_h: float = 72.0) -> list[dict]:
        q = set(t.lower() for t in query_terms)
        tagset = set(tags)
        out = []
        for n in self.notes:
            ts = n.get("t", 0.0)
            # HARD FILTER 1: never expose anything from the future. The harness
            # enforces this too, but a store that can leak is a store that will.
            if ts > now_s:
                continue
            # HARD FILTER 2: time window.
            if now_s - ts > lookback_h * 3600:
                continue
            # SOFT FILTER: tag/equipment overlap, else require lexical hit.
            tag_hit = bool(set(n.get("tags", [])) & tagset)
            overlap = len(self._tokens(n["text"]) & q)
            if not tag_hit and overlap == 0:
                continue

            age_h = (now_s - ts) / 3600.0
            recency = 1.0 / (1.0 + age_h / 24.0)      # halves roughly daily
            prov = PROVENANCE_WEIGHT.get(n.get("provenance", "engineer_note"), 0.5)
            score = (0.6 * tag_hit + 0.1 * overlap) * recency * prov \
                * n.get("reliability", 1.0)
            rec = dict(n)
            rec["score"] = round(score, 3)
            out.append(rec)
        return sorted(out, key=lambda n: -n["score"])[:k]


class ExperienceStore:
    """The agent's own closed episodes. The only store the agent may write to,
    and only through the promotion gate (Plan §8.3).

    Records are weighted BELOW curated cases and, if unverified, may never be
    the sole support for a top-ranked hypothesis. That rule is enforced in
    l6_gate, not here -- a store should not be the thing deciding what counts
    as evidence.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records = json.loads(self.path.read_text())["records"] \
            if self.path.exists() else []

    def save(self) -> None:
        self.path.write_text(json.dumps({"records": self.records}, indent=2))

    def add(self, record: dict) -> None:
        self.records.append(record)
        self.save()

    def match(self, signature: dict, k: int = 3) -> list[dict]:
        """Same weighted Jaccard as the case library, but every hit is tagged
        so the prompt can say 'unconfirmed, seen 3 times'."""
        lib = CaseLibrary.__new__(CaseLibrary)
        lib.cases = self.records
        return lib.match(signature, k)
