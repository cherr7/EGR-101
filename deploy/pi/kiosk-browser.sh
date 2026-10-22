#!/bin/sh
set -eu
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/conditioning-kiosk"
mkdir -p "$state_dir"
exec /usr/bin/chromium \
    --kiosk \
    --noerrdialogs \
    --no-first-run \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --ozone-platform=wayland \
    --user-data-dir="$state_dir" \
    "${KIOSK_URL:-http://127.0.0.1:8080}"
