#!/bin/sh
set -eu
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/fabric-scanner"
mkdir -p "$state_dir"
export KIOSK_URL="${KIOSK_URL:-http://127.0.0.1:8080/station.html?station=shared-door#kiosk}"
# Open a local inline page immediately, including while nginx is still starting.
# It polls only local UI readiness; central API failure stays visible in the app.
startup_url=$(python3 - <<'PY'
import os
from urllib.parse import quote
print("file:///opt/fabric-scanner/bin/startup.html#" + quote(os.environ["KIOSK_URL"], safe=""))
PY
)
# Hardware fallback when a Chromium build cannot leave the file-origin starting page.
if [ "${KIOSK_DIRECT_START:-false}" = true ]; then startup_url=$KIOSK_URL; fi
exec /usr/bin/chromium \
    --kiosk \
    --noerrdialogs \
    --no-first-run \
    --no-default-browser-check \
    --disable-session-crashed-bubble \
    --password-store=basic \
    --ozone-platform=wayland \
    --user-data-dir="$state_dir" \
    "$startup_url"
