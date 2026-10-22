#!/bin/sh
# Isolate browser-created batches from the normal application database.
set -eu
. "$(dirname -- "$0")/common.sh"
exists=$(compose exec -T db sh -c 'exec psql -U "$POSTGRES_USER" -d postgres -At' <<'SQL'
SELECT 1 FROM pg_database WHERE datname = 'conditioning_browser_test';
SQL
)
if [ "$exists" != "1" ]; then
    compose exec -T db sh -c 'exec createdb -U "$POSTGRES_USER" conditioning_browser_test'
fi
echo "Browser database ready: conditioning_browser_test (existing data preserved)."
