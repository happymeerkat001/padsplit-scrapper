#!/usr/bin/env bash
# Monthly PadSplit SEO / vacancy advice. 9:00am CT on the 1st via launchd or systemd.
# Do not run from GitHub Actions. Does not change prices or Instant Book.
# bash or zsh. Workspace defaults to this script's directory.
set -euo pipefail

_padsplit_self=$0
case "$_padsplit_self" in
  /*) ;;
  *) _padsplit_self=$(pwd)/$_padsplit_self ;;
esac
PADSPLIT_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$_padsplit_self")" && pwd -P)
# shellcheck source=scripts/run_common.sh
. "$PADSPLIT_SCRIPT_DIR/scripts/run_common.sh"
padsplit_bootstrap "padsplit-seo-monthly.lock"

if [ -n "${GITHUB_ACTIONS:-}" ] || [ -n "${CI:-}" ]; then
  echo "[$(date)] CI must not Discord-post SEO monthly; exiting"
  exit 0
fi

padsplit_acquire_lock "SEO monthly" || exit 0
trap padsplit_release_lock EXIT

echo "[$(date)] Starting monthly SEO / vacancy advice"
"$PYTHON" "$WORKSPACE/padsplit_scraper/seo_monthly.py"
echo "[$(date)] Monthly SEO / vacancy advice complete"
