"""
=============================================================================
 L4  --  DIAGNOSTICIAN  (Plan §7, prompt skeleton in Appendix A.6)
=============================================================================
The first of only two stages that run a language model.

Its job is the one thing a model is actually good at here: weighing several
explanations against messy evidence. Its weakness (making things up) is boxed
in on both sides -- L1 supplies the facts, L6 approves the actions, and the
candidate cause list comes from the plant topology so it cannot invent a
component that does not exist.

JSON reliability: LiteRT-LM may not expose grammar-constrained decoding
(Plan §10.3, open question). So the path here is
    few-shot + strict parse + ONE repair retry + deterministic fallback,
and the PARSE FAILURE RATE IS A REPORTED METRIC, not a swallowed error.
=============================================================================
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..runtime.llm_backend import LLMBackend, call_record
from ..schemas import AgentEnvelope, Fact

PROMPT_DIR = Path(__file__).parent / "prompts"


def _load(name: str) -> str:
    return (PROMPT_DIR / name).read_text()


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply.

    Handles the three things small models do: wrap in ```json fences, emit a
    preamble sentence, or emit trailing prose. Anything else returns None and
    goes down the repair path.
    """
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None


def validate(payload: dict) -> tuple[bool, str]:
    """Schema check. Cheap, strict, and it runs before anything downstream."""
    if not isinstance(payload, dict):
        return False, "not an object"
    if "hypotheses" not in payload or not isinstance(payload["hypotheses"], list):
        return False, "missing hypotheses list"
    for h in payload["hypotheses"]:
        for k in ("cause", "confidence", "supports"):
            if k not in h:
                return False, f"hypothesis missing {k}"
        if not isinstance(h["supports"], list):
            return False, "supports must be a list of fact ids"
        try:
            c = float(h["confidence"])
        except (TypeError, ValueError):
            return False, "confidence not numeric"
        if not 0.0 <= c <= 1.0:
            return False, "confidence out of range"
    return True, ""


def est_tokens(text: str) -> int:
    """Cheap prompt-size estimate when the backend does not report one (a
    failed call, or the device path before it parses litert's timing block).
    ~4 chars/token is close enough for the scheduling study's bucketing."""
    return (len(text) + 3) // 4


class Diagnostician:
    def __init__(self, backend: LLMBackend, cfg: dict):
        self.backend = backend
        self.cfg = cfg
        self.log_prompts = bool(cfg.get("log_prompts", False))
        self.template = _load("diagnostician.txt")
        self.repair = _load("repair.txt")
        self.parse_failures = 0
        self.calls = 0
        self.retries = 0

    # -------------------------------------------------------------------
    def build_prompt(self, evidence: str, retrieved: dict,
                     wm_summary: str) -> str:
        cases = "\n".join(
            f"- {c['case_id']} ({c['score']:.2f}) {c.get('title','')}: "
            f"cause={c.get('root_cause','?')}; "
            f"discriminator={c.get('discriminating_evidence','?')}"
            for c in retrieved.get("cases", []))

        for e in retrieved.get("experience", []):
            # Unverified agent records are LABELLED in the prompt. The model is
            # told how much to trust them rather than being left to assume.
            tag = "unconfirmed" if e.get("unverified") else "confirmed"
            cases += (f"\n- {e.get('case_id','EXP')} [own past episode, {tag}, "
                      f"seen {e.get('occurrences',1)}x] cause={e.get('root_cause','?')}")

        # Notes are wrapped in an explicit boundary marker and declared as DATA.
        # Two of the thirty episodes contain instruction-like text on purpose
        # (Plan §3.6); deflection is scored under T9.
        notes = "\n".join(f"- [{n.get('author','?')}] {n['text']}"
                          for n in retrieved.get("notes", [])) or "(none relevant)"

        # records.json (known bug 3): coal lab report, maintenance history and
        # the boiler-water conductivity trend -- the last is the leak vs
        # blow-down vs feed-fault discriminator in several RCA cases.
        rv = retrieved.get("records", {}) or {}
        rlines = []
        if rv.get("coal_lab_report"):
            rlines.append(f"- coal lab report: {rv['coal_lab_report']}")
        for m in rv.get("maintenance_history", []):
            rlines.append(f"- maintenance: {m}")
        if rv.get("boiler_water_conductivity"):
            rlines.append(f"- boiler water conductivity: {rv['boiler_water_conductivity']}")
        for a in rv.get("alarm_log", []):
            rlines.append(f"- alarm log: {a}")
        records = "\n".join(rlines) or "(no records available)"

        return self.template.format(
            operator_question=retrieved.get("operator_query") or "(none stated)",
            facts=evidence,
            candidates=", ".join(retrieved.get("candidates", [])) or "(none)",
            cases=cases or "(no similar case retrieved)",
            notes=notes,
            records=records,
            world=wm_summary)

    # -------------------------------------------------------------------
    def run(self, tick: int, evidence: str, retrieved: dict, facts: list[Fact],
            wm_summary: str) -> AgentEnvelope:
        prompt = self.build_prompt(evidence, retrieved, wm_summary)
        self.calls += 1

        # mock_hint is ignored by gemini/litert. It exists only so the mock
        # backend can produce schema-valid output for plumbing tests.
        hint = {"cases": retrieved.get("cases", []),
                "facts": [f.to_dict() for f in facts],
                "candidates": retrieved.get("candidates", []),
                "headline": ""}

        reply = self.backend.generate(prompt, role="diagnostician",
                                      max_tokens=self.cfg["max_tokens"],
                                      mock_hint=hint)

        env = AgentEnvelope(agent="diagnostician", tick=tick,
                            latency_ms=reply.latency_ms, backend=reply.backend,
                            model=reply.model,
                            tokens={"prefill": reply.prefill_tokens,
                                    "decode": reply.decode_tokens})
        env.retries = getattr(reply, "retries", 0)
        self.retries += env.retries
        env.calls.append(call_record(reply))
        env.prompt_tokens = reply.prefill_tokens or est_tokens(prompt)
        env.retrieved_cases = [c.get("case_id") for c in retrieved.get("cases", [])]
        if self.log_prompts:
            env.prompt = prompt
            env.raw_reply = reply.text

        if reply.status != "ok":
            env.status = reply.status          # timeout / error, recorded not hidden
            env.error = getattr(reply, "error", "")
            env.payload = {"error": env.error or reply.status}
            return env

        payload = extract_json(reply.text)
        ok, why = validate(payload) if payload else (False, "no JSON found")

        # ---- ONE repair retry, then give up and let the tick degrade ----
        if not ok:
            self.parse_failures += 1
            repair_prompt = self.repair.format(bad_output=reply.text[:800], reason=why)
            reply2 = self.backend.generate(repair_prompt, role="diagnostician",
                                           max_tokens=self.cfg["max_tokens"],
                                           mock_hint=hint)
            env.calls.append(call_record(reply2))
            payload = extract_json(reply2.text)
            ok, why = validate(payload) if payload else (False, "no JSON after repair")
            env.latency_ms += reply2.latency_ms
            # a count either call did not report stays None (unknown), never 0
            for k, v in (("prefill", reply2.prefill_tokens),
                         ("decode", reply2.decode_tokens)):
                env.tokens[k] = (None if env.tokens[k] is None or v is None
                                 else env.tokens[k] + v)
            env.retries += getattr(reply2, "retries", 0)
            self.retries += getattr(reply2, "retries", 0)
            if self.log_prompts:
                env.raw_reply = (env.raw_reply + "\n---REPAIR REPLY---\n"
                                 + (reply2.text or ""))

        if not ok:
            env.status = "invalid_schema"
            env.error = why
            env.payload = {"error": why}
            return env

        env.payload = payload
        env.cited_facts = sorted({s for h in payload["hypotheses"]
                                  for s in h.get("supports", [])})
        env.cited_cases = sorted({h["case_ref"] for h in payload["hypotheses"]
                                  if h.get("case_ref")})
        return env
