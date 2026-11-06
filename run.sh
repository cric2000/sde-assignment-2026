#!/usr/bin/env bash
# Local Python process; no Docker or dependency installation during a run.
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -n "${CONTROLLER_PYTHON:-}" ]]; then
  PYTHON_BIN="$CONTROLLER_PYTHON"
elif [[ -x .venv/bin/python ]]; then
  PYTHON_BIN=.venv/bin/python
else
  PYTHON_BIN=python3
fi
exec "$PYTHON_BIN" -m controller --url "$SIM_URL"
