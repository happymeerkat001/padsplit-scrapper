#!/usr/bin/env zsh
set -euo pipefail

WORKSPACE="$(cd "$(dirname "$0")" && pwd)"
VENV="$WORKSPACE/venv/bin/python3"
LOCK_DIR="/private/tmp/padsplit-scraper-afternoon.lock"

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    return 0
  fi
  echo "[$(date)] Afternoon run already in progress; skipping"
  return 1
}

release_lock() {
  rm -f "$LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

commit_and_push() {
  msg=$1
  git -C "$WORKSPACE" add \
    padsplit_scraper/output/latest.json \
    padsplit_scraper/output/stats.json \
    docs/data/latest.json \
    docs/data/monthly_history.json \
    docs/data/stats.json 2>/dev/null || true

  if git -C "$WORKSPACE" diff --cached --quiet; then
    echo "[$(date)] Nothing to commit"
    return
  fi

  git -C "$WORKSPACE" commit -m "$msg" || return
  set +e
  git -C "$WORKSPACE" pull --rebase
  git -C "$WORKSPACE" push
  set -e
}

acquire_lock || exit 0
trap release_lock EXIT

echo "[$(date)] Starting afternoon run"

# --- FIX: Pull remote changes BEFORE scraping ---
echo "[$(date)] Syncing with GitHub..."
set +e
git -C "$WORKSPACE" pull --rebase
set -e
# ------------------------------------------------

echo "[$(date)] Running PadSplit scraper (messages only)..."
if ! "$VENV" "$WORKSPACE/padsplit_scraper/scraper.py" --messages-only; then
  echo "[$(date)] PadSplit scraper failed (exit code: $?)"
fi

echo "[$(date)] Afternoon run complete"

commit_and_push "chore: afternoon data $(date -u +%Y-%m-%dT%H:%M:%SZ)"
