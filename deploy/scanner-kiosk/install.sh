#!/bin/sh
# Usage: sudo install.sh UI_DIRECTORY API_ORIGIN DESKTOP_USER LAN_DNS_IPV4
set -eu
if [ "$(id -u)" -ne 0 ] || [ "$#" -ne 4 ]; then
    echo "Usage (root): $0 UI_DIRECTORY API_ORIGIN DESKTOP_USER LAN_DNS_IPV4" >&2
    exit 2
fi
ui_source=$1
api_origin=$2
kiosk_user=$3
dns_resolver=$4
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -f "$ui_source/station.html" ]; then
    echo "UI_DIRECTORY must contain the dedicated station.html build." >&2
    exit 1
fi
for executable in nginx chromium python3 systemctl getent; do
    command -v "$executable" >/dev/null || { echo "Install prerequisite: $executable" >&2; exit 1; }
done
python3 - "$api_origin" "$dns_resolver" <<'PY'
import ipaddress
import re
import sys
from urllib.parse import urlsplit
origin, resolver = sys.argv[1:]
if not re.fullmatch(r"https?://(?:\[[a-fA-F0-9:]+\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?", origin):
    raise SystemExit("API_ORIGIN must be http(s)://host[:port], without credentials or path.")
parsed = urlsplit(origin)
if parsed.port is not None and not 1 <= parsed.port <= 65535:
    raise SystemExit("Invalid API port.")
if ipaddress.ip_address(resolver).version != 4:
    raise SystemExit("Use an IPv4 LAN DNS resolver.")
PY
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
install -m 0644 "$source_dir/runtime.js" /opt/fabric-scanner/ui/station-runtime.js
install -m 0755 "$source_dir/browser.sh" "$source_dir/watchdog.py" /opt/fabric-scanner/bin/
python3 - <<'PY'
from pathlib import Path
path = Path("/opt/fabric-scanner/ui/station.html")
html = path.read_text()
tag = '<script src="/station-runtime.js"></script>'
if tag not in html:
    if "</body>" not in html:
        raise SystemExit("station.html has no closing body.")
    path.write_text(html.replace("</body>", tag + "</body>"))
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
echo "The dedicated page is http://127.0.0.1:8080/station.html."
