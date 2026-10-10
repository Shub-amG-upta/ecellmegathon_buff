"""
Type router (v7): a second model decides the incident type; the main model does everything else.

On the board (DECISION_v6.md), Gemma 3 4B chose the incident type right 8 times where Llama 3.2 3B was wrong,
and never the reverse (slip vs fall from height, struck-by, chemical vs gas). Llama was better at everything
else. So: Llama answers the turn as before; when its answer **gives a type** and the form has **no type yet**
(the first description), the same prompt goes to the type model with an answer constrained to {"t": <one of
the 15 types>}, and that type replaces Llama's. The type model never adds a type Llama didn't give, never
touches another field, and if it fails, Llama's answer stands.

Cost: one extra short call (~4 output tokens) once per report. Start the type model's llama-server with
--swa-full (Gemma 3 uses sliding-window attention; without it the cached prompt isn't reused: 660 ms prefill
on the board instead of ~100 ms).

  TYPE_MODEL=<gguf file name>   turn it on (app/server.py make_backend); off when unset
  TYPE_MODEL_URL                default http://127.0.0.1:8084 (board.board lane "type")

Stdlib only.
"""
from __future__ import annotations

import json
import re

from app.backends import LLMReply
from app.form import to_tool_call

_FORM = re.compile(r"^Form: (\{.*\})$", re.M)


def type_schema(types: list[str]) -> dict:
    return {"type": "object", "properties": {"t": {"type": "string", "enum": types}},
            "required": ["t"], "additionalProperties": False}


class TypeRouter:
    def __init__(self, main, type_backend, types: list[str], max_tokens: int = 16):
        self.main, self.type_backend, self.types, self.max_tokens = main, type_backend, types, max_tokens
        self.lane = getattr(main, "lane", "?")
        self.model_file = getattr(main, "model_file", "?")
        self.type_model = getattr(type_backend, "model_file", "?")
        self.calls = self.overrides = self.changed = self.failures = 0

    @staticmethod
    def _form_has_type(prompt: str) -> bool:
        m = _FORM.search(prompt)
        try:
            return bool(m and json.loads(m.group(1)).get("t"))
        except ValueError:
            return False

    def generate(self, prompt, max_tokens=128, grammar=None, json_schema=None, system=None):
        reply = self.main.generate(prompt, max_tokens=max_tokens, grammar=grammar, json_schema=json_schema,
                                   system=system)
        if reply.status != "ok" or self._form_has_type(prompt):
            return reply
        try:
            parsed = json.loads(reply.text)
        except ValueError:
            return reply
        call = to_tool_call(parsed)
        if not call or call.get("tool") != "update_report" or not (call.get("args") or {}).get("t"):
            return reply                                   # only replace a type the main model gave
        self.calls += 1
        tr = self.type_backend.generate(prompt, max_tokens=self.max_tokens, json_schema=type_schema(self.types),
                                        system=system)
        part = {"model": tr.model, "status": tr.status, "wall_ms": tr.latency_ms, "prefill_ms": tr.prefill_ms,
                "decode_ms": tr.decode_ms, "prefill_tokens": tr.prefill_tokens, "decode_tokens": tr.decode_tokens,
                "main_t": call["args"]["t"], "type_t": None}
        new_t = None
        if tr.status == "ok":
            try:
                new_t = json.loads(tr.text).get("t")
            except (ValueError, AttributeError):
                new_t = None
        if new_t not in self.types:
            self.failures += 1
            return _with_part(reply, part)                 # the type model failed: the main answer stands
        part["type_t"] = new_t
        self.overrides += 1
        if new_t != call["args"]["t"]:
            self.changed += 1
        call["args"]["t"] = new_t
        text = json.dumps(call if "tool" in parsed else call["args"], ensure_ascii=False, separators=(",", ":"))
        merged = _with_part(reply, part, text)
        for k in ("latency_ms", "prefill_ms", "decode_ms", "prefill_tokens", "decode_tokens"):
            a, b = getattr(reply, k), getattr(tr, k)
            setattr(merged, k, (a or 0) + (b or 0) if a is not None or b is not None else None)
        return merged

    def summary(self) -> dict:
        return {"type_model": self.type_model, "calls": self.calls, "overrides": self.overrides,
                "changed": self.changed, "failures": self.failures}


def _with_part(reply: LLMReply, part: dict, text: str | None = None) -> LLMReply:
    out = LLMReply(**{k: getattr(reply, k) for k in reply.__dataclass_fields__})
    if text is not None:
        out.text = text
    out.parts = list(reply.parts or []) + [part]
    return out
