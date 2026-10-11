#!/usr/bin/env bash
# Open Kage in the browser. A folder selector appears; no need to type a path.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT/kage.sh" web --open "$@"
