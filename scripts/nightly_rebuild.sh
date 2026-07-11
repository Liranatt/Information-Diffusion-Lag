#!/usr/bin/env bash
# Nightly (≈00:00) artifact rebuild on the server: regenerate the backtest
# artifacts from the DB and push them to the deploy branch so both the committed
# reference data and the dashboard stay current, and so a fresh clone/backtest
# is always reproducible.
#
# Shares ONE flock with scripts/deploy_if_changed.sh (same LOCK_FILE) so the
# nightly rebuild and a concurrent deploy never interleave. Rebases onto the
# remote first, so the owner's monthly policy-CSV pushes (a disjoint file set)
# and the server's parquet/pkl pushes never clobber each other.
#
# Suggested crontab on liranserver (00:07 daily, after the US close settles):
#   7 0 * * *  /home/liranatt/cem_clean_repo/scripts/nightly_rebuild.sh >> /var/log/cem_nightly.log 2>&1
set -euo pipefail

APP_DIR="${APP_DIR:-/home/liranatt/cem_clean_repo}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
LOCK_FILE="${LOCK_FILE:-/tmp/cem_clean_repo_deploy.lock}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

exec 9>"$LOCK_FILE"
flock -n 9 || { echo "deploy/rebuild lock held; skipping nightly rebuild"; exit 0; }

cd "$APP_DIR"

git fetch origin "refs/heads/$DEPLOY_BRANCH:refs/remotes/origin/$DEPLOY_BRANCH"
git rebase "origin/$DEPLOY_BRANCH" || { git rebase --abort; echo "rebase failed" >&2; exit 1; }

"$PYTHON_BIN" -m ingest --rebuild

if git diff --quiet -- data/candidates.parquet data/prices.pkl data/probs.pkl; then
  echo "nightly rebuild: no artifact changes; nothing to commit"
  exit 0
fi

git add data/candidates.parquet data/prices.pkl data/probs.pkl
git commit -m "nightly: rebuild candidate + price/prob artifacts from DB"
git push origin "HEAD:$DEPLOY_BRANCH"
echo "nightly rebuild pushed: $(date -Is)"
