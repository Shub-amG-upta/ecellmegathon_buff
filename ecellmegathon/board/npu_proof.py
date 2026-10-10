"""
Prove where the model runs, from llama-server's own startup log (needs -lv 4).

Ported from FieldMind (bench/board_preflight.py, check_lane_log), generalised
to any model. NPU: every layer offloaded (including the output layer) and a
nonzero HTP0 model buffer. CPU: nothing offloaded and no HTP0 buffer.

This is the evidence for judging criterion 1 ("all AI models execute on-device
using Snapdragon acceleration"): show these lines live, and save them with
every benchmark result.
"""
from __future__ import annotations

import re


def placement(log: str) -> dict:
    """The placement facts the startup log states. A missing line is None."""
    m = re.search(r"offloaded (\d+)/(\d+) layers", log)
    htp = re.search(r"HTP0 model buffer size =\s*([0-9.]+)", log)
    return {"offloaded": int(m.group(1)) if m else None,
            "layers": int(m.group(2)) if m else None,
            "htp0_model_mib": float(htp.group(1)) if htp else None}


def check_lane_log(lane: str, log: str, model_file: str | None = None) -> list[str]:
    """Problems with the placement; an empty list means confirmed."""
    p, f = [], placement(log)
    if model_file and model_file not in log:
        p.append(f"{lane}: startup log does not name {model_file}")
    if f["offloaded"] is None:
        p.append(f"{lane}: no 'offloaded N/M layers' line (was -lv 4 passed?)")
    elif lane == "npu" and (f["offloaded"] != f["layers"] or not f["htp0_model_mib"]):
        p.append(f"{lane}: not fully on HTP0 (offloaded {f['offloaded']}/{f['layers']}, "
                 f"HTP0 buffer {f['htp0_model_mib']})")
    elif lane == "cpu" and (f["offloaded"] != 0 or f["htp0_model_mib"] is not None):
        p.append(f"{lane}: CPU lane has offloaded layers or an HTP0 buffer")
    return p


def parse_meminfo(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemFree", "MemAvailable", "SwapTotal", "SwapFree"):
            out[k + "_kB"] = int(v.split()[0])
    return out
