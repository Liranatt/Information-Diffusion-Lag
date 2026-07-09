#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/liranatt/cem_clean_repo}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-cem/phase0-phase1-plumbing}"
LOCK_FILE="${LOCK_FILE:-/tmp/cem_clean_repo_deploy.lock}"

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

cd "$APP_DIR"

git fetch origin "$DEPLOY_BRANCH"
LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "origin/$DEPLOY_BRANCH")"

if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
  exit 0
fi

echo "deploying $REMOTE_SHA from origin/$DEPLOY_BRANCH"
git reset --hard "$REMOTE_SHA"
bash scripts/deploy.sh
