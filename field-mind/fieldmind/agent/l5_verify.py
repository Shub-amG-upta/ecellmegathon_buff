"""
=============================================================================
 L5  --  VERIFIER  (Plan §7.1)
=============================================================================
A SEPARATE CALL, not a self-check.

Asking one model to check its own answer in the same context mostly gets you
agreement. The Verifier is given a deliberately NARROWER view: the claims, and
the raw fact list. IT DOES NOT SEE THE RETRIEVED CASES. Its job is adversarial
and mechanical:

  1. Does every cited fact ID exist, and does the quoted detail match?
  2. Is any claim contradicted by a fact the Diagnostician did not cite?
  3. Is the stated discriminator actually observable in the current window,
     or does it need a tag we do not have?
  4. Given only the facts, is the ranked order defensible?

Failures are NOT thrown away. Verifier disagreement rate is a reportable
metric and it is also the trigger for asking the human rather than asserting.

Runs at INVESTIGATE and above. Under the 'conditional' policy it runs only
when Diagnostician confidence is in a middle band -- large cost saving, and
the risk (unchecked confident errors) is exactly what the sweep measures.
=============================================================================
"""

from __future__ import annotations

from pathlib import Path

from ..runtime.llm_backend import LLMBackend, call_record
from ..schemas import AgentEnvelope, Fact
from .l4_diagnose import extract_json, est_tokens

PROMPT_DIR = Path(__file__).parent / "prompts"


class Verifier:
    def __init__(self, backend: LLMBackend, cfg: dict):
        self.backend = backend
        self.cfg = cfg
        self.log_prompts = bool(cfg.get("log_prompts", False))
        self.template = (PROMPT_DIR / "verifier.txt").read_text()
        self.disagreements = 0
        self.calls = 0
        self.retries = 0

    def should_run(self, triage_level: str, top_confidence: float) -> bool:
        """Policy switch, exposed as decision variable `v` in Plan §11.2."""
        mode = self.cfg.get("verifier", "conditional")   # always|conditional|never
        if mode == "never":
            return False
        if triage_level in ("QUIET", "WATCH"):
            return False
        if mode == "always":
            return True
        lo, hi = self.cfg.get("verifier_band", [0.35, 0.75])
        return lo <= top_confidence <= hi

    def run(self, tick: int, claims: dict, facts: list[Fact]) -> AgentEnvelope:
        self.calls += 1
        # Only the claims and the raw facts. No cases, no notes, no world model.
        fact_block = "\n".join(f"{f.id} [{f.check}/{f.severity}] {f.detail}"
                               for f in facts)
        claim_block = "\n".join(
            f"{h.get('rank', i+1)}. cause={h['cause']} conf={h['confidence']} "
            f"cites={h.get('supports', [])} discriminator={h.get('discriminator','')}"
            for i, h in enumerate(claims.get("hypotheses", [])))

        prompt = self.template.format(facts=fact_block, claims=claim_block)
        reply = self.backend.generate(prompt, role="verifier",
                                      max_tokens=self.cfg["max_tokens"],
                                      mock_hint={"claims": claims})

        env = AgentEnvelope(agent="verifier", tick=tick,
                            latency_ms=reply.latency_ms, backend=reply.backend,
                            model=reply.model,
                            tokens={"prefill": reply.prefill_tokens,
                                    "decode": reply.decode_tokens})
        env.retries = getattr(reply, "retries", 0)
        self.retries += env.retries
        env.calls.append(call_record(reply))
        env.prompt_tokens = reply.prefill_tokens or est_tokens(prompt)
        if self.log_prompts:
            env.prompt = prompt
            env.raw_reply = reply.text

        if reply.status != "ok":
            env.status = reply.status
            env.error = getattr(reply, "error", "")
            env.payload = {"error": env.error or reply.status}
            return env

        payload = extract_json(reply.text)
        if not isinstance(payload, dict):
            env.status = "invalid_schema"
            env.error = "no JSON found"
            env.payload = {"error": "no JSON found"}
            return env

        env.payload = payload
        if payload.get("agree") is False:
            self.disagreements += 1
        return env

    # -------------------------------------------------------------------
    @staticmethod
    def apply(claims: dict, verdict: dict) -> dict:
        """Fold the verifier's result back into the hypotheses.

        A failed check does not delete the hypothesis -- it caps its confidence
        and records why. Deleting would hide the disagreement, and the
        disagreement is the measurement.
        """
        if not verdict:
            return claims
        revised = verdict.get("revised_confidence")
        failed = {c.get("claim") for c in verdict.get("checks", [])
                  if c.get("verdict") == "fail"}
        for h in claims.get("hypotheses", []):
            if h["cause"] in failed:
                h["confidence"] = min(float(h["confidence"]), 0.35)
                h["verifier"] = "failed check"
            elif revised is not None and h.get("rank") == 1:
                h["confidence"] = float(revised)
                h["verifier"] = "confidence revised"
        if verdict.get("strongest_contradiction"):
            claims.setdefault("unexplained", []).append(
                f"verifier contradiction: {verdict['strongest_contradiction']}")
        return claims
