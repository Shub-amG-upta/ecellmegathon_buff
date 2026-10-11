"""
Can the board run two agents at the same time? (multi-agent speed, 2026-10-11)

A second agent only saves time if it runs *while* the first one runs. Both models sit on the same Hexagon NPU
(HTP0), and the CPU is a third option; whether two llama-servers actually overlap there has never been measured.
This probe answers it with the app's real first-turn call (system prompt, a first description, the JSON-schema
answer), each scenario `--reps` times with a different description:

  solo-main      Llama alone on the NPU (lane npu)
  solo-type      Gemma alone on the NPU (lane type), the v7 type call; and with TYPE_PREFILL (pre-filled {"t":")
  par-main+type  both at once, both on the NPU                         -> TYPE_PARALLEL=1 (app/router.py)
  solo-cpu       Llama on the CPU (lane cpu)                            (--cpu)
  par-main+cpu   Llama on the NPU and Llama on the CPU at once          (--cpu) -> agents on NPU + CPU

Reported per pair: each call's slowdown vs solo, and **overlap** = (solo A + solo B - parallel wall) / min(solo A,
solo B): 1.0 = the shorter call is completely hidden, 0 = the board just takes turns (no gain from parallel agents).

Decision, written before any run: TYPE_PARALLEL goes to a full board eval only if par-main+type has overlap ≥ 0.5
and the main call slows by ≤ 15%. Agents on NPU + CPU are worth building only if the CPU lane decodes at ≥ 60% of
the NPU's tokens/s and the NPU call slows by ≤ 15% while the CPU runs. Otherwise: one model call at a time stays.

  .venv/bin/python -m board.parallel_probe                    # npu + type lanes (starts them)
  .venv/bin/python -m board.parallel_probe --cpu --reps 3     # also the CPU lane
  .venv/bin/python -m board.parallel_probe --no-start         # lanes already running

Label: BOARD. Saves JSON next to the benchmark runs. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import threading
import time
from pathlib import Path

from board import board as bb
from app.backends import LlamaServerBackend
from app.form import llm_json_schema, load_schema
from app.prompt import system_prompt, user_message
from app.router import TYPE_PREFIX, type_gbnf, type_schema

ROOT = Path(__file__).resolve().parent.parent
MAIN = "Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf"
TYPE = "gemma-3-4b-it-Q4_0-pure-embq8.gguf"
# first descriptions from the eval set's shapes (not scored here: only time matters)
TEXTS = [
    "There was a water leak near Conference Room 2 around 10 AM. Maintenance has already isolated the area.",
    "A box fell off the top shelf in the store room and hit Ravi on the shoulder about twenty minutes ago.",
    "One of the nurses slipped on the wet floor in the ward 3 corridor last night at 8, she hurt her knee.",
    "Forklift reversed into a rack in bay 4 this morning, some cartons fell, nobody was hurt.",
    "Strong chemical smell in lab 2, two people felt dizzy, we opened the windows and evacuated.",
]


def timed(fn) -> dict:
    t0 = time.perf_counter()
    r = fn()
    wall = (time.perf_counter() - t0) * 1000
    tps = (r.decode_tokens / r.decode_ms * 1000) if r.decode_tokens and r.decode_ms else None
    return {"wall_ms": round(wall, 1), "status": r.status, "prefill_ms": r.prefill_ms, "decode_ms": r.decode_ms,
            "prefill_tokens": r.prefill_tokens, "decode_tokens": r.decode_tokens,
            "decode_tok_s": round(tps, 2) if tps else None, "text": r.text[:120], "error": r.error}


def together(a, b) -> tuple[dict, dict, float]:
    out: dict = {}
    t0 = time.perf_counter()
    ta = threading.Thread(target=lambda: out.__setitem__("a", timed(a)))
    tb = threading.Thread(target=lambda: out.__setitem__("b", timed(b)))
    ta.start(); tb.start(); ta.join(); tb.join()
    return out["a"], out["b"], round((time.perf_counter() - t0) * 1000, 1)


def med(rows: list[dict], k: str = "wall_ms") -> float | None:
    xs = [r[k] for r in rows if r.get(k) is not None]
    return round(st.median(xs), 1) if xs else None


def pair_summary(solo_a: float, solo_b: float, par_a: float, par_b: float, par_wall: float) -> dict:
    return {"solo_a_ms": solo_a, "solo_b_ms": solo_b, "parallel_wall_ms": par_wall,
            "sequential_ms": round(solo_a + solo_b, 1),
            "slowdown_a": round(par_a / solo_a, 3), "slowdown_b": round(par_b / solo_b, 3),
            "overlap": round((solo_a + solo_b - par_wall) / min(solo_a, solo_b), 3)}


def main() -> int:
    bb.drop_pythonpath()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--cpu", action="store_true", help="also measure the CPU lane (Llama on the CPU)")
    ap.add_argument("--no-start", action="store_true", help="use lanes that are already running")
    ap.add_argument("--main", default=MAIN)
    ap.add_argument("--type", default=TYPE)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    lanes = {"npu": a.main, "type": a.type, **({"cpu": a.main} if a.cpu else {})}
    started = {}
    if not a.no_start:
        bb.stop_lanes()
        for lane, model in lanes.items():
            info = bb.start_lane(lane, model)
            info["ready_after_s"] = bb.wait_health(info["url"])
            started[lane] = info
            print(f"{lane}: ready after {info['ready_after_s']} s", flush=True)
    url = {lane: f"http://127.0.0.1:{bb.LANE_PORT[lane]}" for lane in lanes}
    placement = {}
    for lane in ("npu", "type"):
        from board.npu_proof import check_lane_log, placement as place
        log = bb.lane_log(lane)
        placement[lane] = {**place(log), "problems": check_lane_log(lane, log)}
        if placement[lane]["problems"]:
            print(f"STOP: lane {lane} is not fully on HTP0: {placement[lane]['problems']}", file=sys.stderr)
            return 1

    schema = load_schema()
    system = system_prompt(schema)
    js = llm_json_schema(schema)
    types = next(f for f in schema["fields"] if f.get("key") == "t")["values"]
    be = {lane: LlamaServerBackend(url=url[lane], lane=lane, model_file=lanes[lane], cache_prompt=True)
          for lane in lanes}

    def msg(i):
        return user_message({}, None, TEXTS[i % len(TEXTS)])

    def call_main(lane, i):
        return lambda: be[lane].generate(msg(i), max_tokens=128, json_schema=js, system=system)

    def call_type(i, prefill=False):
        if prefill:
            return lambda: be["type"].generate(msg(i), max_tokens=8, grammar=type_gbnf(types), system=system,
                                               prefix=TYPE_PREFIX)
        return lambda: be["type"].generate(msg(i), max_tokens=16, json_schema=type_schema(types), system=system)

    # warm-up: read the system prompt once per lane (the app does this on its first turn)
    for lane in lanes:
        (call_type(99) if lane == "type" else call_main(lane, 99))()

    res: dict = {k: [] for k in ("solo_main", "solo_type", "solo_type_prefill", "par_main", "par_type",
                                 "par_wall", "solo_cpu", "par2_main", "par2_cpu", "par2_wall")}
    temps = []
    for i in range(a.reps):
        temps.append(bb.chip_temperature())
        res["solo_main"].append(timed(call_main("npu", i)))
        res["solo_type"].append(timed(call_type(i)))
        res["solo_type_prefill"].append(timed(call_type(i, prefill=True)))
        pm, pt, w = together(call_main("npu", i), call_type(i))
        res["par_main"].append(pm); res["par_type"].append(pt); res["par_wall"].append({"wall_ms": w})
        if a.cpu:
            res["solo_cpu"].append(timed(call_main("cpu", i)))
            pm, pc, w = together(call_main("npu", i), call_main("cpu", i))
            res["par2_main"].append(pm); res["par2_cpu"].append(pc); res["par2_wall"].append({"wall_ms": w})
        print(f"rep {i + 1}/{a.reps}: main {res['solo_main'][-1]['wall_ms']} ms · type {res['solo_type'][-1]['wall_ms']}"
              f" ms (pre-filled {res['solo_type_prefill'][-1]['wall_ms']} ms) · both at once {res['par_wall'][-1]['wall_ms']} ms",
              flush=True)

    summary = {"main+type (both NPU)": pair_summary(med(res["solo_main"]), med(res["solo_type"]), med(res["par_main"]),
                                                    med(res["par_type"]), med(res["par_wall"])),
               "type call": {"json_schema_ms": med(res["solo_type"]), "prefilled_ms": med(res["solo_type_prefill"]),
                             "json_schema_decode_tokens": med(res["solo_type"], "decode_tokens"),
                             "prefilled_decode_tokens": med(res["solo_type_prefill"], "decode_tokens")}}
    if a.cpu:
        summary["main NPU + main CPU"] = pair_summary(med(res["solo_main"]), med(res["solo_cpu"]), med(res["par2_main"]),
                                                      med(res["par2_cpu"]), med(res["par2_wall"]))
        summary["decode tok/s"] = {"npu": med(res["solo_main"], "decode_tok_s"), "cpu": med(res["solo_cpu"], "decode_tok_s")}
    p = summary["main+type (both NPU)"]
    verdict = {"TYPE_PARALLEL worth a board eval": p["overlap"] >= 0.5 and p["slowdown_a"] <= 1.15}
    if a.cpu:
        q, d = summary["main NPU + main CPU"], summary["decode tok/s"]
        verdict["agents on NPU + CPU worth building"] = bool(
            d["cpu"] and d["npu"] and d["cpu"] >= 0.6 * d["npu"] and q["slowdown_a"] <= 1.15)

    soc = (bb._adb("shell", "getprop ro.soc.model").stdout.strip() or "unknown")
    out_dir = Path(a.out) if a.out else ROOT / "benchmarks" / f"{time.strftime('%Y-%m-%d')}_board_{soc}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"parallel_probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps({"label": "BOARD", "laptop_time": bb.laptop_time(), "lanes": lanes,
                                "started": started, "placement": placement, "reps": a.reps,
                                "temperature_before_each_rep": temps, "summary": summary, "verdict": verdict,
                                "calls": res}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"summary": summary, "verdict": verdict}, indent=1, ensure_ascii=False))
    print(f"saved {path}")
    if not a.no_start:
        bb.stop_lanes()
    return 0


if __name__ == "__main__":
    sys.exit(main())
