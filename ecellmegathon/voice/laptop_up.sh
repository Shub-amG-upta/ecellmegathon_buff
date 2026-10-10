#!/usr/bin/env bash
# Start STT (:8082) and TTS (:8083) on THIS laptop, for development (milestone M2).
# Same HTTP API as the board, so app/voice.py only needs the default URLs.
# Every timing from these servers is a LAPTOP number.
#
#   voice/laptop_up.sh            start both, wait until healthy, print the env to export
#   voice/laptop_up.sh stop       stop both
#
# ENGINES (default: ../../engines next to the repo) holds what was downloaded:
#   whisper/Release/whisper-server(.exe), whisper/ggml-<model>.bin
#   sherpa/sherpa-onnx-*/bin/sherpa-onnx-offline-tts(.exe)
#   voices/vits-piper-en_US-lessac-medium/
# STT_MODEL_FILE (default ggml-base.en.bin) picks the Whisper model.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
ENGINES=${ENGINES:-$(cd "$REPO/../.." && pwd)/engines}
MODEL=${STT_MODEL_FILE:-ggml-base.en.bin}
VOICE=${TTS_VOICE:-vits-piper-en_US-lessac-medium}
LOGS="$REPO/logs"
mkdir -p "$LOGS"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$REPO/.venv/Scripts/python.exe"          # Windows venv layout

exe() { if [ -e "$1.exe" ]; then echo "$1.exe"; else echo "$1"; fi; }

if [ "${1:-}" = "stop" ]; then
    for f in "$LOGS"/stt_laptop.pid "$LOGS"/tts_laptop.pid; do
        [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true
        rm -f "$f"
    done
    echo "stopped"
    exit 0
fi

WHISPER=$(exe "$ENGINES/whisper/Release/whisper-server")
[ -e "$WHISPER" ] || WHISPER=$(exe "$ENGINES/whisper/whisper-server")
TTS_BIN=$(exe "$(ls -d "$ENGINES"/sherpa/sherpa-onnx-*/bin | head -1)/sherpa-onnx-offline-tts")
for p in "$WHISPER" "$ENGINES/whisper/$MODEL" "$TTS_BIN" "$ENGINES/voices/$VOICE"; do
    [ -e "$p" ] || { echo "missing: $p" >&2; exit 1; }
done

"$WHISPER" -m "$ENGINES/whisper/$MODEL" --host 127.0.0.1 --port 8082 -t 6 \
    > "$LOGS/stt_laptop.log" 2>&1 &
echo $! > "$LOGS/stt_laptop.pid"
"$PY" -m voice.tts_server --mode local --bin "$TTS_BIN" --model-dir "$ENGINES/voices/$VOICE" \
    --threads 4 --warm schema/incident_form.json > "$LOGS/tts_laptop.log" 2>&1 &
echo $! > "$LOGS/tts_laptop.pid"

for port in 8082 8083; do
    for _ in $(seq 1 90); do
        curl -sf "http://127.0.0.1:$port/health" > /dev/null && break
        sleep 1
    done
    curl -sf "http://127.0.0.1:$port/health" > /dev/null \
        && echo "ok   :$port" || { echo "FAIL :$port (see $LOGS)" >&2; exit 1; }
done
echo "export STT_URL=http://localhost:8082 STT_DEVICE=laptop STT_MODEL=$MODEL"
echo "export TTS_URL=http://localhost:8083"
