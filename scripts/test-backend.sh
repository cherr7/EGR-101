#!/bin/sh
# Integration tests use conditioning_test, never the normal application DB.
set -eu
. "$(dirname -- "$0")/common.sh"

exists=$(compose exec -T db sh -c 'exec psql -U "$POSTGRES_USER" -d postgres -At' <<'SQL'
SELECT 1 FROM pg_database WHERE datname = 'conditioning_test';
SQL
)
if [ "$exists" != "1" ]; then
    compose exec -T db sh -c 'exec createdb -U "$POSTGRES_USER" conditioning_test'
fi
compose exec -T backend sh -c 'export TEST_DATABASE_URL="${DATABASE_URL%/*}/conditioning_test"; exec python -m pytest -q'
