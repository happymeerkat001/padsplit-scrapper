#!/usr/bin/env bash
# Daily Don-field Quo SMS blast (Don + Dad + Ang GV, one group). Morning 7:00am CT only.
# Hard clock guard: America/Chicago hour > 7 exits 0 (no afternoon/evening blast).
# Hour < 7 is the prior-day catch-up path. Launchd is Hour=7 only.
# Primary transport is Quo SMS (QUO_API_KEY). Google Voice and Messages.app are
# Mac fallbacks and are skipped when uname is not Darwin or their paths are missing.
# bash or zsh. Do not run from GitHub Actions.
set -euo pipefail

_padsplit_self=$0
case "$_padsplit_self" in
  /*) ;;
  *) _padsplit_self=$(pwd)/$_padsplit_self ;;
esac
PADSPLIT_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$_padsplit_self")" && pwd -P)
# shellcheck source=scripts/run_common.sh
. "$PADSPLIT_SCRIPT_DIR/scripts/run_common.sh"
padsplit_bootstrap "padsplit-field-mms.lock"

if [ -n "${GITHUB_ACTIONS:-}" ] || [ -n "${CI:-}" ]; then
  echo "[$(date)] CI must not send MMS; exiting"
  exit 0
fi

CT_HOUR=$((10#$(TZ=America/Chicago date +%H)))
if [ "$CT_HOUR" -gt 7 ]; then
  echo "[$(date)] Field MMS is morning-only (America/Chicago); hour=${CT_HOUR} > 7; skipping"
  exit 0
fi

padsplit_note_mac_only_field_mms

padsplit_acquire_lock "Field MMS" || exit 0
trap padsplit_release_lock EXIT

echo "[$(date)] Starting field MMS window"
"$PYTHON" "$WORKSPACE/padsplit_scraper/field_mms.py"
echo "[$(date)] Field MMS window complete"
