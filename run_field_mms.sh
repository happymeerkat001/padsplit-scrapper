#!/usr/bin/env zsh
set -euo pipefail

# Daily Don-field Quo SMS blast (Don + Dad + Ang GV, one group). Morning 7:00am CT only.
# Hard clock guard: America/Chicago hour > 7 exits 0 (no afternoon/evening blast).
# Hour < 7 is the prior-day catch-up path. Launchd is Hour=7 only.
# Primary transport is Quo SMS (QUO_API_KEY); GV / Messages are fallbacks.
# Not live until this branch is merged and this Mac has pulled + installed the LaunchAgent.
# Do not run from GitHub Actions.

WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"
VENV="$WORKSPACE/venv/bin/python3"
LOCK_DIR="/private/tmp/padsplit-field-mms.lock"

if [ -n "${GITHUB_ACTIONS:-}" ] || [ -n "${CI:-}" ]; then
  echo "[$(date)] CI must not send MMS; exiting"
  exit 0
fi

CT_HOUR=$((10#$(TZ=America/Chicago date +%H)))
if [ "$CT_HOUR" -gt 7 ]; then
  echo "[$(date)] Field MMS is morning-only (America/Chicago); hour=${CT_HOUR} > 7; skipping"
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
