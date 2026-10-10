#!/usr/bin/env bash
# Unattended board campaign. Launch ONCE, in tmux, from the repo root:
#
#     tmux new -s campaign 'scripts/board_campaign.sh all'
#
# It runs stages A-E in order with no prompts (bench/campaign.py), keeps the
# laptop awake with systemd-inhibit, and can be relaunched with the same command
# after any stop: completed episodes are skipped, incomplete ones are redone.
#
#     scripts/board_campaign.sh status     # prints results/board/STATUS.md
#
# Exit codes: 0 finished; 2 stopped on an infrastructure failure after 3
# retries (fix the board, relaunch); 3 stopped by a committed rule (read
# STATUS.md; needs a human decision). Results are committed locally after each
# stage and never pushed.
set -uo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH                      # this laptop exports the QAIRT python folder
mkdir -p logs
cmd=${1:-all}

if [ "$cmd" = status ]; then
    exec .venv/bin/python -m bench.campaign status
fi
if [ "$cmd" != all ]; then
    echo "usage: scripts/board_campaign.sh all|status" >&2
    exit 64
fi

n=$(adb devices | awk 'NR > 1 && $2 == "device"' | wc -l)
if [ "$n" -ne 1 ]; then
    echo "need exactly one adb device, found $n" >&2
    exit 2
fi

log="logs/board_campaign_$(date +%Y%m%dT%H%M%S).log"
echo "campaign log: $log   status: results/board/STATUS.md"
systemd-inhibit --what=sleep:idle:handle-lid-switch --who=fieldmind \
    --why="FieldMind board campaign" \
    .venv/bin/python -m bench.campaign all 2>&1 | tee -a "$log"
rc=${PIPESTATUS[0]}
echo "campaign exit code $rc (0 finished, 2 infrastructure stop, 3 rule stop)" | tee -a "$log"
exit "$rc"
