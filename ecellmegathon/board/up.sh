#!/usr/bin/env bash
# Bring up every model on the QIDK, prove the LLM is on the NPU, print how to
# start the app. BUILD_PLAN §9.
#
#   board/up.sh [model.gguf]       default: Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf
#   CTX=4096 board/up.sh           context size (default 2048, human decision 2026-10-10)
#   SPEC=1 board/up.sh             copy-ahead speculative decoding (off by default; compare on the board)
#   COOL_CPU=1 board/up.sh         2 CPU threads, no busy-polling (cooler CPU; off by default until measured)
#   TYPE_MODEL=gemma-3-4b-it-Q4_0-pure-embq8.gguf board/up.sh
#                                  also start the type model on the NPU (lane "type", port 8084; v7 router)
#
# Exit 1 if the board isn't there or placement isn't confirmed: nothing measured
# after a failed check counts as NPU.
set -euo pipefail
unset PYTHONPATH                     # a QAIRT SDK install exports one that shadows imports
cd "$(dirname "$0")/.."
PY=.venv/bin/python
MODEL=${1:-Llama-3.2-3B-Instruct-Q4_0-pure-embq8.gguf}

echo "[$(date +%FT%T%z)] laptop time (the board clock is wrong; log this one)"
adb get-state >/dev/null 2>&1 || { echo "STOP: no board on adb"; exit 1; }

echo "== chip temperature before"
$PY -m board.board temp | grep -E '"(t|cpu_max_c|npu_max_c)"'

echo "== LLM on the NPU: $MODEL"
$PY -m board.board stop >/dev/null || true
$PY -m board.board start npu "$MODEL" ${CTX:+--ctx "$CTX"} ${SPEC:+--spec} | grep -E '"(url|model_sha256|ready_after_s|ctx|spec|cool_cpu)"'
$PY -m board.board check npu || { echo "STOP: LLM placement not confirmed"; exit 1; }
$PY -m board.board log npu | grep -E 'offloaded [0-9]+/[0-9]+ layers|HTP0 .*buffer size' || true

if [[ -n "${TYPE_MODEL:-}" ]]; then
    echo "== type model on the NPU: $TYPE_MODEL"
    $PY -m board.board start type "$TYPE_MODEL" ${CTX:+--ctx "$CTX"} | grep -E '"(url|model_sha256|ready_after_s|ctx)"'
    $PY -m board.board check type || { echo "STOP: type model placement not confirmed"; exit 1; }
fi

if [[ -x board/voice_up.sh ]]; then
    echo "== STT + TTS (Track B)"
    board/voice_up.sh
else
    echo "== STT + TTS: board/voice_up.sh not there yet (Track B); the app runs text-only"
fi

THINK=""
[[ "$MODEL" == Qwen3* ]] && THINK=" (Qwen3: thinking is turned off automatically)"
cat <<EOF

Ready. Start the app$THINK:
  ${TYPE_MODEL:+TYPE_MODEL=$TYPE_MODEL }LLM=llamaserver LLM_LANE=npu LLM_MODEL=$MODEL $PY -m app.server
  open http://localhost:8000
Eval (run twice):
  LLM=llamaserver LLM_LANE=npu LLM_MODEL=$MODEL $PY eval/run_eval.py --label BOARD
EOF
