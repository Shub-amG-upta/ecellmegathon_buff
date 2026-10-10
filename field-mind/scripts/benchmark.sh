#!/usr/bin/env bash
# Run a named benchmark test with the live stage monitor in THIS terminal;
# results in results/benchmarks/<test name>/. Everything is in bench/benchmark.py:
#
#     scripts/benchmark.sh <test name> <episode> [<episode> ...] [options]
#     scripts/benchmark.sh --help
set -uo pipefail
cd "$(dirname "$0")/.."
unset PYTHONPATH
exec .venv/bin/python bench/benchmark.py "$@"
