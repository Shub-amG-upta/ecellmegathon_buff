"""
=============================================================================
 SCHEMAS  --  the frozen data contracts for the whole system  (Plan §13.1)
=============================================================================

Three contracts must never drift.  Everything else in the repo is negotiable.

  1. EPISODE CONTRACT  the harness reads only the episode files, in timestamp
                       order, and never exposes anything from the future.
  2. FACT CONTRACT     L1 is the SOLE producer of Facts.  Every downstream
                       stage (including the LLM) may only cite Fact IDs that
                       L1 actually emitted.
  3. ACTION CONTRACT   L6 is the SOLE producer of Actions, drawn only from
                       action_catalogue.json.  The model selects; it never
                       writes a procedure.

Deliberately pure-Python (stdlib only) so this module can be copied onto the
QIDK without pulling numpy/pydantic onto the device.
=============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# -----------------------------------------------------------------------
#  Enumerations (plain strings -- easier to serialise, easier to prompt)
# -----------------------------------------------------------------------

# Plant state reported to the engineer, in increasing order of seriousness.
STATES = ["NORMAL", "DEVIATION", "DEGRADED", "ALARM", "TRIP_IMMINENT"]

# Which deterministic check family produced a Fact (Plan §4.1).
CHECK_FAMILIES = ["LIMIT", "RATE", "BALANCE", "VALIDITY", "PATTERN"]

# How much a Fact matters.  Drives triage (Plan §4.4).
SEVERITIES = ["INFO", "WATCH", "ALARM", "CRITICAL"]

# Triage levels -- these decide whether the expensive LLM stages run at all.
TRIAGE_LEVELS = ["QUIET", "WATCH", "INVESTIGATE", "URGENT"]

# The six sensor tags.  Locked in Plan §1 -- do not add a seventh.
TAGS = [
    "drum_level",         # %
    "feed_water_flow",    # TPH
    "steam_flow",         # TPH
    "drum_pressure",      # kg/cm2 (g)
    "bed_temp_avg",       # degC
    "ms_temperature",     # degC  (main steam temperature)
]


# =======================================================================
#  1. FACT  --  produced only by L1.  True by construction.
# =======================================================================

@dataclass(frozen=True)
class Fact:
    """A statement the deterministic layer computed and stands behind.

    Facts are immutable and addressable.  The LLM must cite `id`; the L6 gate
    then re-checks that every cited id exists and that the quoted detail
    matches.  That single mechanism gives us the evidence-faithfulness metric
    (T3) essentially for free, and it is the cheapest hallucination guard we
    have.
    """
    id: str                       # "F3"
    check: str                    # one of CHECK_FAMILIES
    tags: list[str]               # tags involved
    window: tuple[int, int]       # (start_tick, end_tick) this was computed over
    value: float                  # the number behind the claim
    detail: str                   # human-readable, goes verbatim into the prompt
    severity: str                 # one of SEVERITIES
    confidence: float = 1.0       # 1.0 for limits; lower for noisy slope estimates

    def to_dict(self) -> dict:
        d = asdict(self)
        d["window"] = list(self.window)
        return d


# =======================================================================
#  2. WORLD MODEL  --  what the agent believes, carried tick to tick
# =======================================================================

@dataclass
class EquipmentState:
    """Per-component belief.  Populated from the topology graph at episode start."""
    name: str
    status: str = "UNKNOWN"       # OK | SUSPECT | FAULTED | UNKNOWN
    since_tick: int = 0
    reason: str = ""


@dataclass
class Finding:
    """Something noticed and not yet explained.  Kept open across ticks so the
    agent does not re-announce the same thing 400 times (alarm flood, T5)."""
    id: str
    signature_key: str            # dedup key -- same key => same finding
    first_tick: int
    last_tick: int
    severity: str
    detail: str
    resolved: bool = False


@dataclass
class Hypothesis:
    """A candidate cause with a running belief.

    `log_odds` is the accumulator; `confidence` is the squashed 0..1 view of it
    that gets shown to the engineer.  We update log-odds with bounded per-tick
    increments (Plan §5.1) -- close enough to Bayesian to defend in the
    writeup, and it cannot blow up.
    """
    cause: str
    case_ref: str | None = None
    log_odds: float = 0.0
    confidence: float = 0.5
    supports: list[str] = field(default_factory=list)   # Fact ids
    contradicts: list[str] = field(default_factory=list)
    discriminator: str = ""
    first_tick: int = 0
    last_retrieved_tick: int = 0   # last tick a retrieved case carried this hypothesis
    retired: bool = False


@dataclass
class Baseline:
    """Robust per-tag normal behaviour: median + MAD over confirmed-normal ticks.

    `frozen` is the guard that makes family E (slow drift) survivable.  A tag
    with an open finding STOPS updating its baseline, otherwise an adaptive
    baseline happily learns the fault as the new normal (Plan §8.2).
    """
    tag: str
    median: float = 0.0
    mad: float = 0.0
    n: int = 0
    frozen: bool = False


@dataclass
class Event:
    tick: int
    kind: str                     # STATE_CHANGE | HYP_RETIRED | NOTE_SEEN | DEGRADED | ...
    detail: str


@dataclass
class WorldModel:
    """The blackboard.  This is what makes the system an agent rather than a
    classifier: it is one typed object, carried from tick to tick."""
    episode_id: str = ""
    tick: int = 0
    state: str = "NORMAL"
    equipment: dict[str, EquipmentState] = field(default_factory=dict)
    open_findings: list[Finding] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    residuals: dict[str, float] = field(default_factory=dict)
    baselines: dict[str, Baseline] = field(default_factory=dict)
    trusted_tags: set[str] = field(default_factory=lambda: set(TAGS))
    timeline: list[Event] = field(default_factory=list)
    notes_seen: list[str] = field(default_factory=list)
    degraded_mode: str | None = None

    def to_dict(self) -> dict:
        """Checkpointable view (Plan §8.1: episodic memory is written to disk
        each tick).  `set` is not JSON serialisable, hence the explicit cast."""
        return {
            "episode_id": self.episode_id,
            "tick": self.tick,
            "state": self.state,
            "equipment": {k: asdict(v) for k, v in self.equipment.items()},
            "open_findings": [asdict(f) for f in self.open_findings],
            "hypotheses": [asdict(h) for h in self.hypotheses],
            "residuals": dict(self.residuals),
            "baselines": {k: asdict(v) for k, v in self.baselines.items()},
            "trusted_tags": sorted(self.trusted_tags),
            "timeline": [asdict(e) for e in self.timeline],
            "notes_seen": list(self.notes_seen),
            "degraded_mode": self.degraded_mode,
        }


# =======================================================================
#  3. AGENT ENVELOPE  --  every model-backed agent returns exactly this
# =======================================================================

@dataclass
class AgentEnvelope:
    """Uniform return type from every agent (Plan §7.2).

    Malformed payload -> one retry with a repair prompt -> then fall back to
    deterministic-only output.  The tick NEVER crashes.

    The `backend` / `model` / `tokens` fields exist purely so the FieldMind
    scheduling study has per-stage data to work with.  Do not remove them
    even when running against the cloud API -- fill them with what you know.
    """
    agent: str                                # "diagnostician" | "verifier"
    tick: int
    status: str = "ok"                        # ok | invalid_schema | timeout | error
    payload: dict[str, Any] = field(default_factory=dict)
    cited_facts: list[str] = field(default_factory=list)
    cited_cases: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    backend: str = "unknown"                  # npu | gpu | cpu | cloud | mock
    model: str = "unknown"
    tokens: dict[str, int] = field(default_factory=lambda: {"prefill": 0, "decode": 0})
    retries: int = 0                          # transient-failure retries this call
    retrieved_cases: list[str] = field(default_factory=list)   # case ids, rank order, as prompted

    # Debugging aids. `prompt_tokens` and `error` are ALWAYS filled (the
    # scheduling study needs the prompt size; a failed call needs its reason).
    # `prompt` and `raw_reply` are only filled when run with --log-prompts,
    # because the full rendered prompt is ~1-3 kB and would bloat every
    # runs_*.json by an order of magnitude on a normal run.
    prompt_tokens: int = 0
    # One entry per backend call (runtime.llm_backend.call_record): tokens and
    # server-side prefill/decode ms. Phase 0b lane-rate telemetry; no decision reads it.
    calls: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    prompt: str = ""                          # rendered prompt, --log-prompts only
    raw_reply: str = ""                       # unparsed model text, --log-prompts only

    def to_dict(self) -> dict:
        return asdict(self)


# =======================================================================
#  4. ASSESSMENT  --  the per-tick output the engineer actually sees
# =======================================================================

@dataclass
class Action:
    """Selected by L6 from the fixed catalogue.  Never authored by a model."""
    id: str
    text: str
    urgency: str                 # NOW | SOON | MONITOR
    source: str = "catalogue"


@dataclass
class Assessment:
    """Exactly the object in Plan §2.1.  This is the benchmark's unit of record."""
    tick: int
    timestamp: str
    state: str
    headline: str
    facts: list[dict] = field(default_factory=list)
    hypotheses: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    escalate: bool = False
    unexplained: list[str] = field(default_factory=list)
    confidence: float = 0.0
    degraded_mode: str | None = None

    # --- telemetry sidecar: not shown to the engineer, used by the sweep ---
    triage: str = "QUIET"
    llm_invoked: bool = False
    envelopes: list[dict] = field(default_factory=list)
    tick_latency_ms: float = 0.0
    deadline_miss: bool = False
    # Deterministic belief ranking (live hypotheses, descending log-odds), logged
    # BEFORE the model is consulted. `hypotheses` above is re-ordered by the
    # model via Orchestrator._merge, so it cannot show the effect of belief-update
    # changes; this field can. Each entry: {case_ref, cause, log_odds, confidence}.
    belief_ranking: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
