#!/usr/bin/env bash
# Local dev helper: create venv + install + launch uvicorn.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt >/dev/null

export CCS_STORAGE_PATH="${CCS_STORAGE_PATH:-./app/var/storage.json}"
export CCS_PORT="${CCS_PORT:-8000}"

echo "==> Starting at http://localhost:${CCS_PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${CCS_PORT}" --reload
