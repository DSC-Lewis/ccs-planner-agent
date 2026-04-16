#!/usr/bin/env bash
# Rolling update on the VM: git pull + docker compose up --build.
# SSH into the VM first, then run this script. Idempotent.
set -euo pipefail

APP_DIR="${CCS_APP_DIR:-/opt/ccs-planner-agent}"
BRANCH="${CCS_BRANCH:-main}"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

cd "$APP_DIR"

say "Fetching $BRANCH"
git fetch origin "$BRANCH" --quiet
git checkout "$BRANCH" --quiet
git reset --hard "origin/$BRANCH" --quiet

say "Rebuilding + restarting"
sudo docker compose up --build -d

say "Waiting for /api/health"
for _ in $(seq 1 30); do
    if curl -sf "http://localhost:${CCS_PORT:-8000}/api/health" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

curl -sf "http://localhost:${CCS_PORT:-8000}/api/health" | python3 -m json.tool

say "Done. git HEAD:"
git -C "$APP_DIR" log --oneline -1
