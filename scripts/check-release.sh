#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python}"
if [[ "$PYTHON_BIN" == "python" && -x venv/bin/python ]]; then
  PYTHON_BIN="venv/bin/python"
fi

npm run build
PYTHONPATH=. "$PYTHON_BIN" -m unittest discover -s tests

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from backend.config import settings

settings.ENVIRONMENT = "production"
settings.AUTH_MODE = "supabase"
settings.DATABASE_URL = "postgresql://user:pass@db.example.com/postgres"
settings.APP_BASE_URL = "https://getwealthbrief.com"
settings.CORS_ORIGINS = ["https://getwealthbrief.com", "https://www.getwealthbrief.com"]
settings.SUPABASE_URL = "https://project.supabase.co"
settings.SUPABASE_PUBLISHABLE_KEY = "publishable-key"
settings.validate_for_startup()
print("release checks passed")
PY
