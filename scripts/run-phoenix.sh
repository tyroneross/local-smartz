#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -x ".venv/bin/python" ]; then
  echo "error: .venv/bin/python not found. Run: uv sync --dev" >&2
  exit 1
fi

export PHOENIX_WORKING_DIR="${PHOENIX_WORKING_DIR:-$ROOT/.localsmartz/phoenix}"
mkdir -p "$PHOENIX_WORKING_DIR"

exec .venv/bin/python -m phoenix.server.main serve
