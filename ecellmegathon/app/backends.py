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
    parts: list | None = None         # extra calls folded into this reply (v7 type router), else None


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
        self._templates: dict = {}        # system prompt -> (head, tail) of the rendered chat template

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

    # ---- HTTP -----------------------------------------------------------------------------------------------
    def _post(self, path: str, body: dict):
        """(data, None) on success, or (None, (status, error)) on failure."""
        req = urllib.request.Request(f"{self.url}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            return None, ("error", f"HTTP {e.code}: {e.read()[:200]!r}")
        except (TimeoutError, socket.timeout):
            return None, ("timeout", f"llama-server exceeded {self.timeout_s}s")
        except urllib.error.URLError as e:
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                return None, ("timeout", f"llama-server exceeded {self.timeout_s}s")
            return None, ("error", f"connection: {e.reason}")
        try:
            return json.loads(raw), None
        except ValueError as e:
            return None, ("error", f"malformed reply ({e!r}): {raw[:200]}")

    # ---- answer pre-fill (PREFILL=on) ------------------------------------------------------------------------
    _BOS = ("<|begin_of_text|>", "<bos>", "<s>")

    def _template(self, system: str | None):
        """The chat template rendered by llama-server around a sentinel user message, split into (head, tail)
        and cached per system prompt: one /apply-template call per session, not per turn. The BOS text is
        dropped because /completion adds it when it tokenizes the prompt."""
        key = system or ""
        if key not in self._templates:
            sentinel = "\u2063USER\u2063"
            messages = ([{"role": "system", "content": system}] if system else []) + \
                       [{"role": "user", "content": sentinel}]
            body = {"messages": messages}
            if self.thinking is not None:
                body["chat_template_kwargs"] = {"enable_thinking": self.thinking}
            data, err = self._post("/apply-template", body)
            if err or not isinstance(data, dict) or sentinel not in str(data.get("prompt", "")):
                return None
            prompt = data["prompt"]
            for bos in self._BOS:
                if prompt.startswith(bos):
                    prompt = prompt[len(bos):]
                    break
            head, tail = prompt.split(sentinel, 1)
            self._templates[key] = (head, tail)
        return self._templates[key]

    def generate(self, prompt: str, max_tokens: int = 128, grammar: str | None = None,
                 json_schema: dict | None = None, system: str | None = None,
                 prefix: str | None = None) -> LLMReply:
        """One completion. With `prefix`, the answer's fixed start is written into the prompt (read in one
        fast batch) instead of being decoded token by token; `grammar` then constrains only the rest, and the
        returned text is prefix + the model's continuation."""
        t0 = time.perf_counter()

        def fail(status: str, err: str) -> LLMReply:
            return LLMReply(text="", status=status, backend=self.lane,
                            model=self.model_file, error=err[:300],
                            latency_ms=(time.perf_counter() - t0) * 1000)

        if prefix is not None:
            tpl = self._template(system)
            if tpl is None:
                return fail("error", "llama-server /apply-template unavailable for the answer pre-fill")
            body = {"prompt": tpl[0] + prompt + tpl[1] + prefix, "n_predict": max_tokens,
                    "temperature": self.temperature, "seed": self.seed, "cache_prompt": self.cache_prompt,
                    "stream": False}
            if grammar is not None:
                body["grammar"] = grammar
            data, err = self._post("/completion", body)
            if err:
                return fail(*err)
            try:
                text = prefix + (data["content"] or "")
            except (KeyError, TypeError) as e:
                return fail("error", f"malformed reply ({e!r}): {str(data)[:200]}")
        else:
            data, err = self._post("/v1/chat/completions",
                                   self._body(prompt, max_tokens, grammar, json_schema, system))
            if err:
                return fail(*err)
            try:
                text = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as e:
                return fail("error", f"malformed reply ({e!r}): {str(data)[:200]}")
        latency = (time.perf_counter() - t0) * 1000
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
