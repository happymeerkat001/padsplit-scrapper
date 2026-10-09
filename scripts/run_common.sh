# Shared bootstrap for run_morning.sh, run_afternoon.sh, run_field_mms.sh,
# and run_seo_monthly.sh. Source this file; do not execute it.
# Valid under both bash and zsh (launchd invokes these scripts with zsh).
# Already-set variables are kept, matching python-dotenv's default.

padsplit_load_env_file() {
  env_file=$1
  if [ ! -f "$env_file" ]; then
    echo "[$(date)] Env file not found ($env_file); continuing with existing environment"
    return 0
  fi
  echo "[$(date)] Loading env from $env_file"
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    trimmed=$line
    while :; do
      case "$trimmed" in
        ' '*) trimmed=${trimmed# } ;;
        '	'*) trimmed=${trimmed#	} ;;
        *) break ;;
      esac
    done
    case "$trimmed" in
      ''|'#'*) continue ;;
      'export '*) trimmed=${trimmed#export } ;;
    esac
    case "$trimmed" in
      *=*) ;;
      *) continue ;;
    esac
    key=${trimmed%%=*}
    while :; do
      case "$key" in
        *' ') key=${key% } ;;
        *'	') key=${key%	} ;;
        *) break ;;
      esac
    done
    case "$key" in
      ''|*[!A-Za-z0-9_]*|[0-9]*) continue ;;
    esac
    val=${trimmed#*=}
    while :; do
      case "$val" in
        ' '*) val=${val# } ;;
        '	'*) val=${val#	} ;;
        *) break ;;
      esac
    done
    case "$val" in
      \"*\") val=${val#\"}; val=${val%\"} ;;
      \'*\') val=${val#\'}; val=${val%\'} ;;
    esac
    if printenv "$key" >/dev/null 2>&1; then
      continue
    fi
    export "$key=$val"
  done < "$env_file"
}

# Call at top level of each run_*.sh after PADSPLIT_SCRIPT_DIR is set.
# $0 inside a zsh function is the function name, so the caller resolves it.
padsplit_bootstrap() {
  lock_name=$1
  if [ -n "${PADSPLIT_ENV_FILE:-}" ]; then
    padsplit_load_env_file "$PADSPLIT_ENV_FILE"
  else
    _provisional=${PADSPLIT_WORKSPACE:-$PADSPLIT_SCRIPT_DIR}
    padsplit_load_env_file "$_provisional/.env"
  fi
  WORKSPACE=${PADSPLIT_WORKSPACE:-$PADSPLIT_SCRIPT_DIR}
  if [ -n "${PADSPLIT_PYTHON:-}" ]; then
    PYTHON=$PADSPLIT_PYTHON
  elif [ -x "$WORKSPACE/venv/bin/python3" ]; then
    PYTHON=$WORKSPACE/venv/bin/python3
  elif [ -x "$WORKSPACE/.venv/bin/python3" ]; then
    PYTHON=$WORKSPACE/.venv/bin/python3
  else
    PYTHON=$(command -v python3 || true)
  fi
  if [ -z "$PYTHON" ]; then
    echo "[$(date)] python3 not found; set PADSPLIT_PYTHON or create venv/bin/python3" >&2
    exit 1
  fi
  _tmp=${TMPDIR:-/tmp}
  _tmp=${_tmp%/}
  LOCK_DIR=$_tmp/$lock_name
  export WORKSPACE PYTHON LOCK_DIR
}

padsplit_no_push() {
  case "${PADSPLIT_NO_PUSH:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

padsplit_is_macos() {
  [ "$(uname -s)" = "Darwin" ]
}

padsplit_acquire_lock() {
  label=$1
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    return 0
  fi
  echo "[$(date)] $label already in progress; skipping"
  return 1
}

padsplit_release_lock() {
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

padsplit_git_sync() {
  if padsplit_no_push; then
    echo "[$(date)] PADSPLIT_NO_PUSH=1; skipping git pull"
    return 0
  fi
  echo "[$(date)] Syncing with GitHub..."
  set +e
  git -C "$WORKSPACE" pull --rebase
  set -e
}

# PADSPLIT_NO_PUSH skips pull/commit/push only. It does not change send flags.
padsplit_commit_and_push() {
  msg=$1
  shift
  if padsplit_no_push; then
    echo "[$(date)] PADSPLIT_NO_PUSH=1; skipping git commit/pull/push. Would commit:"
    for f in "$@"; do
      printf '  %s\n' "$f"
    done
    return 0
  fi
  git -C "$WORKSPACE" add "$@" 2>/dev/null || true

  if git -C "$WORKSPACE" diff --cached --quiet; then
    echo "[$(date)] Nothing to commit"
    return 0
  fi

  git -C "$WORKSPACE" commit -m "$msg" || return 0
  set +e
  git -C "$WORKSPACE" pull --rebase
  git -C "$WORKSPACE" push
  set -e
}

# Obsidian writes a vault on disk. Skip unless this is macOS and the vault exists.
padsplit_run_obsidian_digest() {
  if ! padsplit_is_macos; then
    echo "[$(date)] Skipping Obsidian daily digest (not macOS)"
    return 0
  fi
  vault=${OBSIDIAN_DAILY_NOTES_DIR:-}
  if [ -z "$vault" ] || [ ! -d "$vault" ]; then
    echo "[$(date)] Skipping Obsidian daily digest (vault path missing)"
    return 0
  fi
  run_phase "Obsidian daily digest" "$PYTHON" "$WORKSPACE/obsidian_daily_digest.py"
}

# Google Voice/Playwright and Messages.app are Mac fallbacks.
# Quo HTTP is unchanged and still follows FIELD_MMS_ENABLE.
# PADSPLIT_MESSAGES_APP overrides the Messages.app bundle path (unset on the Mac).
padsplit_note_mac_only_field_mms() {
  if ! padsplit_is_macos; then
    echo "[$(date)] Skipping Mac-only Google Voice/Playwright Chrome-profile fallback (not macOS)"
    echo "[$(date)] Skipping Mac-only Messages.app (not macOS)"
    export FIELD_MMS_SKIP_GOOGLE_VOICE=1
    export FIELD_MMS_SKIP_MESSAGES=1
    return 0
  fi
  if [ -n "${FIELD_MMS_CHROME_USER_DATA_DIR:-}" ]; then
    chrome=$FIELD_MMS_CHROME_USER_DATA_DIR
  else
    chrome="$HOME/Library/Application Support/Google/Chrome"
  fi
  if [ ! -d "$chrome" ]; then
    echo "[$(date)] Skipping Google Voice/Playwright Chrome-profile fallback (Chrome profile path missing)"
    export FIELD_MMS_SKIP_GOOGLE_VOICE=1
  fi
  if [ -n "${PADSPLIT_MESSAGES_APP:-}" ]; then
    messages_app=$PADSPLIT_MESSAGES_APP
  elif [ -d "/System/Applications/Messages.app" ]; then
    messages_app=/System/Applications/Messages.app
  elif [ -d "/Applications/Messages.app" ]; then
    messages_app=/Applications/Messages.app
  else
    messages_app=
  fi
  if [ -z "$messages_app" ] || [ ! -d "$messages_app" ]; then
    echo "[$(date)] Skipping Mac-only Messages.app (Messages.app path missing)"
    export FIELD_MMS_SKIP_MESSAGES=1
  fi
}
