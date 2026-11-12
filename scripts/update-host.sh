#!/bin/bash
# Update the Mac host to the newest release on main, with a backup first and automatic rollback.
# Run on the Mac, in the git checkout. Options: --check, --status, --ref TAG (see deploy/macos/update.py).
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only --quiet origin main 2>/dev/null || echo "Note: could not fast-forward this checkout; using the updater already here." >&2
exec python3 deploy/macos/update.py "$@"
