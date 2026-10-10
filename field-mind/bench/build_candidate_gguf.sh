#!/usr/bin/env bash
# Build one candidate GGUF with the Phase 0b quantization recipe (HUMAN DECISION
# 2026-10-03, reports/phase0b_board_setup.md): every weight matrix Q4_0, token
# embedding and output at Q8_0, from the original 16-bit weights.
#
#   bench/build_candidate_gguf.sh <name> <hf_source_dir> [extra llama-quantize args...]
#
#   <name>            output is $BUILD/gguf/<name>-Q4_0-pure-embq8.gguf
#   <hf_source_dir>   folder with the safetensors + tokenizer files
#   extra args        passed to llama-quantize before the positional args
#                     (e.g. --override-kv tokenizer.ggml.eos_token_id=int:1)
#
# BUILD (default ~/fieldmind-build) must hold the pinned llama.cpp checkout with
# build-host/bin/llama-quantize, and venv-convert. All tool output goes to
# logs/; the script prints only the verdict, size and sha256.
# Exit 3 = a matrix tensor is outside Q4_0/Q8_0 (stop rule).
set -euo pipefail
unset PYTHONPATH   # this laptop exports the QAIRT python folder; keep it out

name=$1; src=$2; shift 2
REPO=$(cd "$(dirname "$0")/.." && pwd)
BUILD=${BUILD:-$HOME/fieldmind-build}
LLAMA=$BUILD/llama.cpp
mkdir -p "$BUILD/gguf" "$REPO/logs"
mid=$BUILD/gguf/$name-16bit.gguf
out=$BUILD/gguf/$name-Q4_0-pure-embq8.gguf

echo "[$(date +%FT%T%z)] $name: llama.cpp $(git -C "$LLAMA" rev-parse HEAD)"

# 1. safetensors -> GGUF at the source precision (auto: BF16 stays BF16, F16 stays F16)
"$BUILD/venv-convert/bin/python" "$LLAMA/convert_hf_to_gguf.py" "$src" \
    --outtype auto --outfile "$mid" > "$REPO/logs/convert_$name.log" 2>&1

# 2. the recipe
"$LLAMA/build-host/bin/llama-quantize" --pure \
    --token-embedding-type q8_0 --output-tensor-type q8_0 "$@" \
    "$mid" "$out" Q4_0 > "$REPO/logs/quantize_$name.log" 2>&1

# 3. every matrix must be Q4_0 or Q8_0 (reads the whole header of the real file)
"$REPO/.venv/bin/python" -m bench.gguf_types "$out" > "$REPO/logs/gguf_types_$name.log"
grep -E '"(type_counts|F32|F16|BF16|Q4_0|Q4_1|Q8_0|Q6_K|output.weight|token_embd.weight|verdict)"' \
    "$REPO/logs/gguf_types_$name.log"
echo "16-bit source GGUF: $(stat -c %s "$mid") B  $(sha256sum "$mid" | cut -d' ' -f1)"
echo "candidate GGUF:     $(stat -c %s "$out") B  $(sha256sum "$out" | cut -d' ' -f1)  $out"
grep -q '"verdict": "OK' "$REPO/logs/gguf_types_$name.log" || { echo "STOP: non-Q4_0/Q8_0 matrix"; exit 3; }
