#!/usr/bin/env zsh
set -euo pipefail

WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"
VENV="$WORKSPACE/venv/bin/python3"

commit_and_push() {
  msg=$1
  git -C "$WORKSPACE" add \
    padsplit_scraper/output/latest.json \
    docs/data/latest.json 2>/dev/null || true

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

echo "[$(date)] Starting afternoon run"

echo "[$(date)] Running PadSplit scraper (messages only)..."
cd "$WORKSPACE/padsplit_scraper"
"$VENV" scraper.py --messages-only

echo "[$(date)] Afternoon run complete"

commit_and_push "chore: afternoon data $(date -u +%Y-%m-%dT%H:%M:%SZ)"
