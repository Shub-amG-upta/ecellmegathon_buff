"""
=============================================================================
 LLM BACKEND  --  the one place that knows how a model is actually called
=============================================================================

THIS IS THE SWITCHABLE PART.

Everything else in the codebase (l4_diagnose, l5_verify) only ever calls:

        reply = backend.generate(prompt, role="diagnostician")

and gets back an `LLMReply`.  It has no idea whether that prompt went to a
Google datacentre or to the Hexagon NPU eight centimetres away.  That is the
whole point: you develop the agent logic against the cloud API where iteration
is fast, then flip one line in configs/base.yaml and the identical pipeline
runs offline on the QIDK.

    llm:
      backend: mock      <- no network, no key, deterministic. Default.
      backend: gemini    <- Google AI Studio API. For development.
      backend: litert    <- LiteRT-LM on the QIDK. For the real result.

Three implementations, one interface:

    +----------------+-------------------------------+-------------------+
    | MockBackend    | no network, instant, canned   | CI / unit tests   |
    | GeminiBackend  | Google AI Studio HTTP API     | development       |
    | LiteRTBackend  | litert_lm_main over adb/local | the QIDK result   |
    | LlamaServer..  | persistent llama-server, HTTP | QIDK lanes (0b)   |
    +----------------+-------------------------------+-------------------+

Adding a fourth (Qualcomm Genie, llama.cpp, Ollama) means writing one class
with one method.  Nothing downstream changes.
=============================================================================
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field


# =======================================================================
#  The uniform reply object
# =======================================================================

@dataclass
class LLMReply:
    """What every backend must return.

    The token counts and `backend` string are not decoration -- they feed the
    per-stage cost model in the FieldMind scheduling study (Plan §11).  Cloud
    backends fill in what the API tells them; the device backend parses them
    out of litert_lm_main's own timing output.
    """
    text: str                       # raw model output, unparsed
    status: str = "ok"              # ok | timeout | error
    latency_ms: float = 0.0
    backend: str = "unknown"        # npu | gpu | cpu | cloud | mock
    model: str = "unknown"
    # None = the runtime did not report it (LlamaServerBackend). Never filled
    # with a guess; consumers must skip None, not treat it as 0.
    prefill_tokens: int | None = 0
    decode_tokens: int | None = 0
    ttft_ms: float | None = 0.0     # time to first token, headline metric on device
    decode_ms: float | None = None  # server-side decode time (llama-server predicted_ms)
    error: str = ""
    retries: int = 0               # transient-failure retries spent on this call


def call_record(reply: LLMReply) -> dict:
    """One backend call as the envelope's `calls` entry: per-call token counts
    and server-side times, so lane rates and the answer-length distribution are
    computed per call (a repaired diagnosis makes two calls). Telemetry only."""
    return {"status": reply.status, "prefill": reply.prefill_tokens,
            "decode": reply.decode_tokens, "prefill_ms": reply.ttft_ms,
            "decode_ms": getattr(reply, "decode_ms", None),
            "latency_ms": reply.latency_ms}


class LLMBackend:
    """Interface. Subclass this to add a runtime."""

    name = "base"

    def generate(self, prompt: str, role: str = "generic",
                 max_tokens: int = 512, mock_hint: dict | None = None) -> LLMReply:
        """Run one completion.

        role       : which agent is calling ("diagnostician" / "verifier").
                     Used for per-stage telemetry, and lets a backend route
                     different stages to different accelerators later.
        max_tokens : decode budget.  On the NPU this interacts with the static
                     shape chosen at compile time -- see Plan §10.3.
        mock_hint  : IGNORED by every real backend.  It exists so MockBackend
                     can produce a sensible answer without a model.  Never put
                     anything load-bearing in here.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release the model.  On device this actually matters (Plan §10.1:
        all models must stay resident, so we do NOT close between ticks)."""
        pass


# =======================================================================
#  1. MOCK  --  runs anywhere, no key, no device, no network
# =======================================================================

class MockBackend(LLMBackend):
    """A stand-in that returns schema-valid JSON derived from the facts it was
    given.  Purpose: let the whole tick pipeline, the harness and the evaluator
    be developed and unit-tested with zero external dependencies.

    It is NOT a quality baseline.  It picks the top retrieved case and echoes
    it back, so any accuracy number produced under the mock backend measures
    the retrieval layer, not a language model.  The evaluator labels runs with
    the backend name for exactly this reason -- never report a mock number as
    an agent result.
    """

    name = "mock"

    def __init__(self, latency_ms: float = 5.0, **_):
        self.latency_ms = latency_ms

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        t0 = time.perf_counter()
        hint = mock_hint or {}

        if role == "diagnostician":
            payload = self._mock_diagnosis(hint)
        elif role == "verifier":
            payload = self._mock_verification(hint)
        elif role == "text_reader":
            payload = self._mock_text_read(hint)
        else:
            payload = {"note": "mock backend, no role-specific behaviour"}

        text = json.dumps(payload)
        # Rough token accounting so the telemetry plumbing is exercised.
        return LLMReply(
            text=text,
            latency_ms=(time.perf_counter() - t0) * 1000 + self.latency_ms,
            backend="mock", model="mock-0",
            prefill_tokens=len(prompt) // 4,
            decode_tokens=len(text) // 4,
            ttft_ms=self.latency_ms * 0.5,
        )

    @staticmethod
    def _mock_diagnosis(hint: dict) -> dict:
        """Rank the retrieved cases by their retrieval score. No reasoning."""
        if hint.get("compact"):
            return MockBackend._mock_diagnosis_compact(hint)
        cases = hint.get("cases", [])
        facts = [f["id"] for f in hint.get("facts", [])]
        candidates = hint.get("candidates", [])
        hyps = []
        for i, c in enumerate(cases[:3]):
            hyps.append({
                "rank": i + 1,
                "cause": c.get("root_cause", "unknown"),
                "confidence": round(max(0.15, c.get("score", 0.5) - 0.1 * i), 2),
                "supports": facts[:2],
                "case_ref": c.get("case_id"),
                "discriminator": c.get("discriminating_evidence", ""),
            })
        if not hyps and candidates:
            hyps = [{"rank": 1, "cause": f"Fault at or upstream of {candidates[0]}",
                     "confidence": 0.3, "supports": facts[:1],
                     "case_ref": None, "discriminator": "no similar case retrieved"}]
        return {
            "headline": hint.get("headline", "Deviation detected."),
            "hypotheses": hyps,
            "unexplained": [],
        }

    @staticmethod
    def _mock_diagnosis_compact(hint: dict) -> dict:
        """The same rule as _mock_diagnosis, answered in format B' (multi-agent
        Phase 2): the top 3 retrieved cases BY SCORE, each citing the tick's
        first two facts, all as line numbers looked up in the hint's line map
        (so a shuffled case order still gives the same ranking). A fact that
        is not on a shown line is not cited. No confidences, no notes, nothing
        unexplained. It does not read the prompt."""
        lines = hint.get("case_lines", [])
        flines = hint.get("fact_lines", [])
        cites = [flines.index(f) + 1 for f in hint.get("facts", [])[:2]
                 if f in flines]
        r = [[lines.index(cid) + 1, list(cites)]
             for cid in hint.get("cases_by_score", [])[:3] if cid in lines]
        letters = hint.get("letters", [])
        g = letters[r[0][0] - 1] if r and letters else ""
        return {"g": g, "r": r, "sep": r[0][0] if r else None, "n": [], "x": []}

    @staticmethod
    def _mock_text_read(hint: dict) -> dict:
        """Echo the note's own metadata in the text reader's answer format
        (multi-agent Phase 2). It does not read the note text, so it says
        nothing about text-reader quality."""
        note = hint.get("note", {})
        tags = [t for t in note.get("tags", [])][:3]
        kind = "INSTR" if note.get("injection") else ("OBS" if tags else "OTHER")
        return {"k": kind, "s": [[t, "MENTIONED"] for t in tags]}

    @staticmethod
    def _mock_verification(hint: dict) -> dict:
        """Always agrees. A real verifier must not -- that is why the mock is
        useless as a quality baseline and fine as a plumbing test. In the
        compact format (multi-agent Phase 2) it passes every shown claim."""
        if hint.get("compact_ver"):
            return {"v": [[i, "p"] for i in range(1, hint.get("n_claims", 0) + 1)],
                    "c": None}
        return {"checks": [], "strongest_contradiction": None,
                "revised_confidence": None, "agree": True}


# =======================================================================
#  2. GEMINI  --  Google AI Studio, for development before the board is ready
# =======================================================================

class GeminiBackend(LLMBackend):
    """Calls the Google Generative Language REST API over plain HTTPS.

    Uses urllib from the stdlib on purpose -- no google-generativeai SDK
    dependency, so `pip install -r requirements.txt` stays tiny and there is
    one less thing to strip out before pushing to the device.

    Key comes from an environment variable, never from the config file:
        export GOOGLE_API_KEY="..."

    NOTE FOR THE WRITEUP: numbers produced through this backend are cloud
    numbers.  They are useful for measuring AGENT QUALITY (Q1-Q7) while the
    device path is still being built, and useless for SYSTEM metrics (S1-S7).
    Never mix them in one table.
    """

    name = "gemini"

    def __init__(self, model: str = "gemini-flash-lite-latest",
                 api_key_env: str = "GOOGLE_API_KEY",
                 temperature: float = 0.1,
                 timeout_s: float = 30.0,
                 json_mode: bool = True,
                 max_retries: int = 5,
                 backoff_base_s: float = 2.0,
                 backoff_cap_s: float = 60.0,
                 min_interval_s: float = 0.0, **_):
        self.model = model
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.json_mode = json_mode
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_cap_s = backoff_cap_s
        # Proactive client-side spacing between calls. The free tier is ~15 RPM
        # and a full-episode run makes calls back-to-back, so without this every
        # call 429s and the retry loop turns a 5-minute run into an hour.
        # Set from config (llm.gemini.min_interval_s) when running against the
        # rate-limited tier.
        self.min_interval_s = min_interval_s
        self._next_ok = 0.0
        self.api_key = os.environ.get(api_key_env, "")
        if not self.api_key:
            raise RuntimeError(
                f"{api_key_env} is not set. Either export it, or set "
                f"llm.backend: mock in configs/base.yaml."
            )

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        import random
        import urllib.request
        import urllib.error

        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        # Structured output. This is the cloud equivalent of grammar-constrained
        # decoding. LiteRT-LM may not offer it (Plan §10.3, open question) --
        # which is precisely why l4_diagnose still has a repair-retry path that
        # works without it. Do not let the pipeline come to depend on this.
        if self.json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"

        req_data = json.dumps(body).encode()
        headers = {"Content-Type": "application/json",
                   "x-goog-api-key": self.api_key}

        if self.min_interval_s:
            gap = self._next_ok - time.time()
            if gap > 0:
                time.sleep(gap)
            self._next_ok = time.time() + self.min_interval_s

        # ---- retry loop: 429 (rate limit) and 5xx (transient server) and
        #      network drops (timeout, SSL EOF) get exponential backoff with
        #      jitter, honouring Retry-After when the server sends it. A 4xx
        #      that is NOT 429 (e.g. 404 dead model, 400 bad request) is
        #      permanent and returns immediately.
        t_all = time.perf_counter()
        retries = 0
        last_err = "unknown"
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            req = urllib.request.Request(url, data=req_data, headers=headers,
                                         method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    data = json.loads(resp.read().decode())
                break                                          # success
            except urllib.error.HTTPError as e:
                payload = e.read()[:300]
                last_err = f"HTTP {e.code}: {payload!r}"
                transient = e.code == 429 or 500 <= e.code < 600
                if not transient or attempt == self.max_retries:
                    return LLMReply(
                        text="", status="error", backend="cloud",
                        model=self.model, retries=retries,
                        latency_ms=(time.perf_counter() - t_all) * 1000,
                        error=last_err)
                ra = e.headers.get("Retry-After") if e.headers else None
                wait = (float(ra) if ra and str(ra).isdigit()
                        else min(self.backoff_cap_s,
                                 self.backoff_base_s * (2 ** attempt)))
                wait += random.uniform(0, 0.5 * wait)
            except Exception as e:                              # timeout, SSL, DNS
                last_err = str(e)
                if attempt == self.max_retries:
                    return LLMReply(
                        text="", status="timeout", backend="cloud",
                        model=self.model, retries=retries,
                        latency_ms=(time.perf_counter() - t_all) * 1000,
                        error=last_err)
                wait = min(self.backoff_cap_s,
                           self.backoff_base_s * (2 ** attempt))
                wait += random.uniform(0, 0.5 * wait)
            retries += 1
            time.sleep(wait)

        latency = (time.perf_counter() - t_all) * 1000
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            # Safety filter or empty candidate. Treat as a schema failure so the
            # normal repair/fallback path handles it.
            return LLMReply(text="", status="error", backend="cloud",
                            model=self.model, latency_ms=latency, retries=retries,
                            error=f"no candidate: {json.dumps(data)[:200]}")

        usage = data.get("usageMetadata", {})
        return LLMReply(
            text=text, backend="cloud", model=self.model, latency_ms=latency,
            retries=retries,
            prefill_tokens=usage.get("promptTokenCount", 0),
            decode_tokens=usage.get("candidatesTokenCount", 0),
            ttft_ms=0.0,   # not exposed by the non-streaming endpoint
        )


# =======================================================================
#  3. LITERT  --  Gemma3-1B on the QIDK.  The result that actually counts.
# =======================================================================

class LiteRTBackend(LLMBackend):
    """Drives `litert_lm_main` on the Snapdragon 8 Gen 3 board.

    Two modes, same class:

      mode: adb    host pushes the prompt and shells out over adb.  Slower
                   (adb round-trip is in the measurement) but it is how you
                   will do the first runs from the MacBook.
      mode: local  the agent itself is running on the board; invoke the binary
                   directly.  This is the configuration the reported numbers
                   should come from -- no host in the loop.

    LD_LIBRARY_PATH must be set on EVERY invocation or the HTP backend silently
    falls back to CPU and you get a plausible-looking wrong number.  That is
    the single most expensive mistake available here, so the path is built into
    the command rather than left to the shell environment.

    ---------------------------------------------------------------------
    STATUS: the command construction and output parsing below are written
    against litert_lm_main's current CLI.  Confirm the flag names and the
    timing-line format on the board before trusting any number out of this
    class -- run `bench/probe_device.py` first.  Anything that does not parse
    is returned as 0, never as a guess.
    ---------------------------------------------------------------------
    """

    name = "litert"

    # Exact strings the binary accepts. Sourced from its own rejection message:
    #   "Unsupported backend: nonsense. Supported backends are:
    #    [CPU, GPU, NPU, GPU_ARTISAN, CPU_ARTISAN, GOOGLE_TENSOR_ARTISAN]"
    # Uppercase because that is the spelling we have direct evidence for.
    _BACKEND_FLAG = {"cpu": "CPU", "gpu": "GPU", "npu": "NPU"}

    # Warning emitted when a known backend's library is missing (silent CPU
    # fallback follows). CPU has no entry -- it is the fallback.
    _REGISTRY_WARN = {
        "npu": "NPU accelerator could not be loaded and registered",
        "gpu": "GPU accelerator could not be loaded and registered",
    }

    def __init__(self,
                 mode: str = "adb",
                 device_dir: str = "/data/local/tmp/llm",
                 binary: str = "litert_lm_main",
                 model_file: str = "model.litertlm",
                 accelerator: str = "cpu",           # cpu | gpu | npu
                 timeout_s: float = 60.0,
                 adb_serial: str = "", **_):
        accelerator = str(accelerator).lower()
        if accelerator not in self._BACKEND_FLAG:
            raise ValueError(
                f"accelerator {accelerator!r} is not one of "
                f"{sorted(self._BACKEND_FLAG)}")
        self.mode = mode
        self.device_dir = device_dir.rstrip("/")
        self.binary = binary
        self.model_file = model_file
        self.accelerator = accelerator
        self.timeout_s = timeout_s
        self.adb_serial = adb_serial

    # ---- command construction -------------------------------------------

    def _device_command(self, prompt_path: str, max_tokens: int | None = None) -> str:
        """The shell line that runs on the board.

        VERIFIED against this build (2026-09-03, `--helpfull` + `--help=<sub>`):
        litert_lm_main registers exactly FOUR flags --

            --backend  --model_path  --input_prompt  --input_prompt_file

        There is no --max_decode_steps / --max_decode_tokens / --max_tokens,
        and no --report_timing / --benchmark.  `--help=decode` and
        `--help=timing` both return "No flags matched", and --help=<substring>
        searches every registered flag, not just this module's.  So:

          * `max_tokens` CANNOT be enforced here.  The binary decodes to EOS.
            The only bounds available are the prompt itself and self.timeout_s.
            The parameter is kept in the signature so callers do not break.
          * timing is UNCONDITIONAL -- the BenchmarkInfo block always prints,
            which is why no flag exists to ask for it.

        Backend names are validated by the binary with a CHECK-fail, so a typo
        aborts loudly rather than falling back.  A *known* backend that fails
        to register, however, only WARNS and then runs on CPU -- see
        _registration_failure().

        LD_LIBRARY_PATH must be absolute and present on every invocation: the
        Android linker does not search the working directory, so `cd` alone
        leaves libGemmaModelConstraintProvider.so unresolvable and the
        executable will not link at all.
        """
        return (
            f"cd {self.device_dir} && "
            f"LD_LIBRARY_PATH={self.device_dir} "
            f"ADSP_LIBRARY_PATH={self.device_dir} "
            f"./{self.binary} "
            f"--model_path={self.device_dir}/{self.model_file} "
            f"--backend={self._BACKEND_FLAG[self.accelerator]} "
            f"--input_prompt_file={prompt_path}"
        )

    def _registration_failure(self, raw: str) -> str:
        """Detect the silent-fallback case.

        litert_lm_main CHECK-fails on an unknown backend name, but a KNOWN
        backend whose accelerator library is absent only produces:

            WARNING: [npu_registry.cc:34] NPU accelerator could not be loaded
                     and registered: kLiteRtStatusErrorInvalidArgument.

        and then proceeds on CPU.  Observed on this board for BOTH npu and gpu
        (no libQnnHtp*, no libLiteRtClGl/Vulkan accelerator).  A run that hits
        this returns real tokens at CPU speed under an NPU label -- exactly the
        wrong number.  Treat it as an error, never as a result.
        """
        marker = self._REGISTRY_WARN.get(self.accelerator)
        if marker and marker in raw:
            return (f"{self.accelerator} accelerator failed to register; "
                    f"litert_lm_main fell back to CPU. Refusing to report this "
                    f"as a {self.accelerator} measurement.")
        return ""

    def _adb(self, *args: str) -> list[str]:
        cmd = ["adb"]
        if self.adb_serial:
            cmd += ["-s", self.adb_serial]
        return cmd + list(args)

    # ---- the call --------------------------------------------------------

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        t0 = time.perf_counter()
        prompt_path = f"{self.device_dir}/_prompt_{role}.txt"

        try:
            if self.mode == "adb":
                # Push the prompt as a file rather than passing it inline:
                # shell-quoting a 2-3 kB prompt containing JSON and braces is a
                # reliable source of silent corruption.
                import tempfile
                with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                                 delete=False) as fh:
                    fh.write(prompt)
                    host_path = fh.name
                subprocess.run(self._adb("push", host_path, prompt_path),
                               capture_output=True, timeout=self.timeout_s)
                os.unlink(host_path)
                proc = subprocess.run(
                    self._adb("shell", self._device_command(prompt_path, max_tokens)),
                    capture_output=True, text=True, timeout=self.timeout_s)
            else:                                     # mode == "local"
                with open(prompt_path, "w") as fh:
                    fh.write(prompt)
                proc = subprocess.run(
                    ["sh", "-c", self._device_command(prompt_path, max_tokens)],
                    capture_output=True, text=True, timeout=self.timeout_s)

        except subprocess.TimeoutExpired:
            # A deadline miss, not a crash. The orchestrator will record it and
            # fall back to the deterministic assessment (Plan §9).
            return LLMReply(text="", status="timeout", backend=self.accelerator,
                            model=self.model_file,
                            latency_ms=(time.perf_counter() - t0) * 1000,
                            error="litert_lm_main exceeded timeout")
        except FileNotFoundError as e:
            return LLMReply(text="", status="error", backend=self.accelerator,
                            model=self.model_file, error=f"binary/adb missing: {e}")

        latency = (time.perf_counter() - t0) * 1000
        # litert_lm_main writes its INFO/VERBOSE/WARNING log to stderr and the
        # generated text plus BenchmarkInfo to stdout, but `adb shell` merges
        # or splits the two depending on the shell protocol version in use.
        # Concatenate so parsing does not depend on which side of that we land.
        raw = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0 and not raw.strip():
            return LLMReply(text="", status="error", backend=self.accelerator,
                            model=self.model_file, latency_ms=latency,
                            error=(proc.stderr or "")[:300])

        fallback = self._registration_failure(raw)
        if fallback:
            return LLMReply(text="", status="error", backend=self.accelerator,
                            model=self.model_file, latency_ms=latency,
                            error=fallback)

        text, timing = self._parse_output(raw, prompt)
        return LLMReply(
            text=text, backend=self.accelerator, model=self.model_file,
            latency_ms=latency,
            prefill_tokens=timing.get("prefill_tokens", 0),
            decode_tokens=timing.get("decode_tokens", 0),
            ttft_ms=timing.get("ttft_ms", 0.0),
        )

    # ---- BenchmarkInfo parsing ------------------------------------------
    #
    # The real format, captured from this build on 2026-09-03:
    #
    #     BenchmarkInfo:
    #       Init Phases (7):
    #         - Init Executor: 636.38 ms
    #         - Init Total: 708.34 ms
    #       Time to first token: 0.50 s
    #       Prefill Turns (Total 1 turns):
    #         Prefill Turn 1: Processed 16 tokens in 450.708698ms duration.
    #           Prefill Speed: 35.50 tokens/sec.
    #       Decode Turns (Total 1 turns):
    #         Decode Turn 1: Processed 2 tokens in 90.196615ms duration.
    #           Decode Speed: 22.17 tokens/sec.
    #
    # NOTE the unit on TTFT: it is printed in SECONDS. The previous regex
    # captured 0.50 and stored it as ttft_ms, a silent 1000x error -- the exact
    # class of wrong-but-plausible number this backend exists to avoid.
    _RE_TTFT_S = re.compile(r"Time to first token:\s*([\d.]+)\s*s")
    _RE_PREFILL_TURN = re.compile(
        r"Prefill Turn \d+:\s*Processed\s+(\d+)\s+tokens in\s+([\d.]+)\s*ms")
    _RE_DECODE_TURN = re.compile(
        r"Decode Turn \d+:\s*Processed\s+(\d+)\s+tokens in\s+([\d.]+)\s*ms")
    _RE_INIT_TOTAL = re.compile(r"Init Total:\s*([\d.]+)\s*ms")
    _RE_INIT_EXEC = re.compile(r"Init Executor:\s*([\d.]+)\s*ms")

    @classmethod
    def _parse_output(cls, raw: str, prompt: str = "") -> tuple[str, dict]:
        """Separate the generated text from litert_lm_main's timing report.

        Anything that does not match is left at zero rather than estimated. A
        missing number is recoverable; a fabricated one poisons the cost model.
        """
        timing: dict = {}

        pre = [(int(n), float(ms)) for n, ms in cls._RE_PREFILL_TURN.findall(raw)]
        dec = [(int(n), float(ms)) for n, ms in cls._RE_DECODE_TURN.findall(raw)]
        if pre:
            timing["prefill_tokens"] = sum(n for n, _ in pre)
            timing["prefill_ms"] = sum(ms for _, ms in pre)
        if dec:
            timing["decode_tokens"] = sum(n for n, _ in dec)
            timing["decode_ms"] = sum(ms for _, ms in dec)

        m = cls._RE_TTFT_S.search(raw)
        if m:
            timing["ttft_ms"] = float(m.group(1)) * 1000.0     # s -> ms
        m = cls._RE_INIT_TOTAL.search(raw)
        if m:
            timing["init_total_ms"] = float(m.group(1))
        m = cls._RE_INIT_EXEC.search(raw)
        if m:
            timing["init_executor_ms"] = float(m.group(1))
        timing["benchmark_seen"] = "BenchmarkInfo:" in raw

        return cls._extract_text(raw, prompt), timing

    @staticmethod
    def _extract_text(raw: str, prompt: str = "") -> str:
        """Pull the model's own output out of the surrounding noise.

        Three things have to be removed, in order:

        1. Everything up to and including the ECHOED PROMPT. litert_lm_main
           prints `input_prompt: <the entire prompt>` before generating. Every
           FieldMind prompt contains a JSON evidence packet, so a naive
           `\\{.*\\}` search over the whole capture matches the PROMPT's JSON,
           not the model's answer, and the pipeline then happily "parses" its
           own input back. Cut the echo first.
        2. Everything from `BenchmarkInfo:` onward.
        3. The binary's log lines (INFO/WARNING/ERROR/VERBOSE and the absl
           `I0000`-style prefixes), which are interleaved with the output.
        """
        body = raw
        idx = body.find("input_prompt:")
        if idx != -1:
            cut = idx + len("input_prompt:")
            if prompt.strip():
                tail = prompt.strip().splitlines()[-1].strip()
                j = body.find(tail, idx)
                if j != -1:
                    cut = j + len(tail)
            body = body[cut:]

        b = body.find("BenchmarkInfo:")
        if b != -1:
            body = body[:b]

        keep = [ln for ln in body.splitlines()
                if not re.match(r"^\s*(INFO|WARNING|ERROR|VERBOSE|FATAL)\b[:\s]",
                                ln)
                and not re.match(r"^\s*[IWEF]\d{4} ", ln)
                and not ln.startswith("---")]
        body = "\n".join(keep).strip()

        m = re.search(r"\{.*\}", body, re.DOTALL)
        return m.group(0) if m else body


# =======================================================================
#  4. LLAMA-SERVER  --  persistent llama.cpp lanes on the QIDK (Phase 0b)
# =======================================================================

class LlamaServerBackend(LLMBackend):
    """Talks HTTP to a persistent `llama-server` (OpenAI-style chat endpoint).

    One instance per lane: the NPU lane (`--device HTP0 -ngl 99`, port 8080)
    and the CPU lane (no offload, port 8081) differ only in `url` and `lane`.
    The model stays loaded across calls -- that is the point of this backend
    (multi-agent plan, design rule 2: no reload per call).

    Every LLMReply field comes from the server's own `timings` block:

        prefill_tokens = timings.prompt_n      tokens actually prefilled
        decode_tokens  = timings.predicted_n
        ttft_ms        = timings.prompt_ms     SERVER-SIDE prefill time; it
                         excludes the HTTP round trip and the adb forward, so
                         it is a lower bound on client-observed TTFT
        decode_ms      = timings.predicted_ms
        latency_ms     = client wall clock for the whole request

    A timing field the server did not send is None -- not 0, and never
    estimated -- so an aggregate can tell "missing" from "zero" and skip it.
    `cache_prompt` defaults to False so prompt_n counts every prompt token and
    prefix reuse cannot inflate the prefill rate the scheduler learns from.
    `json_mode` (response_format json_object) defaults to False so the
    broken-JSON rate measures the model, not a grammar.
    """

    name = "llamaserver"
    supports_grammar = True     # generate(..., grammar=<GBNF>) constrains the answer

    def __init__(self, url: str = "http://localhost:8080", lane: str = "npu",
                 model_file: str = "unknown", temperature: float = 0.0,
                 seed: int = 0, timeout_s: float = 120.0,
                 json_mode: bool = False, cache_prompt: bool = False, **_):
        self.url = url.rstrip("/")
        self.lane = lane
        self.model_file = model_file
        self.temperature = temperature
        self.seed = seed
        self.timeout_s = timeout_s
        self.json_mode = json_mode
        self.cache_prompt = cache_prompt

    def _body(self, prompt: str, max_tokens: int, grammar: str | None = None) -> dict:
        body = {"messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": self.temperature,
                "seed": self.seed,
                "cache_prompt": self.cache_prompt,
                "stream": False}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        if grammar is not None:
            # per call (multi.grammar): the server samples only what the GBNF
            # allows. Absent = the request is byte for byte what it was.
            body["grammar"] = grammar
        return body

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None,
                 grammar=None):
        import socket
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{self.url}/v1/chat/completions",
            data=json.dumps(self._body(prompt, max_tokens, grammar)).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.perf_counter()

        def fail(status: str, err: str) -> LLMReply:
            return LLMReply(text="", status=status, backend=self.lane,
                            model=self.model_file, error=err[:300],
                            prefill_tokens=None, decode_tokens=None, ttft_ms=None,
                            decode_ms=None,
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
            ttft_ms=num("prompt_ms", float),
            decode_ms=num("predicted_ms", float),
        )


# =======================================================================
#  FACTORY  --  the single switch point
# =======================================================================

_REGISTRY = {
    "mock": MockBackend,
    "gemini": GeminiBackend,
    "litert": LiteRTBackend,
}


def make_backend(cfg: dict) -> LLMBackend:
    """Build a backend from the `llm:` block of the config.

        llm:
          backend: gemini
          gemini: {model: gemini-2.0-flash}
          litert: {accelerator: npu}

    Only the sub-block matching the selected backend is read, so you can keep
    settings for all three side by side and flip between them by editing one
    word.
    """
    name = cfg.get("backend", "mock")
    if name not in _REGISTRY:
        raise ValueError(f"unknown llm backend {name!r}; "
                         f"expected one of {sorted(_REGISTRY)}")
    return _REGISTRY[name](**cfg.get(name, {}))


def register_backend(name: str, cls: type) -> None:
    """Hook for adding Qualcomm Genie, llama.cpp, Ollama, etc. without editing
    this file. Implement LLMBackend.generate and register it at import time."""
    _REGISTRY[name] = cls


register_backend("llamaserver", LlamaServerBackend)