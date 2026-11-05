#!/bin/sh
# Create an encrypted PostgreSQL custom-format archive outside the checkout.
set -eu
if [ "$#" -ne 2 ]; then
  echo 'Usage: backup-db.sh AGE_RECIPIENT ABSOLUTE_OUTPUT.age' >&2; exit 2
fi
recipient=$1
output=$2
case "$recipient" in age1*) ;; *) echo 'Recipient must be an age public recipient.' >&2; exit 2;; esac
case "$output" in /*.age) ;; *) echo 'Output must be an absolute .age path.' >&2; exit 2;; esac
[ -n "${PGDATABASE:-}" ] || { echo 'PGDATABASE is required.' >&2; exit 2; }
[ ! -e "$output" ] || { echo 'Output already exists.' >&2; exit 2; }
parent=$(CDPATH= cd -P -- "$(dirname -- "$output")" && pwd)
repo=$(CDPATH= cd -P -- "$(dirname -- "$0")/.." && pwd)
case "$parent/" in "$repo/"*) echo 'Backups must remain outside the checkout.' >&2; exit 2;; esac
umask 077
tmp=$(mktemp "$parent/.padsplit-backup.XXXXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM
# Shell pipelines do not reliably propagate the first process status. A FIFO
# and explicit wait make a failed pg_dump fail the entire operation.
fifo=$(mktemp -d "$parent/.padsplit-pipe.XXXXXXXX")
trap 'rm -f "$tmp"; rm -rf "$fifo"' EXIT HUP INT TERM
mkfifo "$fifo/stream"
pg_dump --format=custom --no-owner --no-privileges > "$fifo/stream" &
dump_pid=$!
status=0
age -r "$recipient" -o "$tmp" < "$fifo/stream" || status=$?
wait "$dump_pid" || status=1
[ "$status" -eq 0 ] || { echo 'Encrypted backup failed.' >&2; exit 1; }
# Hard link creation is atomic and fails if another process created output.
ln "$tmp" "$output"
rm -f "$tmp"
echo 'Encrypted database archive created.'
