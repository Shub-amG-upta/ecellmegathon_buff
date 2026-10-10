"""
One model call for a compact (Phase 2) agent: call, parse, ONE repair retry,
envelope. The same sequence as the single agent's Diagnostician.run
(fieldmind/agent/l4_diagnose.py), with the prompt, the answer check and the
repair template supplied by the caller. Prompt tokens and answer tokens are
recorded for every call (env.calls), repair calls included.
"""

from __future__ import annotations

from ...agent.l4_diagnose import est_tokens, extract_json
from ...runtime.llm_backend import call_record
from ...schemas import AgentEnvelope


def call_model(backend, prompt: str, role: str, max_tokens: int, hint: dict,
               agent: str, tick: int, check, repair_template: str | None = None,
               log_prompts: bool = False) -> tuple[AgentEnvelope, bool]:
    """Returns (envelope, first answer failed the check). `check(payload)`
    returns (ok, why) for a parsed JSON object."""
    reply = backend.generate(prompt, role=role, max_tokens=max_tokens,
                             mock_hint=hint)
    env = AgentEnvelope(agent=agent, tick=tick, latency_ms=reply.latency_ms,
                        backend=reply.backend, model=reply.model,
                        tokens={"prefill": reply.prefill_tokens,
                                "decode": reply.decode_tokens})
    env.retries = getattr(reply, "retries", 0)
    env.calls.append(call_record(reply))
    env.prompt_tokens = reply.prefill_tokens or est_tokens(prompt)
    if log_prompts:
        env.prompt = prompt
        env.raw_reply = reply.text

    if reply.status != "ok":
        env.status = reply.status
        env.error = getattr(reply, "error", "")
        env.payload = {"error": env.error or reply.status}
        return env, False

    def judge(text):
        payload = extract_json(text)
        if not isinstance(payload, dict):
            return None, False, "no JSON found"
        ok, why = check(payload)
        return payload, ok, why

    payload, ok, why = judge(reply.text)
    failed_first = not ok
    if not ok and repair_template is not None:
        reply2 = backend.generate(
            repair_template.format(bad_output=reply.text[:400], reason=why),
            role=role, max_tokens=max_tokens, mock_hint=hint)
        env.calls.append(call_record(reply2))
        env.latency_ms += reply2.latency_ms
        # a count either call did not report stays None (unknown), never 0
        for k, v in (("prefill", reply2.prefill_tokens),
                     ("decode", reply2.decode_tokens)):
            env.tokens[k] = (None if env.tokens[k] is None or v is None
                             else env.tokens[k] + v)
        env.retries += getattr(reply2, "retries", 0)
        if log_prompts:
            env.raw_reply = (env.raw_reply + "\n---REPAIR REPLY---\n"
                             + (reply2.text or ""))
        if reply2.status == "ok":
            payload, ok, why = judge(reply2.text)
            if not ok:
                why = why + " (after repair)"
        else:
            why = f"repair call {reply2.status}"

    if not ok:
        env.status = "invalid_schema"
        env.error = why
        env.payload = {"error": why}
    else:
        env.payload = payload
    return env, failed_first
