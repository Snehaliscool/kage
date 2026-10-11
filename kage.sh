#!/usr/bin/env bash
# Run Kage straight from this folder (terminal agent). No install needed.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" KAGE_SEARXNG_URL="${KAGE_SEARXNG_URL:-${LOCALPILOT_SEARXNG_URL:-http://127.0.0.1:8888}}"
exec python3 -m kage "$@"
