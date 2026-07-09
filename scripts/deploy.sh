#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/liranatt/cem_clean_repo}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/healthz}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$APP_DIR"

"$PYTHON_BIN" -m py_compile \
  live/config.py \
  live/control_pipeline.py \
  live/dashboard.py \
  live/database.py \
  live/order_manager.py \
  live/run_live.py \
  live/strategy_engine.py \
  live/utils.py

docker compose up -d --build
curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null

echo "deploy ok: $(date -Is)"
