"""
AgentEnvelope now carries the rendered prompt, the raw reply, the prompt token
count, the transient-retry count and the error string -- so a failed cloud
diagnosis can actually be debugged from runs_*.json (before this, a failed call
left payload={} and no prompt at all).

    python bench/test_envelope_logging.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml                                                       # noqa: E402

from fieldmind.agent.l4_diagnose import Diagnostician, est_tokens  # noqa: E402
from fieldmind.agent.l5_verify import Verifier                    # noqa: E402
from fieldmind.runtime.llm_backend import LLMReply, MockBackend   # noqa: E402
from fieldmind.schemas import Fact                                # noqa: E402

ROOT = Path(__file__).parent.parent
ACFG = yaml.safe_load((ROOT / "configs/base.yaml").read_text())["agent"]
PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name} {extra}")


FACTS = [Fact("F1", "RATE", ["drum_level"], (0, 10), -2.8,
              "drum_level -2.8 %/min sustained 3 min", "WATCH")]
RETRIEVED = {"cases": [{"case_id": "RCA-01", "score": 0.12, "root_cause": "FCV seizure",
                        "title": "t", "discriminating_evidence": "d"},
                       {"case_id": "RCA-16", "score": 0.10, "root_cause": "BFP",
                        "title": "t", "discriminating_evidence": "d"}],
             "candidates": ["feed_control_valve"], "notes": [], "records": {},
             "experience": [], "operator_query": ""}


class FailingBackend(MockBackend):
    """First N calls return a transient error, then delegate to the mock."""
    name = "failing"

    def __init__(self, fail_times=0, status="error", err="HTTP 429: rate limit"):
        super().__init__()
        self.fail_times = fail_times
        self.status = status
        self.err = err
        self.n = 0

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        self.n += 1
        if self.n <= self.fail_times:
            return LLMReply(text="", status=self.status, backend="cloud",
                            model="x", error=self.err, retries=0)
        r = super().generate(prompt, role, max_tokens, mock_hint)
        # pretend the successful call cost 2 retries to get through the 429s
        r.retries = 2
        return r


# --- 1. logging OFF: token count present, prompt/raw_reply empty ---------
print("logging OFF (default)")
d_off = Diagnostician(MockBackend(), {**ACFG, "log_prompts": False})
env = d_off.run(5, "F1 [RATE/WATCH] level falling", RETRIEVED, FACTS, "no prior")
check("prompt_tokens is filled even with logging off",
      env.prompt_tokens > 0, f"({env.prompt_tokens})")
check("prompt is NOT stored with logging off", env.prompt == "")
check("raw_reply is NOT stored with logging off", env.raw_reply == "")
check("retrieved_cases is always recorded",
      env.retrieved_cases == ["RCA-01", "RCA-16"], f"({env.retrieved_cases})")

# --- 2. logging ON: prompt + raw reply captured ------------------------
print("\nlogging ON (--log-prompts)")
d_on = Diagnostician(MockBackend(), {**ACFG, "log_prompts": True})
env = d_on.run(5, "F1 [RATE/WATCH] level falling", RETRIEVED, FACTS, "no prior")
check("prompt is stored and non-trivial", len(env.prompt) > 200)
check("prompt contains the retrieved case block", "RCA-01" in env.prompt)
check("raw_reply is stored", len(env.raw_reply) > 0)
check("prompt_tokens ~= est_tokens(prompt) when the backend reports 0",
      env.prompt_tokens == est_tokens(env.prompt))

# --- 3. a failed call is debuggable: status + error + payload ----------
print("\nfailed diagnosis is debuggable")
d_fail = Diagnostician(FailingBackend(fail_times=99, err="HTTP 404: dead model"),
                       {**ACFG, "log_prompts": True})
env = d_fail.run(9, "F1 ...", RETRIEVED, FACTS, "no prior")
check("status carries the failure kind", env.status == "error", f"({env.status})")
check("error string is preserved (was silently dropped before)",
      env.error == "HTTP 404: dead model", f"({env.error!r})")
check("payload carries the error instead of being empty {}",
      env.payload.get("error") == "HTTP 404: dead model", f"({env.payload})")
check("the rendered prompt is still captured on a failed call",
      "RCA-01" in env.prompt)

# --- 4. retry accounting --------------------------------------------
print("\nretry accounting")
d_retry = Diagnostician(FailingBackend(fail_times=0), {**ACFG, "log_prompts": False})
env = d_retry.run(3, "F1 ...", RETRIEVED, FACTS, "no prior")
check("env.retries reflects the backend's retry count", env.retries == 2,
      f"({env.retries})")
check("the Diagnostician accumulates retries across calls",
      d_retry.retries == 2, f"({d_retry.retries})")

# --- 5. verifier gets the same treatment ---------------------------
print("\nverifier envelope")
v = Verifier(MockBackend(), {**ACFG, "log_prompts": True})
venv = v.run(4, {"hypotheses": [{"rank": 1, "cause": "c", "confidence": 0.5,
                                 "supports": ["F1"], "discriminator": ""}]}, FACTS)
check("verifier prompt is captured", len(venv.prompt) > 50)
check("verifier prompt_tokens is filled", venv.prompt_tokens > 0)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
