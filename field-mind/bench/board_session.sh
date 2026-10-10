#!/usr/bin/env bash
# One board session for the accuracy-fix work (reports/multi_accuracy_fix.md).
# Start it at the QIDK laptop, from the repo root, in a real terminal:
#
#     bench/board_session.sh
#
#   (0) PREFLIGHT (board only): both Llama 3B servers started, each answers a
#       trivial prompt as the expected model on the expected backend, placement
#       read from each server's own startup log, free memory and each server's
#       RSS logged (bench/board_preflight.py). Any failure stops the session.
#   (a) BASELINE 3: the current multi-agent (merge_rule model, i.e. unchanged),
#       quick set, Llama 3.2 3B on both lanes (configs/accuracy.yaml), every
#       prompt and raw reply saved.
#   (b) CHECK: every saved quick-set run exists, holds its prompts and replies,
#       and replays (bench/replay_multi.py --rule model --check) with 0
#       differences; on the board, every model call in them reports the 3B model
#       and the lane its agent is placed on. If (a) or (b) fails, it stops HERE.
#   (c) SINGLE-AGENT REFERENCE (decision 6): the single agent, Llama 3.2 3B on
#       the NPU lane, all 36 dev episodes, prompts saved; on the board, every
#       episode's model calls are checked for model and lane before it is kept.
#
# Resumable: an episode with a saved summary is skipped, in (a) and in (c); an
# interrupted episode starts over. Chip temperature is logged before and after
# every episode; the next episode waits until the chip is within MARGIN_C of the
# reading at the start of the batch, for at most MAX_WAIT_S (the campaign's
# protocol, scripts/presentation_all.sh). One thing on the board at a time.
#
# BACKEND=mock runs the same steps on the laptop without the board (output to
# results/accuracy_fix/session_mock_check; never an agent result).
set -uo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH
BACKEND=${BACKEND:-llamaserver}
LLAMA=Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf
if [ "$BACKEND" = llamaserver ]; then
    ROOT=${ROOT:-results/accuracy_fix/board_session}
else
    ROOT=${ROOT:-results/accuracy_fix/session_mock_check}
fi
OUT_A=$ROOT/step0_multi_quick
OUT_C=$ROOT/single_full_dev
QUICK=(dev_A01_fcv_seize dev_A02_fcv_seize_fast dev_B01_tube_leak dev_B02_tube_leak_fast
       dev_C01_wet_coal dev_C02_feeder_trip dev_D01_high_cv_coal dev_D03_high_cv_severe
       dev_N01_normal dev_N02_normal)
if [ -n "${SINGLE_EPISODES:-}" ]; then
    read -r -a ALL_DEV <<< "$SINGLE_EPISODES"
else
    ALL_DEV=($(ls data/episodes_dev))
fi
MARGIN_C=5
MAX_WAIT_S=900
on_board() { [ "$BACKEND" = llamaserver ]; }
mkdir -p "$OUT_A" "$OUT_C/tmp" logs

# ------------------------------------------------------------------ (0)
if on_board; then
    echo "=== (0) preflight: both lanes, Llama 3.2 3B ($(date +%Y-%m-%dT%H:%M:%S%z))"
    mkdir -p "$ROOT"
    if ! .venv/bin/python -m bench.board_preflight lanes "$ROOT/preflight_$(date +%Y%m%dT%H%M%S).json"; then
        echo "(0) PREFLIGHT FAILED: nothing was measured. See the problems above." >&2
        exit 3
    fi
fi

# ------------------------------------------------------------------ (a)
echo "=== (a) baseline 3: multi-agent, merge_rule model, quick set ($(date +%Y-%m-%dT%H:%M:%S%z))"
OVERLAY=configs/accuracy.yaml NPU_MODEL=$LLAMA CPU_MODEL=$LLAMA MERGE_RULE=model \
EPISODES_DIR=data/episodes_dev OUT=$OUT_A BACKEND=$BACKEND LOG_PROMPTS=1 \
    scripts/benchmark_multi_lockstep_all.sh "${QUICK[@]}"
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "(a) failed (exit $rc): stopping before (b) and (c)." >&2
    exit 1
fi

# ------------------------------------------------------------------ (b)
echo "=== (b) every quick-set run saved, with prompts, and replays with 0 differences"
for ep in "${QUICK[@]}"; do
    f="$OUT_A/${ep}_multi.run.json.gz"
    [ -s "$f" ] || { echo "(b) $ep: no saved run ($f): stopping before (c)." >&2; exit 1; }
    .venv/bin/python - "$f" <<'EOF' || { echo "(b) $ep: prompts or replies missing: stopping before (c)." >&2; exit 1; }
import gzip, json, sys
d = json.loads(gzip.decompress(open(sys.argv[1], "rb").read()))
d = d[0] if isinstance(d, list) else d
d = d["runs"][0] if "runs" in d else d
envs = [e for a in d["assessments"] for e in a["envelopes"]]
envs += [t["envelope"] for a in d["assessments"] for t in a["multi"]["text"]]
assert all(e.get("prompt") and e.get("raw_reply") is not None for e in envs), "prompt/reply missing"
assert all("belief_order" in a for a in d["assessments"]), "belief_order missing"
print(f"  {d['episode_id']}: {len(envs)} model calls, every prompt and reply saved")
EOF
done
.venv/bin/python bench/replay_multi.py --rule model --check "$OUT_A"/*_multi.run.json.gz \
    | tee "$ROOT/step0_replay_check.txt"
rc=${PIPESTATUS[0]}
if [ "$rc" -eq 0 ] && on_board; then
    .venv/bin/python -m bench.board_preflight runs multi "$OUT_A"/*_multi.run.json.gz \
        | tee "$ROOT/step0_model_check.txt"
    if [ "${PIPESTATUS[0]}" -ne 0 ]; then
        echo "(b) a model call ran on another model or lane: stopping before (c)." >&2
        exit 1
    fi
fi
if [ "$rc" -ne 0 ]; then
    echo "(b) the replay does not reproduce a saved run: stopping before (c)." >&2
    exit 1
fi

# ------------------------------------------------------------------ (c)
echo "=== (c) single-agent reference: Llama 3.2 3B on the NPU lane, ${#ALL_DEV[@]} dev episodes"
temp() {   # the hottest of the CPU and NPU zones
    .venv/bin/python -m bench.board temp | .venv/bin/python -c \
        'import json,sys; d=json.load(sys.stdin); print(max(d["cpu_max_c"], d["npu_max_c"]))'
}
note() {   # note <episode> <when> <temp> [waited_s]
    printf '{"episode": "%s", "when": "%s", "t": "%s", "max_c": %s, "cool_wait_s": %s}\n' \
        "$1" "$2" "$(date +%Y-%m-%dT%H:%M:%S%z)" "$3" "${4:-null}" >> "$OUT_C/temps_single.jsonl"
}
if on_board; then
    .venv/bin/python -m bench.board stop >/dev/null
    base=$(temp) || { echo "cannot read the board temperature (is it plugged in?)" >&2; exit 2; }
    note batch start "$base"
    echo "batch start: chip at $base C"
fi
for ep in "${ALL_DEV[@]}"; do
    if [ -f "$OUT_C/${ep}_single.summary.json" ]; then
        echo "$ep: already saved, skipping"
        continue
    fi
    if on_board; then
        waited=0
        while t=$(temp); ! .venv/bin/python -c "import sys; sys.exit(0 if $t <= $base + $MARGIN_C else 1)"; do
            [ "$waited" -ge "$MAX_WAIT_S" ] && break
            echo "$ep: chip at $t C, cooling to $base + $MARGIN_C C (waited $waited s)"
            sleep 30
            waited=$((waited + 30))
        done
        .venv/bin/python -m bench.board stop >/dev/null
        .venv/bin/python -m bench.board start npu "$LLAMA" > "logs/session_single_npu.json" \
            && grep -q '"ready_after_s": [0-9]' "logs/session_single_npu.json" \
            || { echo "the npu lane did not come up:"; cat "logs/session_single_npu.json"; exit 2; }
        .venv/bin/python -m bench.board log npu | grep -E "offload|HTP0 model buffer|n_ctx_slot" \
            > "$OUT_C/${ep}_single.lanes.txt"
        note "$ep" before "$t" "$waited"
    fi
    echo "=== $ep  started $(date +%Y-%m-%dT%H:%M:%S%z)  single agent  backend $BACKEND" | tee "$OUT_C/${ep}_single.log"
    PYTHONUNBUFFERED=1 .venv/bin/python run_demo.py --backend "$BACKEND" --arch single \
        --episodes-dir data/episodes_dev --episode "$ep" --log-prompts \
        --tag "$ep" --out "$OUT_C/tmp" 2>&1 | tee -a "$OUT_C/${ep}_single.log"
    rc=${PIPESTATUS[0]}
    if on_board; then
        note "$ep" after "$(temp)"
        .venv/bin/python -m bench.board stop >/dev/null
    fi
    runs="$OUT_C/tmp/runs_${BACKEND}_$ep.json"
    if [ "$rc" -ne 0 ] || [ ! -s "$runs" ]; then
        echo "$ep did not finish (exit $rc): stopping. Run bench/board_session.sh again to resume." >&2
        exit 1
    fi
    gzip -c "$runs" > "$OUT_C/${ep}_single.run.json.gz" && rm "$runs"
    if on_board && ! .venv/bin/python -m bench.board_preflight runs single "$OUT_C/${ep}_single.run.json.gz"; then
        echo "$ep: a model call ran on another model or lane: not kept, stopping." >&2
        exit 1
    fi
    mv "$OUT_C/tmp/summary_${BACKEND}_$ep.json" "$OUT_C/${ep}_single.summary.json"
    echo "$ep: saved"
done
echo "=== session complete ($(date +%Y-%m-%dT%H:%M:%S%z)): $ROOT"
