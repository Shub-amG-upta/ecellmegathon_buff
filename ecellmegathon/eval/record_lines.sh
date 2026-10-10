#!/usr/bin/env bash
# Record the speech eval lines from the terminal (eval/RECORDING.md, BUILD_PLAN §4.5).
# Same files as the UI's record mode: eval/audio/<case>_t<turn>_<speaker>.wav, 16 kHz mono 16-bit.
#
#   eval/record_lines.sh <speaker>            all lines, quiet room          (e.g. shubham)
#   eval/record_lines.sh <speaker> --noisy    the noisy lines, saved as <speaker>-noisy
#
# Keys: Enter starts, Enter stops; then Enter keeps it, r records it again, p plays it back, q quits.
# Lines already recorded are skipped, so you can stop and resume any time.
#
# Needs ffmpeg (macOS, built-in mic through AVFoundation; MIC=":1" picks another input, list them with
#   ffmpeg -f avfoundation -list_devices true -i "") or arecord (Linux). On Windows use the UI record mode.
# macOS asks once for microphone permission for the terminal app: allow it.
set -euo pipefail
cd "$(dirname "$0")/.."
speaker=${1:?usage: eval/record_lines.sh <speaker> [--noisy]}
[[ "$speaker" =~ ^[a-z0-9]+$ ]] || { echo "speaker: lowercase letters and digits only (e.g. shubham)"; exit 1; }
only_noisy=0
if [[ "${2:-}" == "--noisy" ]]; then only_noisy=1; speaker="$speaker-noisy"; fi
OUT=eval/audio
mkdir -p "$OUT"
PY=.venv/bin/python
[[ -x "$PY" ]] || PY=python3

if [[ "$(uname)" == Darwin ]]; then
    command -v ffmpeg >/dev/null || { echo "needs ffmpeg: brew install ffmpeg"; exit 1; }
else
    command -v arecord >/dev/null || command -v ffmpeg >/dev/null || { echo "needs arecord or ffmpeg"; exit 1; }
fi

REC_PID=""
start_rec() {
    if [[ "$(uname)" == Darwin ]]; then
        ffmpeg -nostdin -loglevel error -f avfoundation -i "${MIC:-:0}" -ac 1 -ar 16000 -sample_fmt s16 -y "$1" &
    elif command -v arecord >/dev/null; then
        arecord -q -f S16_LE -r 16000 -c 1 "$1" &
    else
        ffmpeg -nostdin -loglevel error -f pulse -i default -ac 1 -ar 16000 -sample_fmt s16 -y "$1" &
    fi
    REC_PID=$!
}
stop_rec() { kill -INT "$REC_PID" 2>/dev/null || true; wait "$REC_PID" 2>/dev/null || true; REC_PID=""; }
trap '[[ -n "$REC_PID" ]] && stop_rec' EXIT
play_it() { if command -v afplay >/dev/null; then afplay "$1"; else aplay -q "$1"; fi; }

# A clip must be 16 kHz mono 16-bit, at least 0.6 s, and not silent (a silent clip = no mic permission).
check() {
    "$PY" - "$1" <<'EOF'
import array, sys, wave
p = sys.argv[1]
try:
    w = wave.open(p)
except Exception as e:
    print(f"BAD: not a WAV file ({e})"); sys.exit(1)
fmt = (w.getnchannels(), w.getframerate(), w.getsampwidth())
if fmt != (1, 16000, 2):
    print(f"BAD: {fmt[0]} channel(s), {fmt[1]} Hz, {8 * fmt[2]}-bit; need 1 channel, 16000 Hz, 16-bit"); sys.exit(1)
n = w.getnframes()
a = array.array("h", w.readframes(n))
peak = max((abs(x) for x in a), default=0)
if n / 16000 < 0.6:
    print(f"BAD: only {n / 16000:.1f} s long"); sys.exit(1)
if peak < 300:
    print(f"BAD: silent (peak {peak}): check the mic and the terminal's microphone permission"); sys.exit(1)
print(f"ok: {n / 16000:.1f} s, peak {peak}")
EOF
}

# the lines: case, turn, noisy, the question it answers, the text
lines() {
    "$PY" - "$only_noisy" <<'EOF'
import json, sys
sys.path.insert(0, ".")
from app.form import load_schema
asks = {f["key"]: f["ask"] for f in load_schema()["fields"] if f.get("key")}
cases = {c["id"]: c for c in map(json.loads, open("eval/nlu/cases.jsonl"))}
for ln in open("eval/speech_subset.jsonl"):
    r = json.loads(ln)
    if sys.argv[1] == "1" and not r["noisy"]:
        continue
    asked = cases[r["case"]]["turns"][r["turn"] - 1]["asked"]
    q = asks.get(asked, "(start of the report, or the review)") if asked else "(start of the report, or the review)"
    print("\t".join([r["case"], str(r["turn"]), q, r["say"]]))
EOF
}

total=$(lines | wc -l | tr -d ' ')
k=0
while IFS=$'\t' read -r case turn question say <&3; do
    k=$((k + 1))
    f="$OUT/${case}_t${turn}_${speaker}.wav"
    if [[ -f "$f" ]] && check "$f" >/dev/null; then
        continue
    fi
    while true; do
        printf '\n\033[1m[%d/%d]  %s turn %s  →  %s\033[0m\n' "$k" "$total" "$case" "$turn" "$(basename "$f")"
        printf '  assistant asked:  %s\n' "$question"
        printf '\n  SAY:  \033[1;36m%s\033[0m\n\n' "$say"
        read -r -p "  Enter = start recording " _
        start_rec "$f"
        sleep 0.4
        read -r -p "  ● REC: speak now, then Enter to stop " _
        sleep 0.3
        stop_rec
        if ! msg=$(check "$f"); then
            echo "  $msg"; echo "  → recording it again"; continue
        fi
        echo "  $msg"
        read -r -p "  Enter = keep · r = record again · p = play · q = quit " key
        while [[ "$key" == "p" ]]; do play_it "$f"; read -r -p "  Enter = keep · r = record again · p = play · q = quit " key; done
        [[ "$key" == "q" ]] && { echo "stopped; run the same command to resume"; exit 0; }
        [[ "$key" == "r" ]] && continue
        break
    done
done 3< <(lines)

n=$(ls "$OUT"/*_"$speaker".wav 2>/dev/null | wc -l | tr -d ' ')
echo
echo "done: $n of $total files for '$speaker' in $OUT"
