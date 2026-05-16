#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"
if [[ "$PYTHON_BIN" == "python" && -x venv/bin/python ]]; then
  PYTHON_BIN="venv/bin/python"
fi

export PORT="${PORT:-8000}"
export HOST="${HOST:-127.0.0.1}"
export ENVIRONMENT="${ENVIRONMENT:-local}"
export AUTH_MODE="${AUTH_MODE:-local}"
export DEFAULT_USER_ID="${DEFAULT_USER_ID:-local-user}"
export APP_BASE_URL="${APP_BASE_URL:-http://127.0.0.1:${PORT}}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://127.0.0.1:${PORT},http://localhost:${PORT},http://127.0.0.1:8787,http://localhost:8787}"
export ENABLE_BROKER_CONNECTORS="${ENABLE_BROKER_CONNECTORS:-false}"
export ALLOW_LEGACY_DASHBOARD="${ALLOW_LEGACY_DASHBOARD:-false}"

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  npm run build
fi

"$PYTHON_BIN" -m backend.main
