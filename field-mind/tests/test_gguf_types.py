"""bench/gguf_types.py on synthetic GGUF headers built here byte by byte."""
from __future__ import annotations

import struct

import pytest

from bench.gguf_types import read_header, report


def _s(x: str) -> bytes:
    b = x.encode()
    return struct.pack("<Q", len(b)) + b


def _gguf(tensors, kv=(("general.architecture", "llama"),)) -> bytes:
    out = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kv))
    for k, v in kv:
        out += _s(k) + struct.pack("<I", 8) + _s(v)
    for name, dims, t in tensors:
        out += _s(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        out += struct.pack("<IQ", t, 0)
    return out + b"\0" * 64                  # weights would follow; never read


Q4_0, Q8_0, F32, Q6_K, F16 = 2, 8, 0, 14, 1


def test_pure_q4_file_passes_and_norms_are_not_flagged():
    h = read_header(_gguf([("token_embd.weight", [2048, 32000], Q4_0),
                           ("blk.0.attn_q.weight", [2048, 2048], Q4_0),
                           ("blk.0.ffn_down.weight", [8192, 2048], Q8_0),
                           ("blk.0.attn_norm.weight", [2048], F32)]))
    r = report(h)
    assert r["flagged"] == [] and r["verdict"].startswith("OK")
    assert r["one_d_types"] == {"F32": 1}
    assert r["output.weight"].startswith("absent") and r["token_embd.weight"] == "Q4_0"


def test_k_quant_output_and_f16_embedding_are_flagged():
    h = read_header(_gguf([("token_embd.weight", [2048, 32000], F16),
                           ("output.weight", [2048, 32000], Q6_K),
                           ("blk.0.attn_q.weight", [2048, 2048], Q4_0)]))
    r = report(h)
    assert {t["name"] for t in r["flagged"]} == {"token_embd.weight", "output.weight"}
    assert r["output.weight"] == "Q6_K" and r["token_embd.weight"] == "F16"
    assert r["verdict"].startswith("FLAG: 2")


def test_truncated_prefix_says_so_and_bad_magic_is_rejected():
    full = _gguf([("blk.0.attn_q.weight", [2048, 2048], Q4_0)] * 3)
    with pytest.raises(EOFError):
        read_header(full[:40])
    with pytest.raises(ValueError):
        read_header(b"GGML" + full[4:])
