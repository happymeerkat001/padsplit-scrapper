#!/usr/bin/env zsh
set -euo pipefail

# Daily Don-field group MMS. 6:00am CT and 7:00pm CT via launchd.
# Not live until this branch is merged and this Mac has pulled + installed the LaunchAgent.
# Do not run from GitHub Actions.

WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"
VENV="$WORKSPACE/venv/bin/python3"
LOCK_DIR="/private/tmp/padsplit-field-mms.lock"

if [ -n "${GITHUB_ACTIONS:-}" ] || [ -n "${CI:-}" ]; then
  echo "[$(date)] CI must not send MMS; exiting"
  exit 0
fi

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    return 0
  fi
  echo "[$(date)] Field MMS already in progress; skipping"
  return 1
}

release_lock() {
  rm -f "$LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

acquire_lock || exit 0
trap release_lock EXIT

echo "[$(date)] Starting field MMS window"
"$VENV" "$WORKSPACE/padsplit_scraper/field_mms.py"
echo "[$(date)] Field MMS window complete"
