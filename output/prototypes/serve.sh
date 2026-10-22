#!/bin/sh
set -eu
prototype_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 -m http.server "${PROTOTYPE_PORT:-8090}" --bind 127.0.0.1 --directory "$prototype_dir"
