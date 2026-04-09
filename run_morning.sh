#!/usr/bin/env zsh
set -euo pipefail

WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"
VENV="$WORKSPACE/venv/bin/python3"

commit_and_push() {
  msg=$1
  git -C "$WORKSPACE" add \
    padsplit_scraper/output/latest.json \
    thermostat/output/latest.json \
    docs/data/latest.json \
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

echo "[$(date)] Running thermostat scraper..."
cd "$WORKSPACE/thermostat"
"$VENV" scraper.py

echo "[$(date)] Running PadSplit scraper (messages + tasks)..."
cd "$WORKSPACE/padsplit_scraper"
"$VENV" scraper.py

echo "[$(date)] Morning run complete"

commit_and_push "chore: morning data $(date -u +%Y-%m-%dT%H:%M:%SZ)"
