#!/bin/sh
# Usage: sudo install.sh LIVE_UI_PACKAGE API_ORIGIN DESKTOP_USER LAN_DNS_IPV4 [STATION_ID]
# Add --check first for a read-only preflight, including on the development Mac.
set -eu
check_only=false
if [ "${1:-}" = "--check" ]; then check_only=true; shift; fi
if [ "$#" -lt 4 ] || [ "$#" -gt 5 ]; then
    echo "Usage: $0 [--check] LIVE_UI_PACKAGE API_ORIGIN DESKTOP_USER LAN_DNS_IPV4 [STATION_ID]" >&2
    exit 2
fi
ui_source=$1
api_origin=$2
kiosk_user=$3
dns_resolver=$4
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
station_id=$(python3 "$source_dir/validate-package.py" "$ui_source" "$api_origin" "$dns_resolver" "${5:-}")
if [ "$check_only" = true ]; then
    echo "Live React station package validated for $station_id. No system changes made."
    exit 0
fi
if [ "$(id -u)" -ne 0 ]; then echo "Installation requires root; use --check for read-only preflight." >&2; exit 2; fi
for executable in nginx chromium python3 systemctl getent; do
    command -v "$executable" >/dev/null || { echo "Install prerequisite: $executable" >&2; exit 1; }
done
kiosk_home=$(getent passwd "$kiosk_user" | cut -d: -f6)
if [ -z "$kiosk_home" ] || [ ! -d "$kiosk_home" ]; then
    echo "Unknown desktop user: $kiosk_user" >&2
    exit 1
fi
if [ -e /etc/nginx/sites-enabled/conditioning-tracker ]; then
    echo "The older kiosk occupies port 8080. Stop it and disable its nginx site before installing this alternative." >&2
    exit 1
fi
kiosk_group=$(id -gn "$kiosk_user")
install -d /opt/fabric-scanner/ui /opt/fabric-scanner/bin
cp -R "$ui_source/." /opt/fabric-scanner/ui/
install -m 0755 "$source_dir/browser.sh" "$source_dir/watchdog.py" /opt/fabric-scanner/bin/
python3 - "$station_id" <<'PY'
from pathlib import Path
import json
import sys
station = sys.argv[1]
rooms = {"shared-door": ("shared", "Shared conditioning room"),
         "ppe-door": ("ppe", "PPE conditioning room"),
         "footwear-door": ("footwear", "Footwear conditioning room")}
room_id, room_name = rooms.get(station, (station, "Conditioning station " + station))
Path("/opt/fabric-scanner/ui/station-config.js").write_text(
    "window.fabricStationConfig = " + json.dumps({"stationId": station, "stationName": room_name + " door",
                                               "roomId": room_id, "roomName": room_name, "fixed": True,
                                               "targetWidth": 1280, "targetHeight": 720}) + ";\n")
Path("/opt/fabric-scanner/station.env").write_text(
    "KIOSK_URL=http://127.0.0.1:8080/station.html?station=" + station + "#kiosk\n")
PY
chmod -R a+rX /opt/fabric-scanner/ui
sed -e "s|__API_ORIGIN__|$api_origin|g" -e "s|__DNS_RESOLVER__|$dns_resolver|g" \
    "$source_dir/nginx.conf.template" > /etc/nginx/sites-available/fabric-scanner
ln -sf /etc/nginx/sites-available/fabric-scanner /etc/nginx/sites-enabled/fabric-scanner
nginx -t
systemctl enable nginx
systemctl restart nginx
install -d -o "$kiosk_user" -g "$kiosk_group" \
    "$kiosk_home/.config/systemd/user" "$kiosk_home/.config/labwc"
for name in fabric-scanner.target fabric-scanner-browser.service fabric-scanner-watchdog.service; do
    install -m 0644 -o "$kiosk_user" -g "$kiosk_group" "$source_dir/$name" "$kiosk_home/.config/systemd/user/$name"
done
autostart="$kiosk_home/.config/labwc/autostart"
if [ ! -e "$autostart" ]; then
    if [ -f /etc/xdg/labwc/autostart ]; then cp /etc/xdg/labwc/autostart "$autostart"; else touch "$autostart"; fi
fi
if ! grep -Fq '# fabric-scanner dedicated kiosk' "$autostart"; then
    cat >> "$autostart" <<'AUTOSTART'

# fabric-scanner dedicated kiosk
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
systemctl --user daemon-reload
systemctl --user restart fabric-scanner.target &
AUTOSTART
fi
chown "$kiosk_user:$kiosk_group" "$autostart"
chmod 0755 "$autostart"
echo "Installed. Enable Desktop Autologin for the chosen user, disable screen blanking, then reboot."
echo "The dedicated live page is http://127.0.0.1:8080/station.html?station=$station_id#kiosk."
