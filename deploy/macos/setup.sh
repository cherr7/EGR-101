#!/bin/zsh
set -e
if [[ "$EUID" == "0" ]]; then
  print -u2 "Run setup.sh as the normal Mac account. Use install-daemon.sh for a system installation."
  exit 1
fi
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATE="$FABRIC_STATE_DIR"
[[ -n "$STATE" ]] || STATE="$HOME/Library/Application Support/Fabric Conditioning"
if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "This setup script targets macOS."
  exit 1
fi
PYTHON="$FABRIC_SETUP_PYTHON"
if [[ -z "$PYTHON" ]]; then
  for candidate in /opt/homebrew/opt/python@3.14/bin/python3.14 /opt/homebrew/bin/python3.14 /usr/local/bin/python3.14 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [[ -x "$candidate" ]]; then PYTHON="$candidate"; break; fi
  done
fi
if [[ -z "$PYTHON" ]]; then
  print -u2 "Install Python first: brew install python@3.14"
  exit 1
fi
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or later is required"'
if [[ ! -x /opt/homebrew/opt/postgresql@18/bin/pg_ctl && ! -x /usr/local/opt/postgresql@18/bin/pg_ctl && -z "$FABRIC_POSTGRES_BIN" ]]; then
  print -u2 "Install PostgreSQL first: brew install postgresql@18"
  exit 1
fi
mkdir -p "$STATE"
chmod 700 "$STATE"
if [[ ! -x "$STATE/runtime/bin/python" ]]; then "$PYTHON" -m venv "$STATE/runtime"; fi
"$STATE/runtime/bin/python" -m pip install --disable-pip-version-check -r "$ROOT/backend/requirements.txt"
if [[ "$1" != "--skip-frontend" ]]; then
  if ! command -v npm >/dev/null; then print -u2 "Install Node.js first: brew install node"; exit 1; fi
  (cd "$ROOT/frontend" && npm ci && npm run build)
fi
"$STATE/runtime/bin/python" "$ROOT/deploy/macos/build_app.py"
print "Installed the user runtime and built output/macOS/Fabric Conditioning.app."
print "Double-click the app to start the local host and open the staff interface."
print "For startup before interactive login, see docs/MAC_HOST.md."
