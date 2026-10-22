#!/bin/sh
# Shared helpers; sourced by the scripts in this directory.
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

compose() {
    docker compose --project-directory "$project_dir" --file "$project_dir/compose.yaml" "$@"
}
