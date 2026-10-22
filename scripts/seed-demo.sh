#!/bin/sh
# Explicit and repeatable demo creation; never resets the database.
set -eu
. "$(dirname -- "$0")/common.sh"
compose exec -T backend python -m app.seed
