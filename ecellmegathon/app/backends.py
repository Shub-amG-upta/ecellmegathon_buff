"""
LLM backend: the one place that knows how the model is called.

Ported from FieldMind (fieldmind/runtime/llm_backend.py, LlamaServerBackend),
which ran it against llama-server on the QIDK's NPU (HTP0). Dropped from the
original: the Gemini backend (no cloud APIs in this project) and the LiteRT
backend (never used for a result).

Everything else calls

        reply = backend.generate(prompt, grammar=..., json_schema=...)

and gets an `LLMReply`. Every timing comes from the server's own `timings`
block; a field the server did not send is None, never a guess.

Stdlib only, so the core can move onto the device.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class LLMReply:
    text: str                       # raw model output, unparsed
    status: str = "ok"              # ok | timeout | error
    latency_ms: float = 0.0         # client wall clock for the whole request
    backend: str = "unknown"        # npu | cpu | mock
    model: str = "unknown"
    # None = the server did not report it. Consumers skip None, never treat it as 0.
    prefill_tokens: int | None = None
    decode_tokens: int | None = None
    prefill_ms: float | None = None   # server-side prefill time (llama-server prompt_ms)
    decode_ms: float | None = None    # server-side decode time (llama-server predicted_ms)
    error: str = ""


class LlamaServerBackend:
    """Talks HTTP to a persistent `llama-server` (OpenAI-style chat endpoint).

    The model stays loaded across calls. On the board, start the server with
    board/board.py (NPU: `--device HTP0 -ngl 99`, port 8080) and `adb forward`
    the port; then url = http://localhost:8080.

    Constraining the answer (pass at most one per call):
      grammar      GBNF text (app/grammar.py builds it)
      json_schema  a JSON schema; llama-server turns it into a grammar itself

    `cache_prompt` defaults to False so prefill_tokens counts every prompt
    token. Turn it on for the demo: the fixed instruction prefix is then reused
    across turns and prefill gets cheaper (measure it before claiming it).
    """

    name = "llamaserver"

    def __init__(self, url: str = "http://localhost:8080", lane: str = "npu",
                 model_file: str = "unknown", temperature: float = 0.0,
                 seed: int = 0, timeout_s: float = 60.0,
                 cache_prompt: bool = False, thinking: bool | None = None):
        self.url = url.rstrip("/")
        self.lane = lane
        self.model_file = model_file
        self.temperature = temperature
        self.seed = seed
        self.timeout_s = timeout_s
        self.cache_prompt = cache_prompt
        # Qwen3 only: False sends chat_template_kwargs enable_thinking=false.
        # Without it Qwen3 spent a 400-token budget on reasoning and returned
        # empty content (FieldMind smoke test D, BOARD). None = send nothing.
        self.thinking = thinking

    def _body(self, prompt: str, max_tokens: int, grammar: str | None,
              json_schema: dict | None, system: str | None) -> dict:
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
        body = {"messages": messages,
                "max_tokens": max_tokens,
                "temperature": self.temperature,
                "seed": self.seed,
                "cache_prompt": self.cache_prompt,
                "stream": False}
        if grammar is not None and json_schema is not None:
            raise ValueError("pass grammar or json_schema, not both")
        if grammar is not None:
            body["grammar"] = grammar
        if json_schema is not None:
            body["json_schema"] = json_schema
        if self.thinking is not None:
            body["chat_template_kwargs"] = {"enable_thinking": self.thinking}
        return body

    def generate(self, prompt: str, max_tokens: int = 128, grammar: str | None = None,
                 json_schema: dict | None = None, system: str | None = None) -> LLMReply:
        req = urllib.request.Request(
            f"{self.url}/v1/chat/completions",
            data=json.dumps(self._body(prompt, max_tokens, grammar, json_schema,
                                       system)).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.perf_counter()

        def fail(status: str, err: str) -> LLMReply:
            return LLMReply(text="", status=status, backend=self.lane,
                            model=self.model_file, error=err[:300],
                            latency_ms=(time.perf_counter() - t0) * 1000)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            return fail("error", f"HTTP {e.code}: {e.read()[:200]!r}")
        except (TimeoutError, socket.timeout):
            return fail("timeout", f"llama-server exceeded {self.timeout_s}s")
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                return fail("timeout", f"llama-server exceeded {self.timeout_s}s")
            return fail("error", f"connection: {e.reason}")
        latency = (time.perf_counter() - t0) * 1000

        try:
            data = json.loads(raw)
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            return fail("error", f"malformed reply ({e!r}): {raw[:200]}")

        tm = data.get("timings") or {}

        def num(key, cast):
            v = tm.get(key)
            return cast(v) if isinstance(v, (int, float)) else None

        return LLMReply(
            text=text, backend=self.lane,
            model=data.get("model") or self.model_file,
            latency_ms=latency,
            prefill_tokens=num("prompt_n", int),
            decode_tokens=num("predicted_n", int),
            prefill_ms=num("prompt_ms", float),
            decode_ms=num("predicted_ms", float),
        )


class MockBackend:
    """Returns a fixed reply, for building the dialog loop and UI without the
    board. Not a quality baseline: never report a number produced with it."""

    name = "mock"

    def __init__(self, reply: str = '{"tool":"no_op"}'):
        self.reply = reply

    def generate(self, prompt: str, max_tokens: int = 128, grammar: str | None = None,
                 json_schema: dict | None = None, system: str | None = None) -> LLMReply:
        return LLMReply(text=self.reply, backend="mock", model="mock")
