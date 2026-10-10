"""
List the tensor types in a GGUF file and flag any that are not Q4_0 / Q8_0.
Stdlib only; reads the header (metadata + tensor infos), never the weights, so a
prefix of the file is enough:

  adb exec-out "head -c 33554432 /data/local/tmp/<dir>/<model>.gguf" > /tmp/head.gguf
  .venv/bin/python -m bench.gguf_types /tmp/head.gguf

Why: multi-agent plan rule "only Q4_0 or Q8_0 (K-quants silently fall back to the
CPU on HTP)". A file NAMED Q4_0 can still carry K-quant or F16 tensors;
llama-quantize keeps output.weight and token_embd at higher precision by default
unless run with --pure. 1-D tensors (norms, biases) are never quantized by
llama-quantize and are reported separately, not flagged.

Format: GGUF v2/v3 (ggml/docs/gguf.md): magic, version, tensor_count, kv_count,
kv pairs, then per tensor: name, n_dims, dims, ggml_type, offset.
"""
from __future__ import annotations

import json
import struct
import sys

ALLOWED = {"Q4_0", "Q8_0"}

# ggml_type enum (ggml/include/ggml.h). Ids not listed print as type_<n>.
GGML_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 6: "Q5_0", 7: "Q5_1", 8: "Q8_0",
              9: "Q8_1", 10: "Q2_K", 11: "Q3_K", 12: "Q4_K", 13: "Q5_K", 14: "Q6_K",
              15: "Q8_K", 16: "IQ2_XXS", 17: "IQ2_XS", 18: "IQ3_XXS", 19: "IQ1_S",
              20: "IQ4_NL", 21: "IQ3_S", 22: "IQ2_S", 23: "IQ4_XS", 24: "I8", 25: "I16",
              26: "I32", 27: "I64", 28: "F64", 29: "IQ1_M", 30: "BF16"}

# gguf metadata value types -> struct format (8 string, 9 array handled apart)
_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
           10: "<Q", 11: "<q", 12: "<d"}


class _R:
    def __init__(self, buf: bytes):
        self.b, self.i = buf, 0

    def take(self, fmt: str):
        n = struct.calcsize(fmt)
        if self.i + n > len(self.b):
            raise EOFError("header truncated: read a longer prefix of the file")
        v = struct.unpack_from(fmt, self.b, self.i)[0]
        self.i += n
        return v

    def string(self) -> str:
        n = self.take("<Q")
        if self.i + n > len(self.b):
            raise EOFError("header truncated: read a longer prefix of the file")
        s = self.b[self.i:self.i + n].decode("utf-8", "replace")
        self.i += n
        return s

    def value(self, t: int):
        if t in _SCALAR:
            return self.take(_SCALAR[t])
        if t == 8:
            return self.string()
        if t == 9:
            et, n = self.take("<I"), self.take("<Q")
            return [self.value(et) for _ in range(n)]
        raise ValueError(f"unknown gguf value type {t}")


def read_header(buf: bytes) -> dict:
    r = _R(buf)
    if r.take("<4s") != b"GGUF":
        raise ValueError("not a GGUF file")
    version, n_t, n_kv = r.take("<I"), r.take("<Q"), r.take("<Q")
    meta = {}
    for _ in range(n_kv):
        k = r.string()
        v = r.value(r.take("<I"))
        meta[k] = v if not isinstance(v, list) or len(v) <= 8 else f"[array of {len(v)}]"
    tensors = []
    for _ in range(n_t):
        name = r.string()
        dims = [r.take("<Q") for _ in range(r.take("<I"))]
        t = r.take("<I")
        r.take("<Q")                                    # offset (unused)
        tensors.append({"name": name, "dims": dims, "type": GGML_TYPES.get(t, f"type_{t}")})
    return {"version": version, "meta": meta, "tensors": tensors}


def report(h: dict) -> dict:
    counts: dict[str, int] = {}
    flagged, one_d = [], {}
    for t in h["tensors"]:
        counts[t["type"]] = counts.get(t["type"], 0) + 1
        if len(t["dims"]) <= 1 or sum(d > 1 for d in t["dims"]) <= 1:
            one_d[t["type"]] = one_d.get(t["type"], 0) + 1     # norms / biases
        elif t["type"] not in ALLOWED:
            flagged.append(t)
    key = {n: t["type"] for t in h["tensors"] for n in ("output.weight", "token_embd.weight")
           if t["name"] == n}
    return {"gguf_version": h["version"],
            "file_type_meta": h["meta"].get("general.file_type"),
            "architecture": h["meta"].get("general.architecture"),
            "name": h["meta"].get("general.name"),
            "n_tensors": len(h["tensors"]), "type_counts": counts,
            "one_d_types": one_d,
            "output.weight": key.get("output.weight", "absent (tied to token_embd)"),
            "token_embd.weight": key.get("token_embd.weight"),
            "flagged": [{"name": t["name"], "type": t["type"], "dims": t["dims"]}
                        for t in flagged],
            "verdict": "OK: every matrix is Q4_0/Q8_0" if not flagged
                       else f"FLAG: {len(flagged)} matrix tensors outside Q4_0/Q8_0"}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    for path in sys.argv[1:]:
        with open(path, "rb") as fh:
            rep = report(read_header(fh.read()))
        n = len(rep["flagged"])
        if n > 12:
            rep["flagged"] = rep["flagged"][:12] + [f"... {n - 12} more"]
        print(path)
        print(json.dumps(rep, indent=1))
