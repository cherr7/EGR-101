#!/bin/sh
# Restore into a NEW named database; never drops or replaces existing data.
# Usage: scripts/restore.sh /absolute/path/backup.dump conditioning_restore
set -eu
. "$(dirname -- "$0")/common.sh"

if [ "$#" -ne 2 ] || [ ! -f "$1" ]; then
    echo "Usage: $0 EXISTING.dump NEW_DATABASE_NAME" >&2
    exit 2
fi
input=$1
target=$2
case "$target" in
    ''|[0-9]*|*[!a-zA-Z0-9_]*)
        echo "Database name must begin with a letter/underscore and contain only letters, digits, underscores." >&2
        exit 2
        ;;
esac
case "$target" in
    postgres|template0|template1)
        echo "Choose a new application database name." >&2
        exit 2
        ;;
esac
compose exec -T db pg_restore --list < "$input" >/dev/null
# createdb fails if the name exists. There is deliberately no --clean/reset.
compose exec -T db sh -c 'exec createdb -U "$POSTGRES_USER" "$1"' sh "$target"
compose exec -T db sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$1" --single-transaction --exit-on-error --no-owner --no-acl' sh "$target" < "$input"
echo "Restored backup into new database: $target"
