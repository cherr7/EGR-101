#!/bin/sh
set -eu
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/fabric-scanner"
mkdir -p "$state_dir"
export KIOSK_URL="${KIOSK_URL:-http://127.0.0.1:8080/station.html}"
# Wait only for the local UI, never for the central API or the internet.
python3 - <<'PY'
import os
import time
import urllib.error
import urllib.request
for attempt in range(30):
    try:
        with urllib.request.urlopen(os.environ["KIOSK_URL"], timeout=2) as response:
            if response.status == 200:
                break
    except (urllib.error.URLError, TimeoutError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Local kiosk page unavailable; systemd will retry.")
PY
exec /usr/bin/chromium \
    --kiosk \
    --noerrdialogs \
    --no-first-run \
    --no-default-browser-check \
    --disable-session-crashed-bubble \
    --ozone-platform=wayland \
    --user-data-dir="$state_dir" \
    "$KIOSK_URL"
