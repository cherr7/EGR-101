#!/bin/sh
# Usage: scripts/backup.sh /absolute/path/conditioning-2026-10-02.dump
set -eu
. "$(dirname -- "$0")/common.sh"

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 OUTPUT.dump" >&2
    exit 2
fi
output=$1
if [ -e "$output" ] || [ -L "$output" ]; then
    echo "Refusing to overwrite an existing backup: $output" >&2
    exit 1
fi
umask 077
temporary="${output}.partial.$$"
trap 'rm -f -- "$temporary"' EXIT HUP INT TERM
compose exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$temporary"
# Hard-link creation also refuses an output created while pg_dump ran.
ln -- "$temporary" "$output"
rm -- "$temporary"
trap - EXIT HUP INT TERM
echo "Backup written: $output"
