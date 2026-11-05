#!/bin/sh
# Restore only into an explicitly named disposable staging database.
set -eu
if [ "$#" -ne 3 ]; then
  echo 'Usage: restore-db.sh AGE_IDENTITY BACKUP.age padsplit_restore_NAME' >&2; exit 2
fi
identity=$1
archive=$2
target=$3
case "$target" in padsplit_restore_[a-zA-Z0-9_]*) ;; *) echo 'Target must begin padsplit_restore_ and contain only letters, digits, and underscores.' >&2; exit 2;; esac
case "$target" in *[!a-zA-Z0-9_]*) echo 'Invalid target database name.' >&2; exit 2;; esac
[ -f "$identity" ] && [ -f "$archive" ] || { echo 'Identity or archive not found.' >&2; exit 2; }
# libpq can use a separate address or service configuration even when PGHOST
# looks local. Reject both instead of silently restoring to a different server.
[ -z "${PGHOSTADDR:-}" ] && [ -z "${PGSERVICE:-}" ] && [ -z "${PGSERVICEFILE:-}" ] || {
  echo 'PGHOSTADDR and PostgreSQL service configuration are forbidden for staging restore.' >&2; exit 2;
}
[ -n "${PGHOST:-}" ] || { echo 'Explicit PGHOST is required.' >&2; exit 2; }
case "$PGHOST" in *,*) echo 'Multiple PostgreSQL hosts are forbidden for staging restore.' >&2; exit 2;; esac
case "$PGHOST" in 127.0.0.1|localhost|/*) ;; *) echo 'Restore must target an explicit local PostgreSQL instance.' >&2; exit 2;; esac
# The archive is decrypted through a private FIFO, not a plaintext file.
umask 077
pipe_dir=$(mktemp -d "${TMPDIR:-/tmp}/padsplit-restore.XXXXXXXX")
trap 'rm -rf "$pipe_dir"' EXIT HUP INT TERM
mkfifo "$pipe_dir/stream"
age --decrypt -i "$identity" "$archive" > "$pipe_dir/stream" &
age_pid=$!
status=0
pg_restore --exit-on-error --no-owner --no-privileges --dbname "$target" < "$pipe_dir/stream" || status=$?
wait "$age_pid" || status=1
[ "$status" -eq 0 ] || { echo 'Restore failed; discard the staging database.' >&2; exit 1; }
echo 'Encrypted archive restored into staging database.'
