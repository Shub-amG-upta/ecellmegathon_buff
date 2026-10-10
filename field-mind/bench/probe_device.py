#!/usr/bin/env python3
"""
=============================================================================
 DEVICE PROBE  --  run this on the QIDK BEFORE trusting any device number
=============================================================================

LiteRTBackend is written against litert_lm_main's CLI as documented. Flag
names and the timing-report format change between LiteRT-LM releases, and a
wrong flag does not error -- it silently falls back to CPU, which produces a
plausible-looking number that is completely wrong.

This script checks, in order:
  1. adb sees the board
  2. the binary and model are actually on the device
  3. the QNN HTP libraries for Hexagon v75 are present
  4. the binary accepts the flags we build
  5. the timing output parses

Anything that fails here invalidates every S-series metric until it is fixed.

    python bench/probe_device.py
=============================================================================
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fieldmind.runtime.llm_backend import LiteRTBackend   # noqa: E402
from bench.board import clean_env, drop_pythonpath, laptop_time   # noqa: E402

DEVICE_DIR = "/data/local/tmp/llm"

# Which accelerator this probe is validating. Missing libraries for the OTHER
# two are reported but do not fail the probe -- a CPU run is not invalidated by
# the absence of the HTP stack.
ACCELERATOR = "cpu"


def sh(*args) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=60,
                           env=clean_env())
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:
        return 1, str(e)


def main():
    ok = True

    print(f"laptop time {laptop_time()} (board time is never logged: its clock is wrong)")
    print("1. adb devices")
    rc, out = sh("adb", "devices")
    print("  ", out.replace("\n", "\n   "))
    if rc != 0 or "device" not in out.split("\n", 1)[-1]:
        print("   FAIL: no device. Everything below is meaningless.")
        return 1

    print("\n2. binary and model on device")
    rc, out = sh("adb", "shell", f"ls -la {DEVICE_DIR}")
    print("  ", out.replace("\n", "\n   ")[:900])
    for needed in ("litert_lm_main", ".litertlm"):
        if needed not in out:
            print(f"   FAIL: {needed} not found in {DEVICE_DIR}")
            ok = False

    print("\n3. accelerator libraries")
    rc, out = sh("adb", "shell", f"ls {DEVICE_DIR}")
    missing_npu = [l for l in ("libQnnHtpV75Skel.so", "libQnnHtpV75Stub.so")
                   if l not in out]
    missing_gpu = [l for l in ("libLiteRtClGlAccelerator.so",
                               "libLiteRtVulkanAccelerator.so")
                   if l not in out]
    for label, missing in (("NPU", missing_npu), ("GPU", missing_gpu)):
        if not missing:
            print(f"   {label}: libraries present")
            continue
        # A known-but-unregisterable backend does not error -- litert_lm_main
        # WARNS and runs on CPU. Confirmed on this board for both NPU and GPU.
        note = (f"   {label}: UNAVAILABLE (missing {', '.join(missing)}) "
                f"-> --backend={label} would silently return CPU numbers")
        print(note)
        if ACCELERATOR == label.lower():
            print(f"   FAIL: probing {label} but its libraries are absent.")
            ok = False

    print("\n4. flag acceptance")
    b = LiteRTBackend(mode="adb", device_dir=DEVICE_DIR,
                      accelerator=ACCELERATOR, model_file="model.litertlm")
    # generate() pushes the prompt to _prompt_<role>.txt, so show that path
    # rather than a name the probe never actually writes.
    print("   command:", b._device_command(f"{DEVICE_DIR}/_prompt_probe.txt"))
    reply = b.generate("Reply with the single word: ok", role="probe", max_tokens=32)
    print(f"   status={reply.status} latency={reply.latency_ms:.0f}ms "
          f"ttft={reply.ttft_ms:.0f}ms "
          f"prefill={reply.prefill_tokens} decode={reply.decode_tokens}")
    print(f"   text: {reply.text[:200]!r}")
    if reply.status != "ok":
        print(f"   FAIL: {reply.error[:300]}")
        ok = False
    elif reply.prefill_tokens == 0 and reply.decode_tokens == 0:
        print("   WARN: token counts did not parse. Update "
              "LiteRTBackend._parse_output to match this build's timing format,")
        print("         otherwise the per-stage cost model has no input.")
        ok = False
    elif not reply.text.strip():
        print("   WARN: no generated text survived parsing -- check "
              "_extract_text against this build's prompt echo.")
        ok = False

    print("\n5. thermal baseline (log this with every measurement -- results are")
    print("   not repeatable without it, see Plan §14)")
    rc, out = sh("adb", "shell",
                 "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | head -5")
    print("  ", out.replace("\n", " ") or "(unavailable)")

    print("\n" + ("PROBE PASSED" if ok else
                  "PROBE FAILED -- do not report device numbers yet"))
    return 0 if ok else 1


if __name__ == "__main__":
    drop_pythonpath()
    sys.exit(main())