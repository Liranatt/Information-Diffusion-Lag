#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/liranatt/cem_clean_repo}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
LOCK_FILE="${LOCK_FILE:-/tmp/cem_clean_repo_deploy.lock}"

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

cd "$APP_DIR"

git fetch origin "refs/heads/$DEPLOY_BRANCH:refs/remotes/origin/$DEPLOY_BRANCH"
LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "origin/$DEPLOY_BRANCH")"

if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
  exit 0
fi

echo "deploying $REMOTE_SHA from origin/$DEPLOY_BRANCH"
# Never discard the server's own (nightly-rebuild) commits. If HEAD is an
# ancestor of the remote there are no local-only commits and a hard reset is
# safe; otherwise replay the local commits on top of the remote via rebase.
if git merge-base --is-ancestor HEAD "origin/$DEPLOY_BRANCH"; then
  git reset --hard "origin/$DEPLOY_BRANCH"
else
  git rebase "origin/$DEPLOY_BRANCH" || { git rebase --abort; echo "rebase failed" >&2; exit 1; }
fi
bash scripts/deploy.sh
