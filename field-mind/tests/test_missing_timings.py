"""A count the runtime did not report is None end to end: the diagnostician's
repair path keeps it None (it used to add the two calls' counts), and the
bench aggregations skip it and report how many were missing."""
from __future__ import annotations

import json

from bench.board import mean_rates
from bench.model_choice import call_stats
from fieldmind.agent.l4_diagnose import Diagnostician
from fieldmind.runtime.llm_backend import LLMBackend, LLMReply

GOOD = json.dumps({"hypotheses": [{"cause": "c", "confidence": 0.5, "supports": []}]})


class _Seq(LLMBackend):
    """Replies in order: first a broken answer, then a valid one."""
    def __init__(self, replies):
        self.replies = list(replies)

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        return self.replies.pop(0)


def _diag(replies):
    d = Diagnostician(_Seq(replies), {"max_tokens": 60})
    return d.run(1, "facts", {"cases": [], "notes": [], "candidates": []}, [], "none")


def test_repair_path_keeps_unknown_counts_unknown():
    env = _diag([LLMReply(text="not json", prefill_tokens=None, decode_tokens=None),
                 LLMReply(text=GOOD, prefill_tokens=100, decode_tokens=20)])
    assert env.status == "ok"
    assert env.tokens == {"prefill": None, "decode": None}


def test_repair_path_still_sums_known_counts():
    env = _diag([LLMReply(text="not json", prefill_tokens=50, decode_tokens=7),
                 LLMReply(text=GOOD, prefill_tokens=100, decode_tokens=20)])
    assert env.tokens == {"prefill": 150, "decode": 27}


def _env(prefill, decode, status="ok"):
    return {"agent": "diagnostician", "status": status, "latency_ms": 1000.0,
            "tokens": {"prefill": prefill, "decode": decode}, "cited_facts": [],
            "prompt_tokens": 999, "backend": "npu", "model": "m"}


def test_model_choice_excludes_none_and_counts_it():
    run = {"diag_calls": 3, "parse_failure_rate": 0.0,
           "assessments": [{"facts": [], "envelopes": [_env(1000, 40), _env(None, None),
                                                        _env(2000, None)]}]}
    s = call_stats([run])
    assert s["prompt_tokens_mean"] == 1500.0 and s["prompt_tokens_missing"] == 1
    assert s["answer_tokens_mean"] == 40.0 and s["answer_tokens_missing"] == 2


def test_board_mean_rates_excludes_none_and_counts_it():
    r = mean_rates([{"prefill_tok_s": 900.0, "decode_tok_s": None},
                    {"prefill_tok_s": 700.0, "decode_tok_s": 12.0}])
    assert r["prefill_tok_s"] == 800.0 and r["prefill_tok_s_missing"] == 0
    assert r["decode_tok_s"] == 12.0 and r["decode_tok_s_missing"] == 1
    assert r["ttft_ms_server"] is None and r["ttft_ms_server_missing"] == 2


def test_each_backend_call_is_recorded_separately():
    env = _diag([LLMReply(text="not json", prefill_tokens=50, decode_tokens=256,
                          ttft_ms=60.0, decode_ms=20000.0),
                 LLMReply(text=GOOD, prefill_tokens=100, decode_tokens=20,
                          ttft_ms=110.0, decode_ms=1500.0)])
    assert [c["decode"] for c in env.calls] == [256, 20]          # not one summed 276
    assert [c["prefill_ms"] for c in env.calls] == [60.0, 110.0]
    assert [c["decode_ms"] for c in env.calls] == [20000.0, 1500.0]
