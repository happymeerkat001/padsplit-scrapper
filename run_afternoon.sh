#!/usr/bin/env zsh
set -euo pipefail

WORKSPACE="${0:A:h}"
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
  # Project private scraper data before staging any public snapshot.
  if [ -f "$WORKSPACE/padsplit_scraper/output/latest.json" ]; then
    (cd "$WORKSPACE" && "$VENV" -m padsplit_scraper.public_snapshot \
      padsplit_scraper/output/latest.json docs/data/latest.json) || return 1
  fi
  if [ -f "$WORKSPACE/docs/data/occupancy.json" ]; then
    (cd "$WORKSPACE" && "$VENV" -m padsplit_scraper.public_snapshot --occupancy \
      docs/data/occupancy.json docs/data/occupancy.json) || return 1
  fi
  local publish_paths=(docs/data/latest.json docs/data/occupancy.json)
  (cd "$WORKSPACE" && "$VENV" -m padsplit_scraper.publication_guard) || return 1
  git -C "$WORKSPACE" add -- "${publish_paths[@]}"

  if git -C "$WORKSPACE" diff --cached --quiet -- "${publish_paths[@]}"; then
    echo "[$(date)] Nothing to commit"
    return
  fi

  git -C "$WORKSPACE" commit --only -m "$msg" -- "${publish_paths[@]}" || return
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

run_phase "PadSplit scraper (messages only)" "$VENV" "$WORKSPACE/padsplit_scraper/scraper.py" --messages-only
run_phase "Lock code automation" "$VENV" "$WORKSPACE/padsplit_scraper/lock_codes.py"
run_phase "Codes history catch-up" "$VENV" "$WORKSPACE/padsplit_scraper/codes_history.py"
run_phase "PadSplit lockout replies" "$VENV" "$WORKSPACE/padsplit_scraper/lockout_reply.py"
run_phase "PadSplit leak replies" "$VENV" "$WORKSPACE/padsplit_scraper/leak_reply.py"

echo "[$(date)] Afternoon run complete"

commit_and_push "chore: afternoon data $(date -u +%Y-%m-%dT%H:%M:%SZ)"
