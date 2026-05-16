#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"
if [[ "$PYTHON_BIN" == "python" && -x venv/bin/python ]]; then
  PYTHON_BIN="venv/bin/python"
fi

if [[ -f .env.staging ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.staging
  set +a
fi

export PORT="${PORT:-8000}"
export HOST="${HOST:-127.0.0.1}"
export ENVIRONMENT="staging"
export AUTH_MODE="supabase"
export APP_BASE_URL="${APP_BASE_URL:-http://127.0.0.1:${PORT}}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://127.0.0.1:${PORT},http://localhost:${PORT}}"
export ENABLE_BROKER_CONNECTORS="${ENABLE_BROKER_CONNECTORS:-false}"
export ALLOW_LEGACY_DASHBOARD="false"

: "${SUPABASE_URL:?Set SUPABASE_URL in .env.staging or the shell}"
: "${SUPABASE_PUBLISHABLE_KEY:?Set SUPABASE_PUBLISHABLE_KEY in .env.staging or the shell}"
: "${DATABASE_URL:?Set DATABASE_URL in .env.staging or the shell}"

case "$DATABASE_URL" in
  postgres://*|postgresql://*) ;;
  *)
    echo "DATABASE_URL must be a Postgres connection string for staging." >&2
    exit 1
    ;;
esac

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  npm run build
fi

"$PYTHON_BIN" -m backend.main
