#!/usr/bin/env bash
# STT (:8082) and TTS (:8083) on the QIDK, reached from the laptop (Track B, BUILD_PLAN §5 and §9 step 4).
# Called by board/up.sh after the LLM is up; also runs on its own.
#
#   board/voice_up.sh            push (if needed), start, forward, health-check, print the env to export
#   board/voice_up.sh stop       stop both
#
# STATUS: UNTESTED ON THE BOARD (written before we had it). Expect to fix paths on first run.
#
# What runs where:
#   STT  whisper.cpp `whisper-server` on the board's CPU, port 8082 on the board, adb-forwarded.
#   TTS  sherpa-onnx `sherpa-onnx-offline-tts` + a Piper voice on the board's CPU. The HTTP
#        wrapper (voice/tts_server.py --mode adb) runs on the laptop, because the board has no
#        Python; every synthesis runs on the board through `adb shell`.
#   Neither touches the NPU: the LLM keeps HTP0 to itself.
#
# Inputs, prepared on a laptop beforehand (ENGINES defaults to ../../engines next to the repo):
#   $ENGINES/android/stt/bin/whisper-server       arm64-v8a build (Android NDK; see below)
#   $ENGINES/android/stt/lib/*.so                 its shared libraries, if any
#   $ENGINES/whisper/$STT_MODEL_FILE              the same ggml model as on the laptop
#   $ENGINES/android/tts/bin/sherpa-onnx-offline-tts
#   $ENGINES/android/tts/lib/*.so
#   $ENGINES/voices/$TTS_VOICE/                   the same Piper voice as on the laptop
# Building whisper-server for the board (Fedora laptop, Android NDK r26+):
#   cmake -B build-android -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
#         -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-28 -DWHISPER_BUILD_SERVER=ON \
#         -DWHISPER_BUILD_TESTS=OFF -DGGML_OPENMP=OFF && cmake --build build-android -j --target whisper-server
set -euo pipefail
unset PYTHONPATH   # a QAIRT install exports one that breaks tools (board/board.py has the same rule)
# Git Bash on Windows rewrites "/data/local/tmp/..." arguments into Windows paths before they reach
# adb.exe (so `adb push x /data/...` lands in the wrong place). No effect on macOS or Linux.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
cd "$(dirname "$0")/.."
REPO=$(pwd)
ENGINES=${ENGINES:-$(cd "$REPO/../.." && pwd)/engines}
MODEL=${STT_MODEL_FILE:-ggml-base.en.bin}
VOICE=${TTS_VOICE:-vits-piper-en_US-lessac-medium}
STT_THREADS=${STT_THREADS:-4}
SERIAL=${ANDROID_SERIAL:-}
STT_DIR=/data/local/tmp/stt
TTS_DIR=/data/local/tmp/tts
LOGS="$REPO/logs"
mkdir -p "$LOGS"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$REPO/.venv/Scripts/python.exe"

adb_() { if [ -n "$SERIAL" ]; then adb -s "$SERIAL" "$@"; else adb "$@"; fi; }
stamp() { date +%FT%T%z; }   # laptop time; the board's clock is wrong

if [ "${1:-}" = "stop" ]; then
    adb_ shell "pkill whisper-server" || true
    [ -f "$LOGS/tts_board.pid" ] && kill "$(cat "$LOGS/tts_board.pid")" 2>/dev/null || true
    rm -f "$LOGS/tts_board.pid"
    echo "[$(stamp)] stopped"
    exit 0
fi

adb_ get-state > /dev/null || { echo "no board: adb get-state failed" >&2; exit 1; }
echo "[$(stamp)] board $(adb_ shell getprop ro.soc.model | tr -d '\r')"

# Push a file only when the board copy differs; then compare sha256 (CLAUDE.md: after any adb push).
push() {
    local src=$1 dst=$2 want got
    [ -e "$src" ] || { echo "missing on laptop: $src" >&2; exit 1; }
    want=$(sha256sum "$src" | cut -d' ' -f1)
    # Not on the board yet: sha256sum fails, adb passes that exit code on, and set -e would end the script.
    got=$(adb_ shell "sha256sum $dst 2>/dev/null" | cut -d' ' -f1 | tr -d '\r') || got=""
    if [ "$want" != "$got" ]; then
        # adb.exe needs a Windows path for the laptop side (MSYS_NO_PATHCONV above stops Git Bash doing it).
        if command -v cygpath > /dev/null; then adb_ push "$(cygpath -m "$src")" "$dst" > /dev/null
        else adb_ push "$src" "$dst" > /dev/null; fi
        got=$(adb_ shell "sha256sum $dst" | cut -d' ' -f1 | tr -d '\r')
        [ "$want" = "$got" ] || { echo "sha256 mismatch after push: $dst" >&2; exit 1; }
        echo "pushed $dst"
    fi
}
dir_sums() {   # "<sha256>  <relative path>" for every file under a laptop dir ($1) or board dir ($2), sorted
    if [ -n "${1:-}" ]; then (cd "$1" && find . -type f -exec sha256sum {} + | sed 's/ \*/  /')
    else adb_ shell "cd '$2' 2>/dev/null && find . -type f -exec sha256sum {} +" | tr -d '\r' || true
    fi | sort -k2
}
push_dir() {   # every regular file under $1 to $2, same relative paths; one push and one checksum pass
    # (per-file pushes took ~25 min for the voice folder's 359 files)
    local src=$1 dst=$2
    [ -d "$src" ] || { echo "missing on laptop: $src" >&2; exit 1; }
    [ "$(dir_sums "$src")" = "$(dir_sums "" "$dst")" ] && return 0
    adb_ shell "mkdir -p '$dst'" > /dev/null
    if command -v cygpath > /dev/null; then adb_ push "$(cygpath -m "$src")/." "$dst/" > /dev/null
    else adb_ push "$src/." "$dst/" > /dev/null; fi
    diff <(dir_sums "$src") <(dir_sums "" "$dst") > /dev/null \
        || { echo "sha256 mismatch after push: $dst (extra or changed files on the board?)" >&2; exit 1; }
    echo "pushed $dst ($(find "$src" -type f | wc -l) files)"
}

# ---- STT: whisper-server on the board -------------------------------------
adb_ shell "mkdir -p $STT_DIR/bin $STT_DIR/lib"
push "$ENGINES/android/stt/bin/whisper-server" "$STT_DIR/bin/whisper-server"
[ -d "$ENGINES/android/stt/lib" ] && push_dir "$ENGINES/android/stt/lib" "$STT_DIR/lib"
push "$ENGINES/whisper/$MODEL" "$STT_DIR/$MODEL"
adb_ shell "chmod 755 $STT_DIR/bin/whisper-server"
adb_ shell "pkill whisper-server" || true
adb_ shell -n "cd $STT_DIR; nohup sh -c 'LD_LIBRARY_PATH=$STT_DIR/lib ./bin/whisper-server -m $STT_DIR/$MODEL \
    --host 127.0.0.1 --port 8082 -t $STT_THREADS' > $STT_DIR/stt.log 2>&1 < /dev/null &"
adb_ forward tcp:8082 tcp:8082 > /dev/null

# ---- TTS: engine on the board, thin wrapper on the laptop ------------------
adb_ shell "mkdir -p $TTS_DIR/bin $TTS_DIR/lib"
push "$ENGINES/android/tts/bin/sherpa-onnx-offline-tts" "$TTS_DIR/bin/sherpa-onnx-offline-tts"
push_dir "$ENGINES/android/tts/lib" "$TTS_DIR/lib"
push_dir "$ENGINES/voices/$VOICE" "$TTS_DIR/$VOICE"
adb_ shell "chmod 755 $TTS_DIR/bin/sherpa-onnx-offline-tts"
[ -f "$LOGS/tts_board.pid" ] && kill "$(cat "$LOGS/tts_board.pid")" 2>/dev/null || true
"$PY" -m voice.tts_server --mode adb --device-dir "$TTS_DIR" --voice "$VOICE" --threads 2 \
    ${SERIAL:+--serial "$SERIAL"} --warm schema/incident_form.json > "$LOGS/tts_board.log" 2>&1 &
echo $! > "$LOGS/tts_board.pid"

# ---- health ----------------------------------------------------------------
for port in 8082 8083; do
    for _ in $(seq 1 120); do
        curl -sf "http://127.0.0.1:$port/health" > /dev/null && break
        sleep 1
    done
    if curl -sf "http://127.0.0.1:$port/health" > /dev/null; then
        echo "[$(stamp)] ok   :$port"
    else
        echo "[$(stamp)] FAIL :$port  (STT log: adb shell cat $STT_DIR/stt.log; TTS log: $LOGS/tts_board.log)" >&2
        exit 1
    fi
done
echo "export STT_URL=http://localhost:8082 STT_DEVICE=cpu STT_MODEL=$MODEL"
echo "export TTS_URL=http://localhost:8083"
