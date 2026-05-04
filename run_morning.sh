#!/usr/bin/env zsh
set -euo pipefail

WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"
VENV="$WORKSPACE/venv/bin/python3"

commit_and_push() {
  msg=$1
  git -C "$WORKSPACE" add \
    padsplit_scraper/output/latest.json \
    padsplit_scraper/output/stats.json \
    thermostat/output/latest.json \
    docs/data/latest.json \
    docs/data/stats.json \
    docs/thermostat/latest.json 2>/dev/null || true

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

echo "[$(date)] Starting morning run"

# --- FIX: Pull remote changes BEFORE scraping ---
echo "[$(date)] Syncing with GitHub..."
set +e
git -C "$WORKSPACE" pull --rebase
set -e
# ------------------------------------------------

echo "[$(date)] Running thermostat scraper..."
cd "$WORKSPACE/thermostat"
"$VENV" scraper.py

echo "[$(date)] Running PadSplit scraper (messages + tasks)..."
cd "$WORKSPACE/padsplit_scraper"
"$VENV" scraper.py

echo "[$(date)] Writing Obsidian daily digest..."
cd "$WORKSPACE"
"$VENV" obsidian_daily_digest.py

echo "[$(date)] Morning run complete"4

commit_and_push "chore: morning data $(date -u +%Y-%m-%dT%H:%M:%SZ)"
