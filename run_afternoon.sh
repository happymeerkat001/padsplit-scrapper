#!/usr/bin/env bash
# Afternoon scrape. bash or zsh. Workspace defaults to this script's directory.
set -euo pipefail

_padsplit_self=$0
case "$_padsplit_self" in
  /*) ;;
  *) _padsplit_self=$(pwd)/$_padsplit_self ;;
esac
PADSPLIT_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$_padsplit_self")" && pwd -P)
# shellcheck source=scripts/run_common.sh
. "$PADSPLIT_SCRIPT_DIR/scripts/run_common.sh"
padsplit_bootstrap "padsplit-scraper-afternoon.lock"

padsplit_acquire_lock "Afternoon run" || exit 0
trap padsplit_release_lock EXIT

echo "[$(date)] Starting afternoon run"

# Pull remote changes before scraping, unless this is a no-push shadow run.
padsplit_git_sync

run_phase "PadSplit scraper (messages only)" "$PYTHON" "$WORKSPACE/padsplit_scraper/scraper.py" --messages-only
run_phase "Spanish Moss lock codes" "$PYTHON" "$WORKSPACE/padsplit_scraper/lock_codes.py"
run_phase "Codes history catch-up" "$PYTHON" "$WORKSPACE/padsplit_scraper/codes_history.py"
run_phase "PadSplit lockout replies" "$PYTHON" "$WORKSPACE/padsplit_scraper/lockout_reply.py"
run_phase "PadSplit leak replies" "$PYTHON" "$WORKSPACE/padsplit_scraper/leak_reply.py"

echo "[$(date)] Afternoon run complete"

padsplit_commit_and_push "chore: afternoon data $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  padsplit_scraper/output/latest.json \
  padsplit_scraper/output/stats.json \
  padsplit_scraper/output/monthly_history.json \
  docs/data/latest.json
