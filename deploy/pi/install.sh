#!/bin/sh
# Provision the already-built local UI on Raspberry Pi OS Desktop (labwc).
# Usage: sudo deploy/pi/install.sh frontend/dist http://SERVER:8000 KIOSK_USER LAN_DNS_IPV4
set -eu
if [ "$(id -u)" -ne 0 ] || [ "$#" -ne 4 ]; then
    echo "Usage (as root): $0 BUILT_UI_DIR CENTRAL_API_ORIGIN KIOSK_USER DNS_IPV4" >&2
    exit 2
fi
ui_source=$1
api_origin=$2
kiosk_user=$3
dns_resolver=$4
template_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ ! -f "$ui_source/index.html" ]; then
    echo "Build the frontend first; UI directory has no index.html." >&2
    exit 1
fi
for executable in nginx chromium python3 systemctl; do
    command -v "$executable" >/dev/null || {
        echo "Install prerequisite: $executable" >&2
        exit 1
    }
done
python3 - "$api_origin" "$dns_resolver" <<'PY'
import ipaddress
import re
import sys
from urllib.parse import urlsplit

origin, resolver = sys.argv[1:]
if not re.fullmatch(r"https?://(?:\[[a-fA-F0-9:]+\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?", origin):
    raise SystemExit("Central API origin must be http(s)://host[:port], without a path or credentials.")
parsed = urlsplit(origin)
if parsed.port is not None and not 1 <= parsed.port <= 65535:
    raise SystemExit("Invalid API port.")
if ipaddress.ip_address(resolver).version != 4:
    raise SystemExit("Use an IPv4 LAN DNS resolver.")
PY
user_home=$(getent passwd "$kiosk_user" | cut -d: -f6)
if [ -z "$user_home" ] || [ ! -d "$user_home" ]; then
    echo "Unknown kiosk desktop user: $kiosk_user" >&2
    exit 1
fi
kiosk_group=$(id -gn "$kiosk_user")
install -d /opt/conditioning-tracker/ui /opt/conditioning-tracker/bin
cp -R "$ui_source/." /opt/conditioning-tracker/ui/
chmod -R a+rX /opt/conditioning-tracker/ui
install -m 0755 "$template_dir/kiosk-browser.sh" /opt/conditioning-tracker/bin/kiosk-browser.sh
sed -e "s|__API_ORIGIN__|$api_origin|g" -e "s|__DNS_RESOLVER__|$dns_resolver|g" \
    "$template_dir/nginx.conf.template" > /etc/nginx/sites-available/conditioning-tracker
ln -sf /etc/nginx/sites-available/conditioning-tracker /etc/nginx/sites-enabled/conditioning-tracker
nginx -t
systemctl enable nginx
systemctl restart nginx

install -d -o "$kiosk_user" -g "$kiosk_group" \
    "$user_home/.config/systemd/user" "$user_home/.config/labwc"
install -m 0644 -o "$kiosk_user" -g "$kiosk_group" \
    "$template_dir/conditioning-kiosk.service" \
    "$user_home/.config/systemd/user/conditioning-kiosk.service"
autostart="$user_home/.config/labwc/autostart"
touch "$autostart"
if ! grep -Fq '# conditioning-tracker kiosk' "$autostart"; then
    cat >> "$autostart" <<'AUTOSTART'

# conditioning-tracker kiosk
systemctl --user import-environment WAYLAND_DISPLAY XDG_RUNTIME_DIR
systemctl --user daemon-reload
systemctl --user restart conditioning-kiosk.service &
AUTOSTART
fi
chown "$kiosk_user:$kiosk_group" "$autostart"
echo "Installed local UI. Enable Desktop Autologin for $kiosk_user, then reboot."
echo "Kiosk opens http://127.0.0.1:8080 and restarts after browser exit."
