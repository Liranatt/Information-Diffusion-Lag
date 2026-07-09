#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/liranatt/cem_clean_repo}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/healthz}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$APP_DIR"

"$PYTHON_BIN" -m py_compile \
  interactive_brokers/config.py \
  interactive_brokers/control_pipeline.py \
  interactive_brokers/dashboard.py \
  interactive_brokers/database.py \
  interactive_brokers/order_manager.py \
  interactive_brokers/run_live.py \
  interactive_brokers/strategy_engine.py \
  interactive_brokers/utils.py

docker compose up -d --build
curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null

echo "deploy ok: $(date -Is)"
