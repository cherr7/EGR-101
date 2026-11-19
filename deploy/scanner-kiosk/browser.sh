#!/bin/sh
set -eu
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/fabric-scanner"
mkdir -p "$state_dir"
# The first hand-made setup also started ~/kiosk/kiosk.sh at login: a second full-screen browser
# pointed straight at the Mac, with no scanner. When it sat on top, scans saved through this page
# while the screen in front kept showing "Ready". Remove its autostart line and stop it.
autostart="$HOME/.config/labwc/autostart"
if [ -f "$autostart" ] && grep -q 'kiosk/kiosk\.sh' "$autostart"; then
    kept=$(grep -v 'kiosk/kiosk\.sh' "$autostart" || true)
    printf '%s\n' "$kept" > "$autostart"
fi
pkill -f 'kiosk/kiosk\.sh' 2>/dev/null || true
# Any other full-screen Chromium covers this one; only the browser using this state folder belongs.
for pid in $(pgrep -f 'chromium.*--kiosk' 2>/dev/null || true); do
    case "$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)" in
        *"--user-data-dir=$state_dir"*) ;;
        *) kill "$pid" 2>/dev/null || true ;;
    esac
done
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
exec "${CHROMIUM:-/usr/bin/chromium}" \
    --kiosk \
    --noerrdialogs \
    --no-first-run \
    --no-default-browser-check \
    --disable-session-crashed-bubble \
    --password-store=basic \
    --ozone-platform=wayland \
    --user-data-dir="$state_dir" \
    "$startup_url"
