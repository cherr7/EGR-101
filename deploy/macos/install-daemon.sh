#!/bin/zsh
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PREFERRED="$FABRIC_SETUP_PYTHON"
[[ -n "$PREFERRED" ]] || PREFERRED=/opt/homebrew/opt/python@3.14/bin/python3.14
for PYTHON in "$PREFERRED" /opt/homebrew/bin/python3.14 /usr/local/bin/python3.14 /usr/bin/python3; do
  if [[ -x "$PYTHON" ]]; then
    exec "$PYTHON" "$ROOT/deploy/macos/install_daemon.py" "$@"
  fi
done
print -u2 "Install Python first."
exit 1
