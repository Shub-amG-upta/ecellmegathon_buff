"""
=============================================================================
 BLACKBOARD  --  the shared world model, one writer per section
 (docs/multi_agent_plan.pdf, "Blackboard and messages")
=============================================================================

Agents never call each other; they read and write named sections here. Every
section has exactly ONE writer (OWNERS below). The rule is enforced in code,
three ways:

  1. write(section, value, writer)   raises WriterError for a non-owner
  2. mutable(section, writer)        hands the live object only to the owner
  3. audit mode (multi.audit_writes) fingerprints every section before and
     after each agent step (`step()`), and raises WriterError if a section
     changed that the stepping agent does not own. This is the one that
     matters in practice: the reused world-model functions
     (world_model.update_findings, update_hypotheses ...) mutate in place, so
     an agent handed the wrong object would otherwise write silently.

read(section) returns read-only containers (tuple / frozenset /
MappingProxyType), so an accidental write to the TOP level of a section through
a read handle fails at once. This is SHALLOW: the objects inside (a Hypothesis,
a case dict) are the live ones, and only the audit catches a change to them.
With audit_writes off, enforcement is the API check alone.

The sections map onto the single agent's WorldModel fields where one exists,
so the same object can be inspected either way. Events (the timeline) are
written only by gate+memory: other agents post this tick's events to their own
outbox section and the gate appends them, in pipeline order.

Fact IDs: facts are kept per tick and indexed by their TICK-STAMPED id
(`t84.F1`). The Fact objects themselves keep the local id (`F1`) because the
prompt text must stay identical to the single agent's in Phase 1; the stamp
lives in the index, and the gate resolves citations against the facts of the
answer's EVIDENCE tick.

Stdlib only (copied to the device with the rest of fieldmind/).
=============================================================================
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import asdict, is_dataclass
from types import MappingProxyType

# Section -> its single writer. Readers are documented in
# reports/multi_phase1_plumbing.md ("Design note").
OWNERS: dict[str, str] = {
    "facts": "sensor",          # per-tick facts, tick-stamped index
    "trust": "sensor",          # WorldModel.trusted_tags
    "residuals": "sensor",      # WorldModel.residuals (last values for triage)
    "findings": "sensor",       # WorldModel.open_findings
    "baselines": "sensor",      # WorldModel.baselines
    "signature": "sensor",      # this tick's (tag, direction, band) signature + slopes
    "outbox.sensor": "sensor",  # this tick's events, for the gate to log
    "triage": "triage",         # level, reason, plant state
    "retrieval": "retriever",   # retrieved packet (cases, notes, records, candidates)
    "belief": "retriever",      # WorldModel.hypotheses (log-odds)
    "outbox.retriever": "retriever",
    "jobs": "scheduler",        # job table
    "diagnosis": "gate",        # checked + merged diagnostician claims
    "verdict": "gate",          # claims after the checked verifier answer
    "status": "gate",           # degraded_mode, tick, state
    "events": "gate",           # WorldModel.timeline
    "assessment": "gate",       # the published Assessment
    "equipment": "gate",        # WorldModel.equipment (not written in Phase 1)
    "notes_seen": "gate",       # WorldModel.notes_seen (not written in Phase 1)
    # Phase 2. Note-facts: one per engineer note, written by the text reader
    # AFTER the gate has checked the model's answer (plan p.12). Record-facts:
    # written by code from records.json, no model call (human decision a).
    "notefacts": "text_reader",
    "recordfacts": "retriever",
    # accuracy-fix decision 1, secondary arm (merge_rule: nudge): the model's
    # capped per-case offsets for the episode. Never written into belief.
    "model_evidence": "gate",
    # Phase 3 (call only on change): each side's last CHECKED answer and the
    # fingerprint of the evidence it answered. Written by the gate only.
    "side_answers": "gate",
}

# Every agent that takes a step. The diagnostician and verifier own no section:
# their answers come back as Results and only the gate writes them onto the
# board. The text reader owns `notefacts` but writes only gate-checked answers.
MODEL_AGENTS = ["diagnostician", "verifier"]
AGENTS = sorted(set(OWNERS.values())) + MODEL_AGENTS


class WriterError(AssertionError):
    """A section was written by an agent that is not its owner."""


class FactsBook:
    """Facts per tick, indexed by tick-stamped id. Keeps the last `keep` ticks
    (an answer may be checked one tick after its evidence tick)."""

    def __init__(self, keep: int = 2):
        self.keep = keep
        self._by_tick: dict[int, list] = {}

    @staticmethod
    def stamp(tick: int, fact_id: str) -> str:
        return f"t{tick}.{fact_id}"

    def add(self, tick: int, facts: list) -> None:
        self._by_tick[tick] = list(facts)
        for old in sorted(self._by_tick)[:-self.keep]:
            del self._by_tick[old]

    def facts_of(self, tick: int) -> list:
        """The Fact objects of `tick` (local ids, as they went into the prompt).
        Raises KeyError for a tick no longer kept: an answer that old is stale."""
        return list(self._by_tick[tick])

    def stamped_ids(self, tick: int) -> list[str]:
        return [self.stamp(tick, f.id) for f in self._by_tick[tick]]

    def resolve(self, stamped: str):
        """`t84.F1` -> the Fact, or None if no such fact was emitted."""
        head, _, local = stamped.partition(".")
        try:
            tick = int(head[1:])
        except ValueError:
            return None
        return next((f for f in self._by_tick.get(tick, []) if f.id == local), None)

    def ticks(self) -> list[int]:
        return sorted(self._by_tick)

    def __repr__(self) -> str:      # used by the audit fingerprint
        return repr({t: [f.to_dict() for f in fs] for t, fs in sorted(self._by_tick.items())})


class FactsView:
    """Read-only face of a FactsBook (no add())."""

    def __init__(self, book: FactsBook):
        self._b = book

    def facts_of(self, tick: int) -> list:
        return self._b.facts_of(tick)

    def stamped_ids(self, tick: int) -> list[str]:
        return self._b.stamped_ids(tick)

    def resolve(self, stamped: str):
        return self._b.resolve(stamped)

    def ticks(self) -> list[int]:
        return self._b.ticks()


def _readonly(v):
    if isinstance(v, FactsBook):
        return FactsView(v)
    if isinstance(v, list):
        return tuple(v)
    if isinstance(v, set):
        return frozenset(v)
    if isinstance(v, dict):
        return MappingProxyType(v)
    return v


def _canon(v):
    """A deterministic, structure-revealing form for fingerprinting."""
    if is_dataclass(v) and not isinstance(v, type):
        return ("dc", type(v).__name__, _canon(asdict(v)))
    if isinstance(v, dict):
        return ("d", tuple((repr(k), _canon(x)) for k, x in sorted(v.items(), key=lambda kv: repr(kv[0]))))
    if isinstance(v, (list, tuple)):
        return ("l", tuple(_canon(x) for x in v))
    if isinstance(v, (set, frozenset)):
        return ("s", tuple(sorted(repr(x) for x in v)))
    return repr(v)


def fingerprint(v) -> str:
    return hashlib.sha1(repr(_canon(v)).encode()).hexdigest()


class Blackboard:
    """Named sections with single-writer enforcement.

    The WorldModel-backed sections (trust, residuals, findings, baselines,
    belief, events, status) live on the WorldModel passed in, so the reused
    world-model functions operate on the same objects.
    """

    _WM_FIELDS = {"trust": "trusted_tags", "residuals": "residuals",
                  "findings": "open_findings", "baselines": "baselines",
                  "belief": "hypotheses", "events": "timeline",
                  "equipment": "equipment", "notes_seen": "notes_seen"}

    def __init__(self, wm, audit: bool = True):
        self.wm = wm
        self.audit = audit
        # Real-time mode: model calls run on lane worker threads. Model agents
        # own no section and write nothing, so a step taken off this (the tick
        # loop's) thread is not audited and cannot mark an active writer.
        self._thread = threading.get_ident()
        self._s: dict = {
            "facts": FactsBook(),
            "signature": {},
            "outbox.sensor": (None, []),
            "triage": {},
            "retrieval": {},
            "outbox.retriever": (None, []),
            "jobs": [],
            "diagnosis": None,
            "verdict": None,
            "assessment": None,
            "notefacts": {},            # note id -> note-fact (or a rejection)
            "recordfacts": [],
            "model_evidence": {},
            "side_answers": {},         # side -> {fingerprint, payload, evidence_tick}
        }
        self._active: str | None = None
        self.writes: dict[str, int] = {s: 0 for s in OWNERS}

    # ------------------------------------------------------------------
    def _get(self, section: str):
        if section not in OWNERS:
            raise KeyError(f"no blackboard section {section!r}")
        if section in self._WM_FIELDS:
            return getattr(self.wm, self._WM_FIELDS[section])
        if section == "status":
            return {"degraded_mode": self.wm.degraded_mode,
                    "tick": self.wm.tick, "state": self.wm.state,
                    "episode_id": self.wm.episode_id}
        return self._s[section]

    def keep_fact_ticks(self, n: int) -> None:
        """How many ticks of facts the board holds (real time keeps more, so a
        late answer whose side's evidence is unchanged can still be checked)."""
        self._s["facts"].keep = max(2, int(n))

    def _check(self, section: str, writer: str) -> None:
        if threading.get_ident() != self._thread:
            # model calls run on lane worker threads; nothing there may write
            raise WriterError(f"{writer!r} wrote {section!r} off the tick thread")
        owner = OWNERS.get(section)
        if owner is None:
            raise KeyError(f"no blackboard section {section!r}")
        if writer != owner:
            raise WriterError(f"{writer!r} may not write section {section!r} "
                              f"(owner: {owner!r})")
        if self._active is not None and writer != self._active:
            raise WriterError(f"{writer!r} wrote {section!r} during "
                              f"{self._active!r}'s step")

    # ------------------------------------------------------------------
    def read(self, section: str):
        return _readonly(self._get(section))

    def mutable(self, section: str, writer: str):
        """The live object, for in-place updates by the owner only."""
        self._check(section, writer)
        self.writes[section] += 1
        return self._get(section)

    def write(self, section: str, value, writer: str) -> None:
        self._check(section, writer)
        self.writes[section] += 1
        if section in self._WM_FIELDS:
            setattr(self.wm, self._WM_FIELDS[section], value)
        elif section == "status":
            for k in ("degraded_mode", "tick", "state"):
                if k in value:
                    setattr(self.wm, k, value[k])
        else:
            self._s[section] = value

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, str]:
        return {s: fingerprint(self._get(s)) for s in OWNERS}

    def step(self, agent: str):
        """Context manager around one agent's step. Under audit, any section
        that changed and is not owned by `agent` raises WriterError."""
        return _Step(self, agent)


class _Step:
    def __init__(self, bb: Blackboard, agent: str):
        if agent not in AGENTS:
            raise KeyError(f"unknown agent {agent!r}")
        self.bb, self.agent = bb, agent

    def __enter__(self):
        self.off_thread = threading.get_ident() != self.bb._thread
        if self.off_thread:
            return self.bb
        self.prev = self.bb._active
        self.bb._active = self.agent
        self.before = self.bb.snapshot() if self.bb.audit else None
        return self.bb

    def __exit__(self, exc_type, exc, tb):
        if self.off_thread:
            return False
        self.bb._active = self.prev
        if exc_type is not None or self.before is None:
            return False
        after = self.bb.snapshot()
        bad = [s for s in OWNERS
               if after[s] != self.before[s] and OWNERS[s] != self.agent]
        if bad:
            raise WriterError(f"{self.agent!r} changed sections it does not "
                              f"own: {bad}")
        return False
