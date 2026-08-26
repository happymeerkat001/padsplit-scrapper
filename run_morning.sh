#!/usr/bin/env zsh
set -euo pipefail

WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"
VENV="$WORKSPACE/venv/bin/python3"
LOCK_DIR="/private/tmp/padsplit-scraper-morning.lock"

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    return 0
  fi
  echo "[$(date)] Morning run already in progress; skipping"
  return 1
}

release_lock() {
  rm -f "$LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

run_phase() {
  label=$1
  shift
  echo "[$(date)] Running $label..."
  if "$@"; then
    echo "[$(date)] $label completed"
  else
    status=$?
    echo "[$(date)] $label failed with exit code $status; continuing so rolling outputs can be committed" >&2
  fi
}

commit_and_push() {
  msg=$1
  git -C "$WORKSPACE" add \
    padsplit_scraper/output/latest.json \
    padsplit_scraper/output/drafts.json \
    padsplit_scraper/output/drafted_messages.json \
    padsplit_scraper/output/stats.json \
    padsplit_scraper/output/occupancy.json \
    thermostat/output/latest.json \
    docs/data/latest.json \
    docs/data/stats.json \
    docs/data/occupancy.json \
    docs/data/monthly_history.json \
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

acquire_lock || exit 0
trap release_lock EXIT

echo "[$(date)] Starting morning run"

# --- FIX: Pull remote changes BEFORE scraping ---
echo "[$(date)] Syncing with GitHub..."
set +e
git -C "$WORKSPACE" pull --rebase
set -e
# ------------------------------------------------

run_phase "thermostat scraper" "$VENV" "$WORKSPACE/thermostat/scraper.py"
run_phase "PadSplit scraper (messages + tasks)" "$VENV" "$WORKSPACE/padsplit_scraper/scraper.py"
run_phase "PadSplit draft replies" "$VENV" "$WORKSPACE/message_drafter.py"
run_phase "Obsidian daily digest" "$VENV" "$WORKSPACE/obsidian_daily_digest.py"

echo "[$(date)] Morning run complete"

commit_and_push "chore: morning data $(date -u +%Y-%m-%dT%H:%M:%SZ)"
