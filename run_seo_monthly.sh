#!/usr/bin/env zsh
set -euo pipefail

# Monthly PadSplit SEO / vacancy advice. 9:00am CT on the 1st via launchd.
# Not live until this branch is merged and this Mac has pulled + installed the LaunchAgent.
# Do not run from GitHub Actions. Does not change prices or Instant Book.
# Chief Grok Bot cron fallback stays until this LaunchAgent is loaded.

WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"
VENV="$WORKSPACE/venv/bin/python3"
LOCK_DIR="/private/tmp/padsplit-seo-monthly.lock"

if [ -n "${GITHUB_ACTIONS:-}" ] || [ -n "${CI:-}" ]; then
  echo "[$(date)] CI must not Discord-post SEO monthly; exiting"
  exit 0
fi

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    return 0
  fi
  echo "[$(date)] SEO monthly already in progress; skipping"
  return 1
}

release_lock() {
  rm -f "$LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

acquire_lock || exit 0
trap release_lock EXIT

echo "[$(date)] Starting monthly SEO / vacancy advice"
"$VENV" "$WORKSPACE/padsplit_scraper/seo_monthly.py"
echo "[$(date)] Monthly SEO / vacancy advice complete"
