"""
Unattended board campaign (Phase 0b): screening, dev runs, model choice,
reporting run, ranking follow-up. Launched once by scripts/board_campaign.sh.

  .venv/bin/python -m bench.campaign all            # stages A-E, resumable
  .venv/bin/python -m bench.campaign status

Every decision point is applied by code from rules committed in advance
(reports/phase0b_board_setup.md, reports/phase0b_lanes_model_choice.md). The
model is chosen by bench/model_choice.py, run unchanged as a subprocess.

What this module guarantees:
  * Ordered job list (stage, model, episode) with a progress manifest. On
    restart, completed episodes are skipped and incomplete ones are redone. An
    episode is complete only when its summary file is written (written LAST).
  * Per-call JSONL log, flushed and fsynced at once (CallLog).
  * Reply cache keyed by (model sha256, server flags, sampling params, prompt
    sha256). A cache hit is marked and carries NO timings anywhere downstream.
  * Infrastructure failures (connection refused, HTTP error, timeout, adb drop,
    server crash) raise InfraFailure out of the episode: they are never scored
    as model failures and never reach the agent's deterministic fallback. The
    episode is marked INCOMPLETE, the lane is health-checked and restarted, and
    the episode is retried up to MAX_INFRA_RETRIES times; then the campaign
    stops cleanly and writes where it stopped.
  * Thermal gate before each episode.
  * results/board/ is tracked in git; each stage is committed locally. Never pushed.

Settings throughout (bench.board.lane_command): -c 4096 -np 1 -fit off,
temperature 0, fixed seed, no JSON grammar, prompt caching off, one episode at
a time, one lane (NPU), nothing else on the board.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from bench import board as _board
from bench.evaluator import aggregate, evaluate
from bench.gguf_types import read_header, report as gguf_report
from bench.harness import Episode, run_episode
from bench.model_choice import call_stats, projected_s
from fieldmind.agent.l4_diagnose import extract_json, validate
from fieldmind.runtime.llm_backend import LLMBackend, LLMReply, register_backend

ROOT = Path(__file__).resolve().parent.parent

# ---- committed rules (human decisions; sources in the docstrings/reports) ----
# Candidate order: reports/phase0b_lanes_model_choice.md, "Candidates, run in this order".
MODELS = [
    {"tag": "llama32-3b", "file": "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf", "extra_body": {}},
    # Thinking off: per-request chat_template_kwargs (recorded in every call's
    # sampling params). Any <think> block in a reply fails Qwen3's screening.
    {"tag": "qwen3-1.7b", "file": "Qwen3-1.7B-Q4_0-pure-embq8.gguf",
     "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}, "forbid_think": True},
    {"tag": "gemma3-1b-qat", "file": "gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf", "extra_body": {}},
    {"tag": "qwen25-0.5b", "file": "Qwen2.5-0.5B-Instruct-Q4_0-pure-embq8.gguf", "extra_body": {}},
]
PRIMARY = "llama32-3b"              # must pass screening, or the campaign stops
# Model-choice dev episodes = the 3 rounds (round 3 = dev_C02).
ROUNDS = ["dev_A01_fcv_seize", "dev_B01_tube_leak", "dev_C02_feeder_trip"]
# Pre-registered follow-up: dev fault episodes whose truth is a library case.
FOLLOWUP_13 = ["dev_A01_fcv_seize", "dev_A02_fcv_seize_fast", "dev_A03_bfp_suction",
               "dev_A05_fcv_caught", "dev_A06_fcv_seize_repeat", "dev_B01_tube_leak",
               "dev_B02_tube_leak_fast", "dev_B03_tube_leak_slow", "dev_B05_tube_leak_repeat",
               "dev_C02_feeder_trip", "dev_C04_feeder_trip_caught", "dev_D01_high_cv_coal",
               "dev_D03_high_cv_severe"]
FAMILY_ORDER = ["N", "A", "B", "C", "D", "E"]    # reporting run: round-robin by family
SCREEN_SHAPES = [("screen_diag", 700, 60), ("screen_ver", 350, 30)]   # (role, prompt tok, answer cap)
SCREEN_CALLS = 5
DROP_FIRST = 0.30       # after round 1: first-reply broken-JSON rate above this -> drop
DROP_AFTER = 0.10       # after round 1: after-repair invalid rate above this -> drop
THERMAL_MARGIN_C = 5.0  # gate: within 5 C of the measured idle temperature
THERMAL_MAX_WAIT_S = 900
THERMAL_POLL_S = 15
MAX_INFRA_RETRIES = 3
CONFIRM_LOG_LEVEL = 4
# HUMAN DECISION 2026-10-03 (memory guard): before each episode, if board
# MemAvailable is under 2 GB, restart the lane before that episode. Never inside
# an episode. 2 GB read as 2 GiB = 2,097,152 kB (MemAvailable is in kB): the
# stricter of the two readings, so it restarts slightly earlier, never later.
MEM_GUARD_KB = 2 * 1024 * 1024   # the offload-confirmation launch always logs at -lv 4
EXIT_OK, EXIT_INFRA, EXIT_STOP = 0, 2, 3
# HUMAN OVERRIDE 2026-10-05 (reports/phase0b_board_setup.md), decided after both
# stage C results were seen: bench.model_choice passed no model, and the campaign
# continues with this model anyway. The rule is NOT changed: its own result is
# kept as `rule_pick`. Applies only when the rule picks nothing and this model
# completed every dev round. None = no override (stop and ask, as committed).
PICK_OVERRIDE: str | None = "llama32-3b"


class InfraFailure(Exception):
    """The board, the lane or the link failed. Not a model result."""


class CampaignStop(Exception):
    """A committed rule says stop (e.g. no model passes). Not an error."""


def now() -> str:
    return _board.laptop_time()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(tmp, mode) as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# =============================================================================
#  Board access (real). tests/fake_board.py provides the same interface.
# =============================================================================
class RealBoard:
    lane = "npu"
    url = "http://localhost:8080"
    sleep = staticmethod(time.sleep)

    def check_model(self, model_file: str) -> str:
        return _board.check_model(model_file)

    def gguf(self, model_file: str) -> dict:
        p = subprocess.run(["adb", "exec-out", f"head -c 33554432 {_board.DEVICE_MODELS}/{model_file}"],
                           capture_output=True, timeout=300, env=_board.clean_env())
        if p.returncode != 0 or not p.stdout:
            raise InfraFailure(f"adb exec-out failed for {model_file}: {p.stderr[:200]!r}")
        return gguf_report(read_header(p.stdout))

    def start(self, model_file: str, log_level: int | None) -> dict:
        info = _board.start_lane(self.lane, model_file, log_level=log_level)
        info["ready_after_s"] = _board.wait_health(self.url, 300)
        if info["ready_after_s"] is None:
            raise InfraFailure(f"lane did not become healthy: {model_file}")
        return info

    def stop(self) -> None:
        _board.stop_lanes()

    def log(self) -> str:
        return _board.lane_log(self.lane)

    def health(self) -> bool:
        return _board.lane_health(self.url)

    def temperature(self) -> dict:
        return _board.chip_temperature()

    def memory(self) -> dict:
        """Server RSS and board MemAvailable, in kB."""
        p = _board._adb("shell", "grep MemAvailable /proc/meminfo; "
                                 "pid=$(pidof llama-server); "
                                 "[ -n \"$pid\" ] && grep VmRSS /proc/$pid/status")
        vals = dict(re.findall(r"(MemAvailable|VmRSS):\s+(\d+) kB", p.stdout))
        if "MemAvailable" not in vals:
            raise InfraFailure(f"adb: MemAvailable unreadable ({p.stderr[:100]!r})")
        return {"mem_available_kb": int(vals["MemAvailable"]),
                "server_rss_kb": int(vals["VmRSS"]) if "VmRSS" in vals else None, "t": now()}

    def server_commit(self) -> str | None:
        lib = f"{_board.DEVICE_PKG}/lib"
        p = _board._adb("shell", f"cd {_board.DEVICE_PKG} && LD_LIBRARY_PATH={lib} "
                                 f"./bin/llama-server --version 2>&1")
        m = re.search(r"commit ([0-9a-f]{7,40})", p.stdout)
        return m.group(1) if m else None


def network_state() -> dict:
    """Laptop network state (the campaign itself needs none). Never raises."""
    out = {"t": now(), "interfaces": {}, "default_route": None}
    try:
        for d in sorted(Path("/sys/class/net").iterdir()):
            out["interfaces"][d.name] = (d / "operstate").read_text().strip()
        r = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True,
                           timeout=5)
        out["default_route"] = bool(r.stdout.strip())
    except Exception as e:                                   # telemetry only
        out["error"] = str(e)[:100]
    return out


def parse_offload(log: str) -> dict:
    """NPU confirmation from a -lv 4 startup log: every layer including the
    output layer offloaded, and a nonzero HTP0 model buffer."""
    m = re.search(r"offloaded (\d+)/(\d+) layers", log)
    b = re.search(r"HTP0 model buffer size\s*=\s*([\d.]+) MiB", log)
    types = {t: int(n) for t, n in re.findall(r"llama_model_loader: - type\s+(\S+):\s+(\d+) tensors", log)}
    out = {"layers_offloaded": int(m.group(1)) if m else None,
           "layers_total": int(m.group(2)) if m else None,
           "output_layer_offloaded": "offloading output layer" in log,
           "htp0_model_buffer_mib": float(b.group(1)) if b else None,
           "loader_type_counts": types}
    out["htp0_all_layers"] = bool(m and out["layers_offloaded"] == out["layers_total"]
                                  and out["layers_total"] > 0 and out["output_layer_offloaded"]
                                  and (out["htp0_model_buffer_mib"] or 0) > 0)
    return out


# =============================================================================
#  Per-call log, reply cache, backend
# =============================================================================
class CallLog:
    """Append-only JSONL. Every record is flushed and fsynced before the call
    returns, so a kill loses at most the call in flight."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # "x": exclusive create. A call log is never reopened for writing, so an
        # earlier attempt's records can never be overwritten (a relaunch did that).
        self.fh = open(self.path, "x")
        self.n = 0

    def write(self, rec: dict) -> None:
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        os.fsync(self.fh.fileno())
        self.n += 1

    def close(self) -> None:
        self.fh.close()


class ReplyCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.d: dict[str, dict] = {}
        if self.path.exists():
            for ln in self.path.read_text().splitlines():
                try:
                    r = json.loads(ln)
                    self.d[r["key"]] = r
                except (ValueError, KeyError):
                    continue                    # a torn last line after a kill
        self.fh = open(self.path, "a")

    @staticmethod
    def key(model_sha: str, server_flags: str, sampling: dict, prompt_sha: str) -> str:
        return sha256_text(json.dumps([model_sha, server_flags, sampling, prompt_sha],
                                      sort_keys=True))

    def get(self, key: str) -> dict | None:
        return self.d.get(key)

    def put(self, key: str, rec: dict) -> None:
        rec = dict(rec, key=key)
        self.d[key] = rec
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        os.fsync(self.fh.fileno())


class CallContext:
    """Everything a model call is logged with. One per lane launch + episode."""

    def __init__(self, board, cache: ReplyCache, cfg_ls: dict):
        self.board, self.cache = board, cache
        self.temperature = cfg_ls["temperature"]
        self.seed = cfg_ls["seed"]
        self.timeout_s = cfg_ls["timeout_s"]
        self.json_mode = cfg_ls["json_mode"]
        self.cache_prompt = cfg_ls["cache_prompt"]
        self.model: dict = {}
        self.model_sha = ""
        self.server_flags = ""
        self.stage = self.phase = self.episode = ""
        self.tick: int | None = None
        self.use_cache = True
        self.log: CallLog | None = None
        self.events: list[dict] = []
        self.peak: dict = {}

    def note_temp(self, temp: dict) -> None:
        """Peak in-episode chip temperature (human decision 3: reported, not a gate)."""
        for k in ("cpu_max_c", "npu_max_c"):
            v = temp.get(k)
            if v is not None and (self.peak.get(k) is None or v > self.peak[k]):
                self.peak[k] = v
        self.peak["n_reads"] = self.peak.get("n_reads", 0) + 1

    def set_tick(self, k: int) -> None:
        self.tick = k

    def sampling(self, max_tokens: int) -> dict:
        return {"temperature": self.temperature, "seed": self.seed, "max_tokens": max_tokens,
                "cache_prompt": self.cache_prompt, "json_mode": self.json_mode,
                "extra_body": self.model.get("extra_body", {})}


def parse_status(role: str, text: str) -> str:
    """How the reply parses, by the agent's own parser. Log field only."""
    payload = extract_json(text)
    if payload is None:
        return "no_json"
    if role == "diagnostician":
        ok, why = validate(payload)
        return "ok" if ok else f"invalid: {why}"
    return "ok" if isinstance(payload, dict) else "no_json"


class CampaignBackend(LLMBackend):
    """llama-server over HTTP with the campaign's log, cache and failure rule.
    Differs from LlamaServerBackend in one decision: an infrastructure failure
    RAISES InfraFailure instead of returning status timeout/error, so the agent
    never degrades to its deterministic answer because the board hiccupped."""

    name = "campaign"

    def __init__(self, ctx: CallContext, **_):
        self.ctx = ctx

    def _body(self, prompt: str, max_tokens: int) -> dict:
        c = self.ctx
        body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
                "temperature": c.temperature, "seed": c.seed, "cache_prompt": c.cache_prompt,
                "stream": False}
        if c.json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(c.model.get("extra_body", {}))
        return body

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        c = self.ctx
        psha = sha256_text(prompt)
        sampling = c.sampling(max_tokens)
        key = ReplyCache.key(c.model_sha, c.server_flags, sampling, psha)
        base = {"t": now(), "stage": c.stage, "phase": c.phase, "model": c.model.get("tag"),
                "model_file": c.model.get("file"), "model_sha256": c.model_sha,
                "server_flags": c.server_flags, "episode": c.episode, "tick": c.tick,
                "lane": c.board.lane, "role": role, "sampling": sampling,
                "prompt_sha256": psha, "prompt": prompt}
        hit = c.cache.get(key) if c.use_cache else None
        if hit is not None:
            # marked, and no timing of any kind: nothing was run on the board
            c.log.write({**base, "cache_hit": True, "status": "ok", "raw_reply": hit["raw_reply"],
                         "reasoning_content": hit.get("reasoning_content"),
                         "parse_status": hit["parse_status"], "stop_reason": hit["stop_reason"],
                         "timings": None, "prompt_n": hit["prompt_n"],
                         "predicted_n": hit["predicted_n"], "latency_ms": None, "chip_temp": None})
            c.events.append({"tick": c.tick, "role": role, "cache_hit": True})
            # the ORIGINAL measured values keep the agent's arithmetic valid; the
            # runner nulls them in the run record before anything reads timings
            return LLMReply(text=hit["raw_reply"], backend=c.board.lane, model=hit["server_model"],
                            latency_ms=hit["latency_ms"], prefill_tokens=hit["prompt_n"],
                            decode_tokens=hit["predicted_n"], ttft_ms=hit["prompt_ms"],
                            decode_ms=hit["predicted_ms"])

        req = urllib.request.Request(f"{c.board.url}/v1/chat/completions",
                                     data=json.dumps(self._body(prompt, max_tokens)).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.perf_counter()

        def infra(err: str):
            c.log.write({**base, "cache_hit": False, "status": "infra", "error": err[:300],
                         "raw_reply": None, "parse_status": None, "stop_reason": None,
                         "timings": None, "latency_ms": (time.perf_counter() - t0) * 1000,
                         "chip_temp": None})
            raise InfraFailure(err[:300])

        try:
            with urllib.request.urlopen(req, timeout=c.timeout_s) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            infra(f"HTTP {e.code}: {e.read()[:200]!r}")
        except (TimeoutError, socket.timeout):
            infra(f"timeout after {c.timeout_s}s")
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            infra(f"connection: {getattr(e, 'reason', e)}")
        latency = (time.perf_counter() - t0) * 1000
        try:
            data = json.loads(raw)
            msg = data["choices"][0]["message"]
            text = msg.get("content") or ""
            stop_reason = data["choices"][0].get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            infra(f"malformed server reply ({e!r}): {raw[:200]}")
        tm = data.get("timings") or {}

        def num(k, cast):
            v = tm.get(k)
            return cast(v) if isinstance(v, (int, float)) else None      # None, never 0

        temp = c.board.temperature()
        if "error" in temp or temp.get("cpu_max_c") is None:
            infra(f"adb: chip temperature unavailable ({temp.get('error', 'no zones')})")
        c.note_temp(temp)
        rec = {"raw_reply": text, "reasoning_content": msg.get("reasoning_content"),
               "parse_status": parse_status(role, text), "stop_reason": stop_reason,
               "prompt_n": num("prompt_n", int), "predicted_n": num("predicted_n", int),
               "prompt_ms": num("prompt_ms", float), "predicted_ms": num("predicted_ms", float),
               "latency_ms": latency, "server_model": data.get("model") or c.model.get("file")}
        c.log.write({**base, "cache_hit": False, "status": "ok", "raw_reply": text,
                     "reasoning_content": rec["reasoning_content"],
                     "parse_status": rec["parse_status"], "stop_reason": stop_reason,
                     "timings": {k: tm.get(k) for k in ("prompt_n", "prompt_ms", "prompt_per_second",
                                                        "predicted_n", "predicted_ms",
                                                        "predicted_per_second")},
                     "prompt_n": rec["prompt_n"], "predicted_n": rec["predicted_n"],
                     "latency_ms": latency,
                     "chip_temp": {k: temp.get(k) for k in ("t", "cpu_max_c", "npu_max_c")}})
        if c.use_cache:
            c.cache.put(key, rec)
        c.events.append({"tick": c.tick, "role": role, "cache_hit": False})
        return LLMReply(text=text, backend=c.board.lane, model=rec["server_model"],
                        latency_ms=latency, prefill_tokens=rec["prompt_n"],
                        decode_tokens=rec["predicted_n"], ttft_ms=rec["prompt_ms"],
                        decode_ms=rec["predicted_ms"])


register_backend("campaign", CampaignBackend)


def null_cache_hit_timings(run: dict, events: list[dict]) -> int:
    """Cache hits carry no timing. Null every timing a hit left in the run
    record (call records, the envelope's latency) and mark them, so no rate,
    projection or latency statistic can include them. Returns the hit count."""
    by: dict[tuple, list[dict]] = {}
    for e in events:
        by.setdefault((e["tick"], e["role"]), []).append(e)
    hits = 0
    for a in run["assessments"]:
        for env in a.get("envelopes", []) or []:
            evs = by.get((env["tick"], env["agent"]), [])
            if len(evs) != len(env.get("calls") or []):
                raise RuntimeError(f"call log and envelope disagree at tick {env['tick']} "
                                   f"{env['agent']}: {len(evs)} vs {len(env.get('calls') or [])}")
            for call, ev in zip(env["calls"], evs):
                if ev["cache_hit"]:
                    call.update(prefill_ms=None, decode_ms=None, latency_ms=None, cache_hit=True)
                    env["latency_ms"] = None
                    a["cache_hit"] = True
                    hits += 1
    return hits


# =============================================================================
#  Git helpers (the campaign commits locally, never pushes)
# =============================================================================
def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          env=_board.clean_env()).stdout.strip()


def git_commit_hash() -> str:
    return _git("rev-parse", "HEAD")


def code_hash() -> str:
    """Hash of every decision path (agent, bench, config, entry point) at HEAD
    plus any uncommitted change to them. Result commits do not move it."""
    trees = _git("rev-parse", "HEAD:fieldmind", "HEAD:bench", "HEAD:configs", "HEAD:data/kb")
    dirty = _git("status", "--porcelain", "--", "fieldmind", "bench", "configs", "data/kb")
    return sha256_text(trees + "\n" + dirty)[:16]


# =============================================================================
#  The campaign
# =============================================================================
class Campaign:
    def __init__(self, out_dir: Path, board, config: str = "configs/base.yaml",
                 commit: bool = True, baseline_dir: Path | None = None,
                 models: list[dict] | None = None):
        self.out = Path(out_dir)
        self.board = board
        self.sleep = getattr(board, "sleep", time.sleep)
        self.cfg = yaml.safe_load((ROOT / config).read_text())
        self.commit_enabled = commit
        self.baseline_dir = Path(baseline_dir or ROOT / "results" / "baselines")
        self.models = {m["tag"]: m for m in (models or MODELS)}
        self.model_order = [m["tag"] for m in (models or MODELS)]
        self.out.mkdir(parents=True, exist_ok=True)
        self.tmp = self.out / "tmp"
        self.tmp.mkdir(exist_ok=True)
        self.cache = ReplyCache(self.out / "cache" / "replies.jsonl")
        self.ctx = CallContext(board, self.cache, self.cfg["llm"]["llamaserver"])
        self.mpath = self.out / "manifest.json"
        self.manifest = (json.loads(self.mpath.read_text()) if self.mpath.exists() else
                         {"version": 1, "created": now(), "jobs": {}, "stages": {}, "idle_temp": None})
        self.lane_state: tuple | None = None       # (model file, log level) of the running lane
        self.offload_seen: dict[str, dict] = {}
        self.stage = ""
        self.last_error = ""
        self.durations: list[float] = []
        self.stage_total = self.stage_done = 0
        self.server_commit = None

    # ---- manifest / status ---------------------------------------------------
    def save(self) -> None:
        atomic_write(self.mpath, json.dumps(self.manifest, indent=1))

    def job(self, jid: str) -> dict:
        return self.manifest["jobs"].setdefault(jid, {"status": "pending", "attempts": 0})

    def live_log(self, jid: str) -> Path:
        """This attempt's call log (attempt number from the manifest)."""
        n = self.manifest["jobs"][jid]["attempts"]
        return self.tmp / f"{jid.replace('/', '__')}.attempt{n}.calls.jsonl"

    def archive_attempt_logs(self, jid: str, keep: Path | None = None) -> list[str]:
        """Move every call log of `jid` left in tmp/ (a failed attempt, or one a
        kill interrupted) into attempts/, gzipped, under its attempt number.
        Never overwrites: an archive that already exists is an error."""
        out = []
        for f in sorted(self.tmp.glob(f"{jid.replace('/', '__')}.attempt*.calls.jsonl")):
            if keep is not None and f == keep:
                continue
            dst = self.out / "attempts" / (f.name + ".gz")
            if dst.exists():
                raise RuntimeError(f"attempt log archive already exists: {dst}")
            atomic_write(dst, gzip.compress(f.read_bytes()))
            f.unlink()
            out.append(str(dst.relative_to(self.out)))
        if out:
            self.manifest["jobs"][jid].setdefault("attempt_logs", []).extend(out)
        return out

    def is_complete(self, jid: str) -> bool:
        j = self.manifest["jobs"].get(jid, {})
        return j.get("status") == "complete" and (self.out / j.get("summary", "?")).exists()

    def write_status(self, model: str = "", note: str = "") -> None:
        jobs = self.manifest["jobs"]
        done_all = sum(self.is_complete(j) for j in jobs)
        left = max(0, self.stage_total - self.stage_done)
        eta = (f"{left * sum(self.durations) / len(self.durations) / 60:.0f} min for this stage "
               f"(mean of {len(self.durations)} finished episodes; later stages not included)"
               if self.durations and left else ("0 min for this stage" if not left else "unknown"))
        atomic_write(self.out / "STATUS.md", "\n".join([
            "# Board campaign status", "",
            f"- updated (laptop time): {now()}",
            f"- stage: {self.stage or '-'}",
            f"- model: {model or '-'}",
            f"- episodes done in this stage: {self.stage_done} of {self.stage_total}",
            f"- episodes complete overall: {done_all} of {len(jobs)} known jobs",
            f"- last error: {self.last_error or 'none'}",
            f"- estimated time left: {eta}",
            f"- note: {note or '-'}", ""]))

    def commit(self, msg: str, extra: list[str] | None = None) -> None:
        if not self.commit_enabled:
            return
        rel = os.path.relpath(self.out, ROOT)
        subprocess.run(["git", "add", "--", rel, *(extra or [])], cwd=ROOT, env=_board.clean_env())
        subprocess.run(["git", "commit", "-q", "-m", msg, "--", rel, *(extra or [])], cwd=ROOT,
                       env=_board.clean_env())

    # ---- board helpers -------------------------------------------------------
    @staticmethod
    def _max_c(t: dict) -> float:
        vals = [v for v in (t.get("cpu_max_c"), t.get("npu_max_c")) if v is not None]
        if "error" in t or not vals:
            raise InfraFailure(f"adb: chip temperature unavailable ({t.get('error', 'no zones')})")
        return max(vals)

    def measure_idle(self) -> None:
        """Idle temperature, measured once per campaign with no lane running:
        poll until two consecutive readings differ by under 0.5 C (max 5 min)."""
        if self.manifest.get("idle_temp"):
            return
        self.board.stop()
        self.lane_state = None
        prev, readings = None, []
        for _ in range(20):
            cur = self._max_c(self.board.temperature())
            readings.append(cur)
            if prev is not None and abs(cur - prev) < 0.5:
                break
            prev = cur
            self.sleep(THERMAL_POLL_S)
        self.manifest["idle_temp"] = {"max_c": readings[-1], "readings": readings, "t": now()}
        self.save()

    def thermal_gate(self) -> dict:
        limit = self.manifest["idle_temp"]["max_c"] + THERMAL_MARGIN_C
        waited, timed_out = 0.0, False
        while True:
            cur = self._max_c(self.board.temperature())
            if cur <= limit:
                break
            if waited >= THERMAL_MAX_WAIT_S:
                timed_out = True
                break
            self.sleep(THERMAL_POLL_S)
            waited += THERMAL_POLL_S
        return {"waited_s": waited, "limit_c": round(limit, 1), "temp_c": cur, "timed_out": timed_out}

    def ensure_lane(self, tag: str, log_level: int | None) -> None:
        m = self.models[tag]
        if self.lane_state == (m["file"], log_level) and self.board.health():
            return
        self.board.stop()
        self.lane_state = None
        sha = self.board.check_model(m["file"])       # ModelNotAllowed is fatal, not infra
        info = self.board.start(m["file"], log_level)
        self.lane_state = (m["file"], log_level)
        self.ctx.model, self.ctx.model_sha = m, sha
        # everything after "--port N": -c 4096 -np 1 <offload> -fit off [-lv 4]
        self.ctx.server_flags = info["command"].split("--port ", 1)[1].split(" ", 1)[1]
        if log_level == CONFIRM_LOG_LEVEL:
            self.offload_seen[tag] = parse_offload(self.board.log())
        if self.server_commit is None:
            self.server_commit = getattr(self.board, "server_commit", lambda: None)()

    def memory_guard(self, tag: str, jid: str) -> dict:
        """Log server RSS and board MemAvailable; restart the lane if MemAvailable
        is under MEM_GUARD_KB. Called only between episodes."""
        before = self.board.memory()
        out = {"threshold_kb": MEM_GUARD_KB, "before": before, "restarted": False}
        if before["mem_available_kb"] < MEM_GUARD_KB:
            self.board.stop()
            self.lane_state = None
            self.ensure_lane(tag, self.timed_log_level())
            out.update(restarted=True, after=self.board.memory())
            self.manifest.setdefault("lane_restarts", []).append(
                {"t": now(), "before_job": jid, "reason": "memory guard", **out})
            self.save()
            self.write_status(tag, f"memory guard: MemAvailable {before['mem_available_kb'] // 1024} MB "
                                   f"< {MEM_GUARD_KB // 1024} MB, lane restarted before {jid}")
        return out

    def timed_log_level(self) -> int | None:
        return _board.TIMED_LOG_LEVEL

    # ---- one job with the infrastructure-failure rule ------------------------
    def run_job(self, jid: str, tag: str, fn) -> dict:
        """Run fn() (which must write the job's summary LAST and return its
        path) under the infrastructure rule. Returns the loaded summary."""
        j = self.job(jid)
        if self.is_complete(jid):
            self.stage_done += 1
            return json.loads((self.out / j["summary"]).read_text())
        self.archive_attempt_logs(jid)          # left by a kill or an older launch
        for attempt in range(1, MAX_INFRA_RETRIES + 2):
            j.update(status="incomplete", attempts=j["attempts"] + 1, started=now())
            self.save()
            self.write_status(tag, f"running {jid} (attempt {j['attempts']})")
            t0 = time.monotonic()
            try:
                summary = fn()
            except InfraFailure as e:
                self.last_error = f"{now()} {jid}: INFRASTRUCTURE: {e}"
                j.update(status="incomplete", last_error=str(e))
                self.save()
                if self.ctx.log:
                    self.ctx.log.close()
                    self.ctx.log = None
                self.archive_attempt_logs(jid)
                self.save()
                if attempt > MAX_INFRA_RETRIES:
                    self.write_status(tag, f"STOPPED at {jid}: infrastructure failure after "
                                           f"{MAX_INFRA_RETRIES} retries. Fix the board and relaunch; "
                                           f"completed episodes are kept.")
                    raise
                # health-check, then restart the lane, then retry the whole episode
                healthy = False
                try:
                    healthy = self.board.health()
                except Exception:
                    pass
                self.write_status(tag, f"{jid} INCOMPLETE (lane healthy before restart: {healthy}); "
                                       f"restarting the lane, retry {attempt} of {MAX_INFRA_RETRIES}")
                self.lane_state = None
                try:
                    self.board.stop()
                except Exception:
                    pass
                self.sleep(min(60, 10 * attempt))
                continue
            rel = os.path.relpath(summary, self.out)
            j.update(status="complete", summary=rel, finished=now())
            j.pop("last_error", None)
            self.save()
            self.durations.append(time.monotonic() - t0)
            self.stage_done += 1
            self.write_status(tag, f"finished {jid}")
            return json.loads(Path(summary).read_text())
        raise AssertionError("unreachable")

    # ---- an episode ----------------------------------------------------------
    def episode_cfg(self, episodes_dir: str) -> dict:
        cfg = copy.deepcopy(self.cfg)
        cfg["llm"]["backend"] = "campaign"
        cfg["llm"]["campaign"] = {"ctx": self.ctx}
        cfg["paths"]["episodes"] = episodes_dir
        cfg.setdefault("agent", {})["log_prompts"] = False     # prompts live in the call log
        return cfg

    def prefix(self, stage: str, phase: str, tag: str, episode: str) -> str:
        """Path prefix of a job's files; '.summary.json' etc. are appended."""
        return str(self.out / stage / tag / (episode if phase == stage else f"{phase}.{episode}"))

    def run_episode_job(self, stage: str, phase: str, tag: str, episode: str,
                        episodes_dir: str) -> dict:
        jid = f"{stage}/{phase}/{tag}/{episode}"
        base = self.prefix(stage, phase, tag, episode)

        def fn() -> Path:
            gate = self.thermal_gate()
            self.ensure_lane(tag, self.timed_log_level())
            guard = self.memory_guard(tag, jid)              # before the episode, never inside it
            c = self.ctx
            c.stage, c.phase, c.episode, c.tick, c.use_cache, c.events = stage, phase, episode, None, True, []
            c.peak = {}
            live = self.live_log(jid)
            c.log = CallLog(live)
            cfg = self.episode_cfg(episodes_dir)
            t_start, net = self.board.temperature(), network_state()
            self._max_c(t_start)
            c.note_temp(t_start)
            run = run_episode(Episode(ROOT / episodes_dir / episode), cfg, on_tick=c.set_tick)
            t_end = self.board.temperature()
            self._max_c(t_end)
            c.note_temp(t_end)
            c.log.close()
            c.log = None
            hits = null_cache_hit_timings(run, c.events)
            run["chip_temp_start"], run["chip_temp_end"] = t_start, t_end
            ev = evaluate(run)
            cfg["llm"].pop("campaign")
            meta = {"job": jid, "stage": stage, "phase": phase, "model": tag,
                    "model_file": c.model["file"], "model_sha256": c.model_sha,
                    "server_flags": c.server_flags, "sampling": c.sampling(cfg["agent"]["max_tokens"]),
                    "episode": episode, "episodes_dir": episodes_dir,
                    "git_commit": git_commit_hash(), "code_hash": code_hash(),
                    "llamacpp_commit": self.server_commit, "config": cfg,
                    "chip_temp_start": t_start, "chip_temp_end": t_end, "thermal_gate": gate,
                    "chip_temp_peak": dict(c.peak), "memory_guard": guard,
                    "network": net, "n_calls": len(c.events), "n_cache_hits": hits,
                    "finished": now(), "energy_mwh": None}
            atomic_write(Path(base + ".run.json.gz"), gzip.compress(json.dumps(run).encode()))
            atomic_write(Path(base + ".calls.jsonl.gz"), gzip.compress(live.read_bytes()))
            # the summary is written LAST: its presence is what "complete" means
            atomic_write(Path(base + ".summary.json"),
                         json.dumps({"meta": meta, "evaluation": ev}, indent=1))
            live.unlink()
            return Path(base + ".summary.json")

        return self.run_job(jid, tag, fn)

    def load_run(self, stage: str, phase: str, tag: str, episode: str) -> dict:
        base = self.prefix(stage, phase, tag, episode)
        return json.loads(gzip.decompress(Path(base + ".run.json.gz").read_bytes()))

    # ---- stage A: screening --------------------------------------------------
    def _tokenize_n(self, text: str) -> int:
        req = urllib.request.Request(f"{self.board.url}/tokenize",
                                     data=json.dumps({"content": text, "add_special": False}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return len(json.loads(r.read().decode())["tokens"])
        except Exception as e:
            raise InfraFailure(f"/tokenize failed: {e}")

    def shape_prompt(self, body: str, tail: str, n_content: int) -> str:
        """Longest prefix of `body` such that prefix + tail has at most
        n_content tokens (server's own tokenizer, binary search on characters)."""
        lo, hi = 0, len(body)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._tokenize_n(body[:mid] + tail) <= n_content:
                lo = mid
            else:
                hi = mid - 1
        return body[:lo] + tail

    def screening_sources(self) -> dict:
        """One real diagnostician prompt and one real verifier prompt: the first
        of each in the MOCK runs of the model-choice dev episodes, taken in
        round order (deterministic; dev_A01 has no verifier call on mock, so
        the verifier prompt comes from dev_B01)."""
        if getattr(self, "_screen_src", None):
            return self._screen_src
        cfg = copy.deepcopy(self.cfg)
        cfg["llm"]["backend"] = "mock"
        cfg.setdefault("agent", {})["log_prompts"] = True
        out, src = {}, {}
        for ep in ROUNDS:
            run = run_episode(Episode(ROOT / "data/episodes_dev" / ep), cfg)
            for a in run["assessments"]:
                for e in a.get("envelopes", []) or []:
                    role = {"diagnostician": "screen_diag", "verifier": "screen_ver"}.get(e["agent"])
                    if role and role not in out and e.get("prompt"):
                        out[role], src[role] = e["prompt"], f"{ep} tick {e['tick']}"
            if len(out) == len(SCREEN_SHAPES):
                break
        if len(out) != len(SCREEN_SHAPES):
            raise CampaignStop("screening: no diagnostician/verifier source prompt found")
        self._screen_src, self._screen_src_from = out, src
        return out

    def screen_model(self, tag: str) -> dict:
        jid = f"A/A/{tag}/screen"
        base = self.prefix("A", "A", tag, "screen")
        m = self.models[tag]

        def fn() -> Path:
            gguf = self.board.gguf(m["file"])
            gguf_ok = str(gguf.get("verdict", "")).startswith("OK")
            self.ensure_lane(tag, CONFIRM_LOG_LEVEL)             # offload-confirmation launch
            off = self.offload_seen[tag]
            if self.timed_log_level() != CONFIRM_LOG_LEVEL:
                self.ensure_lane(tag, self.timed_log_level())
            gate = self.thermal_gate()
            c = self.ctx
            c.stage = c.phase = "A"
            c.episode, c.tick, c.use_cache, c.events = "screen", None, False, []
            live = self.live_log(jid)
            c.log = CallLog(live)
            be = CampaignBackend(c)
            src = self.screening_sources()
            # warm-up call: recorded, excluded from every statistic; also gives
            # the chat-template overhead (server prompt_n minus content tokens)
            warm = "Reply with the single word: ok"
            w = be.generate(warm, role="screen_warmup", max_tokens=8)
            overhead = (w.prefill_tokens or 0) - self._tokenize_n(warm)
            calls, shapes = [], {}
            for role, n_prompt, cap in SCREEN_SHAPES:
                text = src[role]
                cut = text.rfind("Output schema:")
                body, tail = (text[:cut], "\n" + text[cut:]) if cut > 0 else (text, "")
                prompt = self.shape_prompt(body, tail, n_prompt - overhead)
                shapes[role] = {"target_prompt_tokens": n_prompt, "answer_cap": cap,
                                "prompt_sha256": sha256_text(prompt),
                                "source": self._screen_src_from[role]}
                for i in range(SCREEN_CALLS):
                    c.tick = i
                    r = be.generate(prompt, role=role, max_tokens=cap)
                    calls.append({"role": role, "i": i, "prompt_n": r.prefill_tokens,
                                  "predicted_n": r.decode_tokens, "prompt_ms": r.ttft_ms,
                                  "predicted_ms": r.decode_ms, "latency_ms": r.latency_ms})
            c.log.close()
            c.log = None
            recs = [json.loads(ln) for ln in live.read_text().splitlines()]
            think = [r["tick"] for r in recs
                     if "<think>" in (r.get("raw_reply") or "") or (r.get("reasoning_content") or "").strip()]
            timed = [x for x in calls if None not in (x["prompt_n"], x["predicted_n"],
                                                      x["prompt_ms"], x["predicted_ms"])]
            pr = dr = measured = None
            if len(timed) == len(calls) and all(x["prompt_ms"] and x["predicted_ms"] for x in timed):
                pr = sum(x["prompt_n"] for x in timed) / (sum(x["prompt_ms"] for x in timed) / 1000)
                dr = sum(x["predicted_n"] for x in timed) / (sum(x["predicted_ms"] for x in timed) / 1000)
                per = {role: [x["prompt_ms"] + x["predicted_ms"] for x in timed if x["role"] == role]
                       for role, _, _ in SCREEN_SHAPES}
                measured = round(sum(sum(v) / len(v) for v in per.values()) / 1000, 3)
            proj = projected_s(pr, dr)
            think_ok = not (m.get("forbid_think") and think)
            res = {"model": tag, "model_file": m["file"], "model_sha256": c.model_sha,
                   "server_flags": c.server_flags, "llamacpp_commit": self.server_commit,
                   "git_commit": git_commit_hash(), "gguf": gguf, "gguf_all_q4_0_q8_0": gguf_ok,
                   "offload": off, "htp0_all_layers": off["htp0_all_layers"],
                   "loader_types_match_gguf": {k.upper(): v for k, v in off["loader_type_counts"].items()}
                   == gguf.get("type_counts"),
                   "thermal_gate": gate, "template_overhead_tokens": overhead, "shapes": shapes,
                   "calls": calls, "warmup": {"prompt_n": w.prefill_tokens, "prompt_ms": w.ttft_ms},
                   "server_prefill_tok_s": round(pr, 1) if pr else None,
                   "server_decode_tok_s": round(dr, 2) if dr else None,
                   # MEASURED: mean server time of the 700/60 calls + mean of the 350/30 calls
                   "measured_verified_s": measured,
                   # PROJECTION (bench.model_choice.projected_s) at this screening's pooled rates
                   "projected_verified_s": proj,
                   "measured_over_projected": round(measured / proj, 3) if measured and proj else None,
                   "short_answers": sum(1 for x in calls for role, _, cap in SCREEN_SHAPES
                                        if x["role"] == role and (x["predicted_n"] or 0) < cap),
                   "think_blocks_in_calls": think, "thinking_off_mechanism": m.get("extra_body") or None,
                   "think_ok": think_ok, "finished": now(), "energy_mwh": None}
            res["pass"] = bool(gguf_ok and off["htp0_all_layers"] and think_ok)
            atomic_write(Path(base + ".calls.jsonl.gz"), gzip.compress(live.read_bytes()))
            atomic_write(Path(base + ".summary.json"), json.dumps(res, indent=1))
            live.unlink()
            return Path(base + ".summary.json")

        return self.run_job(jid, tag, fn)

    def stage_a(self) -> None:
        self.begin("A", len(self.model_order))
        res = {tag: self.screen_model(tag) for tag in self.model_order}
        passing = [t for t in self.model_order if res[t]["pass"]]
        out = {"passing": passing, "primary": PRIMARY, "dev_models": list(passing),
               "measured_verified_s": {t: res[t]["measured_verified_s"] for t in self.model_order},
               "projected_verified_s": {t: res[t]["projected_verified_s"] for t in self.model_order},
               "checks": {t: {"htp0_all_layers": res[t]["htp0_all_layers"],
                              "gguf_all_q4_0_q8_0": res[t]["gguf_all_q4_0_q8_0"]} for t in self.model_order},
               "rule": "HUMAN DECISION 2026-10-04 (replaces option C): every model that passes screening "
                       "(GGUF + offload checks) gets the dev rounds"}
        atomic_write(self.out / "A" / "screening.json", json.dumps(out, indent=1))
        self.finish("A", out)
        if PRIMARY not in passing:
            raise CampaignStop(f"stage A: {PRIMARY} failed screening")
        if len(passing) < 2:
            raise CampaignStop("stage A: no other model passed screening")

    # ---- stage B: dev runs, interleaved --------------------------------------
    def dev_models(self) -> list[str]:
        """HUMAN DECISION 2026-10-04 (replaces option C): every model that passed
        screening gets the dev rounds, in the committed candidate order."""
        passing = self.manifest["stages"]["A"]["passing"]
        return [t for t in self.model_order if t in passing]

    def stage_b(self) -> None:
        pair = self.dev_models()
        self.begin("B", len(pair) * len(ROUNDS))
        b = self.manifest["stages"].setdefault("B", {"models": pair, "dropped": {}})
        # a stage B record from an earlier model list (option C ran two models) is
        # extended, never restarted: finished episodes and their round-1 rates stay
        b["models"] = pair
        b.pop("done", None)
        b.pop("survivors", None)
        b.setdefault("round1", {})
        alive = [t for t in pair if t not in b["dropped"]]
        for r, ep in enumerate(ROUNDS, 1):
            for tag in pair:
                if tag not in alive:
                    continue
                self.run_episode_job("B", f"round{r}", tag, ep, "data/episodes_dev")
            if r == 1:
                for tag in pair:
                    if tag in b["round1"]:
                        continue                      # decided when that model ran round 1
                    cs = call_stats([self.load_run("B", "round1", tag, ep)])
                    b["round1"][tag] = {"broken_json_first_reply": cs["broken_json_first_reply"],
                                        "broken_json_after_repair": cs["broken_json_after_repair"]}
                    f, g = cs["broken_json_first_reply"], cs["broken_json_after_repair"]
                    if (f is not None and f > DROP_FIRST) or (g is not None and g > DROP_AFTER):
                        b["dropped"][tag] = b["round1"][tag]
                self.save()
                alive = [t for t in pair if t not in b["dropped"]]
                if not alive:
                    b["survivors"] = []
                    self.finish("B", b)
                    raise CampaignStop("stage B: every model dropped after round 1")
        b["survivors"] = alive
        self.finish("B", b)

    # ---- stage C: bench.model_choice, unchanged ------------------------------
    def _model_choice(self, name: str, runs: dict[str, list[dict]], checks: dict, out_dir: Path) -> dict:
        d = self.tmp / name
        d.mkdir(exist_ok=True)
        paths = []
        for col, rs in runs.items():
            p = d / f"runs_{col}.json"
            p.write_text(json.dumps(rs))
            paths.append(str(p))
        (d / "checks.json").write_text(json.dumps(checks))
        out_dir.mkdir(parents=True, exist_ok=True)
        js = out_dir / f"{name}.json"
        if js.exists():                               # keep an earlier result, never overwrite it
            n = 1
            while (out_dir / f"{name}.superseded-{n}.json").exists():
                n += 1
            for ext in ("json", "txt"):
                if (out_dir / f"{name}.{ext}").exists():
                    os.replace(out_dir / f"{name}.{ext}", out_dir / f"{name}.superseded-{n}.{ext}")
        p = subprocess.run([sys.executable, "-m", "bench.model_choice", *paths, "--checks",
                            str(d / "checks.json"), "--json", str(js)], cwd=ROOT,
                           capture_output=True, text=True, env=_board.clean_env())
        (out_dir / f"{name}.txt").write_text(p.stdout + ("\nSTDERR:\n" + p.stderr if p.stderr else ""))
        if p.returncode != 0 or not js.exists():
            raise CampaignStop(f"bench.model_choice failed ({name}): {p.stderr[-300:]}")
        return json.loads(js.read_text())

    def mock_runs(self, episodes: list[str]) -> list[dict]:
        cfg = copy.deepcopy(self.cfg)
        cfg["llm"]["backend"] = "mock"
        return [run_episode(Episode(ROOT / "data/episodes_dev" / e), cfg) for e in episodes]

    def stage_c(self) -> None:
        self.begin("C", 0)
        b, a = self.manifest["stages"]["B"], self.manifest["stages"]["A"]
        runs = {"mock": self.mock_runs(ROUNDS)}          # deterministic floor, same 3 episodes
        for tag in b["survivors"]:
            runs[tag] = [self.load_run("B", f"round{r}", tag, ep) for r, ep in enumerate(ROUNDS, 1)]
        mc = self._model_choice("model_choice", runs, a["checks"], self.out / "C")
        out = self._c_record(mc, b["survivors"])
        self.finish("C", out)
        if out["pick"] is None:
            raise CampaignStop("stage C: no model passes bench.model_choice (stop and ask)")

    def _c_record(self, mc: dict, survivors: list[str]) -> dict:
        rule_pick = mc["selection"]["pick"]
        pick, override = rule_pick, None
        if rule_pick is None and PICK_OVERRIDE is not None and PICK_OVERRIDE in survivors:
            pick = PICK_OVERRIDE
            override = {"model": pick, "by": "human", "decided": "2026-10-05, after the stage C result was seen",
                        "note": "bench.model_choice passed NO model; this model did not pass the rule",
                        "failed_constraints": [k for k, v in mc["selection"]["gates"][pick].items()
                                               if k != "pass" and v is not True]}
        out = {"pick": pick, "rule_pick": rule_pick, "override": override,
               "pick_reason": ("HUMAN OVERRIDE: no model passes the rule" if override
                               else mc["selection"]["pick_reason"]),
               "passing": mc["selection"]["passing"], "flags": mc["selection"]["flags"],
               "gates": mc["selection"]["gates"],
               "ranking_verdict_3_episodes": (mc["columns"][pick]["ranking_model_rank1"]["verdict"]
                                              if pick else None),
               "code_hash": code_hash()}
        return out

    def apply_override_to_finished_c(self) -> None:
        """A stage C that already finished with no pick (the campaign stopped there)
        gets the override without being recomputed: same model_choice output."""
        c = self.manifest["stages"].get("C", {})
        if not c.get("done") or c.get("pick") is not None or PICK_OVERRIDE is None:
            return
        mc = json.loads((self.out / "C" / "model_choice.json").read_text())
        new = self._c_record(mc, self.manifest["stages"]["B"].get("survivors") or [])
        if new["pick"] is None:
            return
        new.update(code_hash=c.get("code_hash"), done=True, finished=c.get("finished"),
                   override_applied=now())
        self.manifest["stages"]["C"] = new
        self.save()
        self.write_status(note=f"stage C: HUMAN OVERRIDE, chosen model {new['pick']} (the rule picked none)")
        self.commit("Board campaign: stage C human override recorded")

    # ---- stage D: reporting run ----------------------------------------------
    @staticmethod
    def reporting_order(episodes: list[str]) -> list[str]:
        fam = {f: sorted(e for e in episodes if e.split("_")[1][0] == f) for f in FAMILY_ORDER}
        out = []
        for i in range(max(len(v) for v in fam.values())):
            out += [fam[f][i] for f in FAMILY_ORDER if i < len(fam[f])]
        return out

    def stage_d(self) -> None:
        pick = self.manifest["stages"]["C"]["pick"]
        eps = self.reporting_order(sorted(p.name for p in (ROOT / "data/episodes").iterdir()
                                          if (p / "ground_truth.json").exists()))
        self.begin("D", len(eps))
        sums = [self.run_episode_job("D", "D", pick, ep, "data/episodes") for ep in eps]
        evals = sorted((s["evaluation"] for s in sums), key=lambda e: e["episode_id"])
        m = self.models[pick]
        name = f"single_v3_real_{pick}_npu_summary.json"
        base = {"commit": git_commit_hash(), "backend": "llamaserver (campaign runner)", "lane": "npu",
                "model": pick, "model_file": m["file"], "model_sha256": sums[0]["meta"]["model_sha256"],
                "server_flags": sums[0]["meta"]["server_flags"],
                "llamacpp_commit": sums[0]["meta"]["llamacpp_commit"],
                "model_choice_override": self.manifest["stages"]["C"].get("override"),
                "note": "REAL model on the QIDK NPU lane, single-agent v3 code, the 30 REPORTING "
                        "episodes, run once. Agent code ran on the laptop; only model calls ran on "
                        "the board. Energy was not measured (null). Cache hits carry no timings.",
                "episode_order": eps, "n_cache_hits": sum(s["meta"]["n_cache_hits"] for s in sums),
                "n_calls": sum(s["meta"]["n_calls"] for s in sums),
                "summary": aggregate(evals), "per_episode": evals}
        atomic_write(self.baseline_dir / name, json.dumps(base, indent=2))
        atomic_write(self.out / "D" / name, json.dumps(base, indent=2))
        self.finish("D", {"model": pick, "baseline": name, "n_episodes": len(eps)},
                    extra=[os.path.relpath(self.baseline_dir / name, ROOT)])

    # ---- stage E: ranking follow-up ------------------------------------------
    def stage_e(self) -> None:
        c = self.manifest["stages"]["C"]
        pick = c["pick"]
        if c["ranking_verdict_3_episodes"] != "INCONCLUSIVE":
            self.begin("E", 0)
            self.finish("E", {"run": False, "reason": f"round-3 verdict is "
                                                      f"{c['ranking_verdict_3_episodes']}, not INCONCLUSIVE"})
            return
        reuse = c["code_hash"] == code_hash()       # decision paths + config unchanged
        todo = [e for e in FOLLOWUP_13 if not (reuse and e in ROUNDS)]
        self.begin("E", len(todo))
        for ep in todo:
            self.run_episode_job("E", "E", pick, ep, "data/episodes_dev")
        runs = []
        for ep in FOLLOWUP_13:
            if reuse and ep in ROUNDS:
                runs.append(self.load_run("B", f"round{ROUNDS.index(ep) + 1}", pick, ep))
            else:
                runs.append(self.load_run("E", "E", pick, ep))
        mc = self._model_choice("ranking_13", {pick: runs}, self.manifest["stages"]["A"]["checks"],
                                self.out / "E")
        self.finish("E", {"run": True, "reused_model_choice_episodes": reuse, "episodes_run": todo,
                          "ranking_verdict_13_episodes": mc["columns"][pick]["ranking_model_rank1"]["verdict"],
                          "pooled": mc["columns"][pick]["ranking_model_rank1"]["pooled"]})

    # ---- stage bookkeeping ---------------------------------------------------
    def begin(self, stage: str, total: int) -> None:
        self.stage, self.stage_total, self.stage_done, self.durations = stage, total, 0, []
        self.write_status(note=f"stage {stage} started")

    def finish(self, stage: str, result: dict, extra: list[str] | None = None) -> None:
        self.manifest["stages"][stage] = dict(result, done=True, finished=now())
        self.save()
        self.write_status(note=f"stage {stage} finished")
        self.commit(f"Board campaign: stage {stage} results", extra)

    def stage_done_flag(self, stage: str) -> bool:
        s = self.manifest["stages"].get(stage, {})
        if stage == "B" and s.get("done") and s.get("models") != self.dev_models():
            return False                              # the dev-model list changed: extend stage B
        return bool(s.get("done"))

    def supersede_after(self, stage: str) -> None:
        """A stage is being (re)run: results of LATER stages were computed from its
        old output. Keep them in the manifest under `superseded`, and recompute."""
        for later in "ABCDE"["ABCDE".index(stage) + 1:]:
            if later in self.manifest["stages"]:
                self.manifest.setdefault("superseded", []).append(
                    {"stage": later, "superseded_at": now(), "because": f"stage {stage} was rerun",
                     "record": self.manifest["stages"].pop(later)})
        self.save()

    def run_all(self) -> int:
        try:
            self.measure_idle()
            if self.stage_done_flag("B"):
                self.apply_override_to_finished_c()
            for stage, fn in (("A", self.stage_a), ("B", self.stage_b), ("C", self.stage_c),
                              ("D", self.stage_d), ("E", self.stage_e)):
                # a finished stage is not recomputed; its stop rule is re-applied
                if self.stage_done_flag(stage):
                    self._reapply_stop(stage)
                    continue
                self.supersede_after(stage)
                fn()
            self.stage = "finished"
            self.write_status(note="campaign finished: stages A-E done")
            self.commit("Board campaign: finished")
            return EXIT_OK
        except CampaignStop as e:
            self.last_error = f"{now()} STOP RULE: {e}"
            self.write_status(note=f"STOPPED by a committed rule: {e}")
            self.save()
            self.commit(f"Board campaign: stopped by rule ({e})")
            return EXIT_STOP
        except InfraFailure as e:
            self.save()
            self.commit("Board campaign: stopped on an infrastructure failure")
            print(f"infrastructure failure, stopped cleanly: {e}", file=sys.stderr)
            return EXIT_INFRA
        finally:
            try:
                self.board.stop()
            except Exception:
                pass

    def _reapply_stop(self, stage: str) -> None:
        s = self.manifest["stages"][stage]
        if stage == "A" and (PRIMARY not in s["passing"] or len(s["passing"]) < 2):
            raise CampaignStop("stage A: screening left fewer than two models")
        if stage == "B" and not s.get("survivors"):
            raise CampaignStop("stage B: every model dropped after round 1")
        if stage == "C" and s.get("pick") is None:
            raise CampaignStop("stage C: no model passes bench.model_choice (stop and ask)")


def main() -> int:
    _board.drop_pythonpath()
    ap = argparse.ArgumentParser(description="FieldMind unattended board campaign")
    ap.add_argument("command", choices=["all", "status"])
    ap.add_argument("--out", default="results/board")
    ap.add_argument("--board", default="bench.campaign:RealBoard",
                    help="module:factory for the board interface (tests pass a fake)")
    ap.add_argument("--no-commit", action="store_true", help="tests only: do not git commit")
    ap.add_argument("--baseline-dir", default=None, help="tests only")
    args = ap.parse_args()
    out = Path(args.out)
    if args.command == "status":
        p = out / "STATUS.md"
        print(p.read_text() if p.exists() else "no campaign has run yet")
        return 0
    mod, fac = args.board.split(":")
    board = getattr(importlib.import_module(mod), fac)()
    return Campaign(out, board, commit=not args.no_commit,
                    baseline_dir=args.baseline_dir).run_all()


if __name__ == "__main__":
    # ONE copy of this module, however it is launched. `python -m bench.campaign`
    # runs this file as __main__; without this line, the later import of
    # "bench.campaign" (the default --board, and tests.fake_board) creates a second
    # copy whose InfraFailure is a different class, so `except InfraFailure` here
    # never matched and a timeout crashed the campaign (2026-10-03, three launches).
    sys.modules.setdefault("bench.campaign", sys.modules[__name__])
    if sys.modules["bench.campaign"] is not sys.modules[__name__]:
        raise SystemExit("bench.campaign was imported twice; launch it with "
                         "`python -m bench.campaign` only")
    raise SystemExit(main())
