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

docker compose -f docker/docker-compose.yml build trader
docker compose -f docker/docker-compose.yml run --rm --no-deps trader \
  python scripts/validate_live_policy.py
docker compose -f docker/docker-compose.yml up -d

healthy=0
for _attempt in $(seq 1 30); do
  if curl -fsS --max-time 10 "$HEALTH_URL" >/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done
if [[ "$healthy" != "1" ]]; then
  echo "dashboard healthcheck failed after 30 attempts" >&2
  exit 1
fi

echo "deploy ok: $(date -Is)"
