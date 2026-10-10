#!/usr/bin/env bash
# BENCHMARK, BACK TO BACK (lockstep): the presentation episodes on the
# MULTI-AGENT system with NO wall-clock pacing. Each tick waits for all of its
# model answers, publishes, and the next tick starts at once -- the way the
# single-agent presentation runs were made (scripts/presentation_all.sh), so
# the two can be put side by side. This is NOT the real-time demonstration
# (scripts/presentation_multi_all.sh, a tick every 30 s that never waits).
#
#     scripts/benchmark_multi_lockstep_all.sh                       # all six
#     scripts/benchmark_multi_lockstep_all.sh ep_A01_fcv_seize      # only these
#
# Same setup as the demonstration (configs/fast.yaml: Llama 3.2 3B on the NPU
# lane for the water and heat diagnosticians, Gemma 3 1B on the CPU lane for
# the verifier and text reader; lanes restarted fresh before every episode
# with -c 4096 -np 1 -fit off --cache-ram 0 -lv 4). What differs in lockstep:
#   * model calls run one after another, never two lanes at once;
#   * an answer is used in the tick that asked for it (no one-tick delay) and
#     the verifier runs on every tick its trigger fires;
#   * the board works without pauses, so it heats like the single-agent runs.
# The job start / finish times inside `multi.results` are the scheduler's
# SIMULATED clock in this mode; the real time of every call is `latency_ms`
# in its envelope, and the real time of a tick is `tick_latency_ms`.
#
# Answers are held to the grammars of fieldmind/multi/grammar.py (multi.grammar,
# on in configs/fast.yaml since 2026-10-05), so results go to a "grammar"
# folder and are never mixed with, or resumed over, the runs made before that.
#
# Every result file carries the suffix _multi (the single agent's carry _single), so a
# file copied out of its folder still says which agent made it.
# Per episode, in $OUT (a folder of its own, never mixed with the real-time run):
#   <ep>_multi.log            the live display, written as it is shown
#   <ep>_multi.ticks.jsonl    every tick's full assessment, appended as it is published
#   <ep>_multi.run.json.gz    every tick (written when the episode finishes)
#   <ep>_multi.summary.json   the evaluation
#   <ep>_multi.lanes.txt      the lane startup lines that prove NPU / CPU placement
#   temps_multi.jsonl       chip temperature before and after
# An episode that already has <ep>_multi.summary.json is skipped, so the same command
# resumes after Ctrl-C (the interrupted episode starts over). The next episode
# waits until the chip is within 5 C of the reading taken at the start of this
# batch, for at most 900 s (the single-agent script's margin and limit).
# Live view: .venv/bin/python bench/stage_monitor_multi.py   (second terminal)
# BACKEND=mock checks this script on the laptop without the board; mock output
# goes to its own folder and is never an agent result.
set -uo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH
# Overridable (accuracy-fix work, reports/multi_accuracy_fix.md):
#   OVERLAY=configs/accuracy.yaml CPU_MODEL=<Llama GGUF> EPISODES_DIR=data/episodes_dev OUT=... \
#   scripts/benchmark_multi_lockstep_all.sh dev_A01_fcv_seize ...
# Every run saves every prompt and raw model reply (--log-prompts), so it can be
# replayed offline (bench/replay_multi.py). LOG_PROMPTS=0 turns that off.
NPU_MODEL=${NPU_MODEL:-Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf}
CPU_MODEL=${CPU_MODEL:-gemma-3-1b-it-qat-Q4_0-pure-embq8.gguf}
OVERLAY=${OVERLAY:-configs/fast.yaml}
EPISODES_DIR=${EPISODES_DIR:-data/episodes}
LOG_PROMPTS=${LOG_PROMPTS:-1}
PROMPT_FLAG=(); [ "$LOG_PROMPTS" = 1 ] && PROMPT_FLAG=(--log-prompts)
MERGE_RULE=${MERGE_RULE:-}            # model | belief_only | tiebreak | nudge; empty = the overlay's
RULE_FLAG=(); [ -n "$MERGE_RULE" ] && RULE_FLAG=(--merge-rule "$MERGE_RULE")
BACKEND=${BACKEND:-llamaserver}
if [ "$BACKEND" = llamaserver ]; then
    OUT=${OUT:-results/presentation_benchmark/multi_agent_LOCKSTEP_grammar_llama32-3b_gemma3-1b}
else
    OUT=${OUT:-results/benchmark_lockstep_mock_check}
fi
EPISODES=("$@")
[ ${#EPISODES[@]} -eq 0 ] && EPISODES=(ep_A01_fcv_seize ep_B01_tube_leak ep_C01_wet_coal
                                       ep_D01_high_cv_coal ep_E01_fouling_drift ep_N01_normal)
MARGIN_C=5
MAX_WAIT_S=900
on_board() { [ "$BACKEND" = llamaserver ]; }

temp() {   # prints the hottest of the CPU and NPU zones
    .venv/bin/python -m bench.board temp | .venv/bin/python -c \
        'import json,sys; d=json.load(sys.stdin); print(max(d["cpu_max_c"], d["npu_max_c"]))'
}
note() {   # note <episode> <when> <temp> [waited_s]
    printf '{"episode": "%s", "when": "%s", "t": "%s", "max_c": %s, "cool_wait_s": %s}\n' \
        "$1" "$2" "$(date +%Y-%m-%dT%H:%M:%S%z)" "$3" "${4:-null}" >> "$OUT/temps_multi.jsonl"
}
start_lanes() {   # start_lanes <episode>: both lanes fresh, placement checked from the logs
    local ep=$1 lane model off
    .venv/bin/python -m bench.board stop >/dev/null
    for lane in npu cpu; do
        model=$NPU_MODEL; [ "$lane" = cpu ] && model=$CPU_MODEL
        echo "starting the $lane lane ($model) ..."
        .venv/bin/python -m bench.board start "$lane" "$model" > "logs/presentation_multi_lane_$lane.json" \
            && grep -q '"ready_after_s": [0-9]' "logs/presentation_multi_lane_$lane.json" \
            || { echo "the $lane lane did not come up:"; cat "logs/presentation_multi_lane_$lane.json"; return 2; }
    done
    {
        echo "== npu ($NPU_MODEL)"
        .venv/bin/python -m bench.board log npu | grep -E "offload|HTP0 model buffer|n_ctx_slot"
        echo "== cpu ($CPU_MODEL)"
        .venv/bin/python -m bench.board log cpu | grep -E "offload|HTP0 model buffer|n_ctx_slot|n_threads ="
    } > "$OUT/${ep}_multi.lanes.txt"
    # NPU: a nonzero HTP0 buffer and every layer offloaded; CPU: no HTP0 buffer
    off=$(sed -n '/^== npu/,/^== cpu/s/.*offloaded \([0-9]*\)\/\([0-9]*\) layers.*/\1 \2/p' "$OUT/${ep}_multi.lanes.txt")
    if ! sed -n '/^== npu/,/^== cpu/p' "$OUT/${ep}_multi.lanes.txt" | grep -q "HTP0 model buffer size = *[1-9]" \
        || [ -z "$off" ] || [ "${off% *}" != "${off#* }" ] \
        || sed -n '/^== cpu/,$p' "$OUT/${ep}_multi.lanes.txt" | grep -q "HTP0 model buffer"; then
        echo "lane placement is not as expected (NPU fully offloaded, CPU with no HTP0 buffer):" >&2
        cat "$OUT/${ep}_multi.lanes.txt" >&2
        return 2
    fi
}

mkdir -p "$OUT" logs
if on_board; then
    if pgrep -f '[p]ython -m bench.campaign all|[p]resentation_multi_all.sh|[r]un_demo.py .*--backend llamaserver|[s]tage_monitor.py' >/dev/null; then
        echo "another board run is going (campaign, real-time demonstration or single agent):" >&2
        echo "wait for it or stop it first (one thing on the board at a time)" >&2
        exit 1
    fi
    .venv/bin/python -m bench.board stop >/dev/null
    base=$(temp) || { echo "cannot read the board temperature (is it plugged in?)" >&2; exit 2; }
    note batch start "$base"
    echo "batch start: chip at $base C"
fi

for ep in "${EPISODES[@]}"; do
    if [ -f "$OUT/${ep}_multi.summary.json" ]; then
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
        start_lanes "$ep" || { .venv/bin/python -m bench.board stop >/dev/null; exit 2; }
        note "$ep" before "$t" "$waited"
    fi
    # ticks of an interrupted attempt are kept under another name, never appended to
    [ -f "$OUT/${ep}_multi.ticks.jsonl" ] && mv "$OUT/${ep}_multi.ticks.jsonl" "$OUT/${ep}_multi.ticks.interrupted-$(date +%Y%m%dT%H%M%S).jsonl"
    echo "=== $ep  started $(date +%Y-%m-%dT%H:%M:%S%z)  BACK TO BACK (lockstep)  backend $BACKEND" | tee "$OUT/${ep}_multi.log"
    # systemd-inhibit keeps the (Linux) board laptop awake; absent elsewhere
    INHIBIT=(); command -v systemd-inhibit >/dev/null && INHIBIT=(systemd-inhibit --what=sleep:idle --why="FieldMind $ep")
    PYTHONUNBUFFERED=1 ${INHIBIT[@]+"${INHIBIT[@]}"} \
        .venv/bin/python run_demo.py --backend "$BACKEND" --arch multi \
        --overlay "$OVERLAY" --mode lockstep --episodes-dir "$EPISODES_DIR" ${PROMPT_FLAG[@]+"${PROMPT_FLAG[@]}"} ${RULE_FLAG[@]+"${RULE_FLAG[@]}"} \
        --episode "$ep" --verbose --live-ticks "$OUT/${ep}_multi.ticks.jsonl" \
        --tag "$ep" --out "$OUT/tmp" 2>&1 | tee -a "$OUT/${ep}_multi.log"
    rc=${PIPESTATUS[0]}
    if on_board; then
        note "$ep" after "$(temp)"
        .venv/bin/python -m bench.board stop >/dev/null
    fi
    runs="$OUT/tmp/runs_${BACKEND}_$ep.json"
    if [ "$rc" -ne 0 ] || [ ! -s "$runs" ]; then
        echo "$ep did not finish (exit $rc): stopping here. Run the same command again to resume." >&2
        exit 1
    fi
    gzip -c "$runs" > "$OUT/${ep}_multi.run.json.gz" && rm "$runs"
    mv "$OUT/tmp/summary_${BACKEND}_$ep.json" "$OUT/${ep}_multi.summary.json"
    echo "$ep: saved"
done
rmdir "$OUT/tmp" 2>/dev/null
echo "all back-to-back benchmark episodes are saved in $OUT"
