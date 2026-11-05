#!/bin/sh
# Explicitly enabled provider adapter for an existing encrypted archive.
# No storage destination, credentials, retention, or schedule is configured here.
set -eu
[ "${PADSPLIT_BACKUP_UPLOAD_ENABLE:-}" = 1 ] || { echo 'Off-server backup upload is disabled.' >&2; exit 2; }
if [ "$#" -ne 2 ]; then echo 'Usage: upload-backup.sh ENCRYPTED_ARCHIVE DESTINATION' >&2; exit 2; fi
archive=$1
dest=$2
adapter=${PADSPLIT_BACKUP_ADAPTER:-}
retention=${PADSPLIT_BACKUP_RETENTION_DAYS:-}
case "$archive" in /*.age) ;; *) echo 'Archive must be an absolute .age file.' >&2; exit 2;; esac
[ -f "$archive" ] && [ ! -L "$archive" ] || { echo 'Archive must be a regular non-symlink file.' >&2; exit 2; }
case "$adapter" in /*) ;; *) echo 'An absolute provider adapter path is required.' >&2; exit 2;; esac
[ -f "$adapter" ] && [ -x "$adapter" ] && [ ! -L "$adapter" ] || { echo 'Provider adapter is unavailable.' >&2; exit 2; }
case "$dest" in ''|*[\?\#\@]*) echo 'A destination without URL credentials, query, or fragment is required.' >&2; exit 2;; esac
case "$retention" in ''|*[!0-9]*) echo 'An explicit retention duration in days is required.' >&2; exit 2;; esac
[ "$retention" -ge 7 ] && [ "$retention" -le 3650 ] || { echo 'Retention must be between 7 and 3650 days.' >&2; exit 2; }
# A header check only detects obvious plaintext; encryption/authenticity is
# verified separately by restoring with the offline identity.
header=$(LC_ALL=C head -c 21 "$archive")
[ "$header" = 'age-encryption.org/v1' ] || { echo 'Archive is not a binary age file.' >&2; exit 2; }
if command -v sha256sum >/dev/null 2>&1; then digest=$(sha256sum "$archive" | cut -d ' ' -f 1)
elif command -v shasum >/dev/null 2>&1; then digest=$(shasum -a 256 "$archive" | cut -d ' ' -f 1)
else echo 'A SHA-256 utility is required.' >&2; exit 2; fi
# Adapter contract: verify retention policy first; reject overwrites; verify the
# remote object checksum after upload. Adapter must not log secret credentials.
"$adapter" verify-retention "$dest" "$retention"
"$adapter" put-if-absent "$archive" "$dest" "$digest"
"$adapter" verify-object "$dest" "$digest"
echo 'Encrypted archive uploaded and remote checksum verified by configured adapter.'
