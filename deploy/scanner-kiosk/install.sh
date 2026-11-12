#!/bin/sh
# Usage: sudo install.sh [--nfc] LIVE_UI_PACKAGE API_ORIGIN DESKTOP_USER LAN_DNS_IPV4 [STATION_ID]
# Add --check first for a read-only preflight, including on the development Mac.
# --nfc also enables the optional PN532 reader in the shared input bridge.
set -eu
check_only=false
with_nfc=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --check) check_only=true; shift ;;
        --nfc) with_nfc=true; shift ;;
        *) break ;;
    esac
done
if [ "$#" -lt 4 ] || [ "$#" -gt 5 ]; then
    echo "Usage: $0 [--check] [--nfc] LIVE_UI_PACKAGE API_ORIGIN DESKTOP_USER LAN_DNS_IPV4 [STATION_ID]" >&2
    exit 2
fi
ui_source=$1
api_origin=$2
kiosk_user=$3
dns_resolver=$4
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
station_id=$(python3 "$source_dir/validate-package.py" "$ui_source" "$api_origin" "$dns_resolver" "${5:-}")
for name in input-bridge.py nfc_tags.py fabric-scanner-input.service startup.html station-agent.py fabric-scanner-agent.service \
    render-nginx.py nginx.conf.template; do
    [ -f "$source_dir/$name" ] || { echo "Missing station file: $source_dir/$name" >&2; exit 1; }
done
if [ "$with_nfc" = true ]; then
    for name in nfc-requirements.txt; do
        [ -f "$source_dir/$name" ] || { echo "Missing NFC bridge file: $source_dir/$name" >&2; exit 1; }
    done
fi
if [ -n "${API_FALLBACK:-}" ]; then
    python3 "$source_dir/render-nginx.py" --check-fallback "${API_FALLBACK}" || exit 2
fi
python3 - "$source_dir/input-bridge.py" "$source_dir/nfc_tags.py" "$source_dir/watchdog.py" "$source_dir/station-agent.py" \
    "$source_dir/render-nginx.py" <<'PY'
import ast
from pathlib import Path
import sys
for name in sys.argv[1:]:
    ast.parse(Path(name).read_text())
PY
if [ "$check_only" = true ]; then
    echo "Live React station package validated for $station_id. No system changes made."
    exit 0
fi
if [ "$(id -u)" -ne 0 ]; then echo "Installation requires root; use --check for read-only preflight." >&2; exit 2; fi
for executable in nginx chromium python3 systemctl getent runuser; do
    command -v "$executable" >/dev/null || { echo "Install prerequisite: $executable" >&2; exit 1; }
done
python3 -c "import evdev, venv" || { echo "Install python3-evdev and python3-venv before provisioning." >&2; exit 1; }
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
kiosk_uid=$(id -u "$kiosk_user")
user_bus="/run/user/$kiosk_uid/bus"
user_systemctl() {
    runuser -u "$kiosk_user" -- env XDG_RUNTIME_DIR="/run/user/$kiosk_uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=$user_bus" systemctl --user "$@"
}
# Stop the previous NFC keyboard bridge before installing the shared direct reader.
# Leaving it alive would deliver both virtual keystrokes and direct NFC scans.
if [ -S "$user_bus" ]; then
    for unit in fabric-scanner.target fabric-scanner-nfc.service fabric-scanner-input.service; do
        if [ "$(user_systemctl show "$unit" -p LoadState --value)" != not-found ]; then
            user_systemctl stop "$unit"
        fi
    done
fi
rm -f "$kiosk_home/.config/systemd/user/fabric-scanner.target.wants/fabric-scanner-nfc.service" \
    "$kiosk_home/.config/systemd/user/fabric-scanner-nfc.service"
rm -f /opt/fabric-scanner/bin/nfc-bridge.py
# A dormant legacy environment is harmless; remove it only after stopping its service.
rm -rf /opt/fabric-scanner/nfc-env
install -d /opt/fabric-scanner/ui /opt/fabric-scanner/bin
cp -R "$ui_source/." /opt/fabric-scanner/ui/
install -m 0755 "$source_dir/browser.sh" "$source_dir/watchdog.py" "$source_dir/input-bridge.py" "$source_dir/nfc_tags.py" \
    "$source_dir/station-agent.py" /opt/fabric-scanner/bin/
install -m 0644 "$source_dir/startup.html" /opt/fabric-scanner/bin/startup.html
python3 - "$station_id" "$api_origin" <<'PY'
from pathlib import Path
import json
import sys
station, api_origin = sys.argv[1], sys.argv[2]
rooms = {"shared-door": ("shared", "Shared conditioning room"),
         "ppe-door": ("ppe", "PPE conditioning room"),
         "footwear-door": ("footwear", "Footwear conditioning room")}
room_id, room_name = rooms.get(station, (station, "Conditioning station " + station))
Path("/opt/fabric-scanner/ui/station-config.js").write_text(
    "window.fabricStationConfig = " + json.dumps({"stationId": station, "stationName": room_name + " door",
                                               "roomId": room_id, "roomName": room_name, "fixed": False, "inputBridge": True, "apiOrigin": api_origin,
                                               "targetWidth": 1280, "targetHeight": 720}) + ";\n")
Path("/opt/fabric-scanner/station.env").write_text(
    "KIOSK_URL=http://127.0.0.1:8080/station.html?station=" + station + "#kiosk\n"
    "STATION_ID=" + station + "\n")
PY
chmod -R a+rX /opt/fabric-scanner/ui
# API_FALLBACK=http://IP:PORT (optional): the Mac's local-network address, used only while the
# main address (API_ORIGIN, normally its Tailscale name) cannot be reached.
python3 "$source_dir/render-nginx.py" "$source_dir/nginx.conf.template" "$api_origin" "$dns_resolver" "${API_FALLBACK:-}" \
    > /etc/nginx/sites-available/fabric-scanner.new
mv /etc/nginx/sites-available/fabric-scanner.new /etc/nginx/sites-available/fabric-scanner
ln -sf /etc/nginx/sites-available/fabric-scanner /etc/nginx/sites-enabled/fabric-scanner
nginx -t
systemctl enable nginx
systemctl restart nginx
install -d -o "$kiosk_user" -g "$kiosk_group" \
    "$kiosk_home/.config/systemd/user" "$kiosk_home/.config/labwc"
for name in fabric-scanner.target fabric-scanner-browser.service fabric-scanner-watchdog.service fabric-scanner-input.service \
    fabric-scanner-agent.service; do
    install -m 0644 -o "$kiosk_user" -g "$kiosk_group" "$source_dir/$name" "$kiosk_home/.config/systemd/user/$name"
done
# apt's python3-evdev is available in this environment without building a wheel.
[ -x /opt/fabric-scanner/input-env/bin/python ] || python3 -m venv --system-site-packages /opt/fabric-scanner/input-env
/opt/fabric-scanner/input-env/bin/python -c "import evdev"
if getent group input >/dev/null; then usermod -aG input "$kiosk_user"; fi
printf 'ENABLE_NFC=%s\n' "$with_nfc" > /opt/fabric-scanner/input.env
# Keep operator-specific stable device path/name overrides on updates.
if [ ! -e /opt/fabric-scanner/scanner.env ]; then
    cat > /opt/fabric-scanner/scanner.env <<'SCANNER'
# Optional exact device selection. Use a stable /dev/input/by-id/...-event-kbd path.
# SCANNER_DEVICE=/dev/input/by-id/usb-EXAMPLE-event-kbd
# SCANNER_MATCH=eyoyo
SCANNER
    chmod 0644 /opt/fabric-scanner/scanner.env
fi
chmod 0644 /opt/fabric-scanner/input.env
if [ "$with_nfc" = true ]; then
    /opt/fabric-scanner/input-env/bin/pip install --quiet -r "$source_dir/nfc-requirements.txt"
    /opt/fabric-scanner/input-env/bin/python -c "import adafruit_pn532"
    for group in spi gpio; do
        if getent group "$group" >/dev/null; then usermod -aG "$group" "$kiosk_user"; fi
    done
fi
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
# The Touch Display 2 panel is 720 x 1280 portrait; the kiosk is laid out for 1280 x 720.
# Rotate every DSI output at login, before the kiosk starts. labwc rotates touch with it.
# DISPLAY_ROTATION=270 if the picture comes out upside down; DISPLAY_ROTATION=none to leave it.
rotation="${DISPLAY_ROTATION:-90}"
tmp_autostart="$(mktemp)"
awk '/^# fabric-scanner landscape/ {skip = 1} !skip {print} /^# end fabric-scanner landscape/ {skip = 0}' "$autostart" > "$tmp_autostart"
cat "$tmp_autostart" > "$autostart" && rm -f "$tmp_autostart"
case "$rotation" in
    90|180|270)
        tmp_autostart="$(mktemp)"
        cat > "$tmp_autostart" <<ROTATE
# fabric-scanner landscape
for output in \$(wlr-randr 2>/dev/null | awk '/^DSI-/ {print \$1}'); do wlr-randr --output "\$output" --transform $rotation; done
# end fabric-scanner landscape
ROTATE
        cat "$autostart" >> "$tmp_autostart" && cat "$tmp_autostart" > "$autostart" && rm -f "$tmp_autostart" ;;
    none) ;;
    *) echo "DISPLAY_ROTATION must be 90, 180, 270 or none" >&2; exit 2 ;;
esac
grep -q '^# fabric-scanner landscape' "$autostart" || [ "$rotation" = none ] || { echo "Display rotation was not written" >&2; exit 1; }
chown "$kiosk_user:$kiosk_group" "$autostart"
chmod 0755 "$autostart"
# raspi-config reads the account to autologin from SUDO_USER, so point it at the kiosk user
# rather than whoever ran sudo. For do_blanking and do_spi, nonint 1 means off and 0 means on.
if command -v raspi-config >/dev/null; then
    env SUDO_USER="$kiosk_user" raspi-config nonint do_boot_behaviour B4
    raspi-config nonint do_blanking 1
    if [ "$with_nfc" = true ]; then raspi-config nonint do_spi 0; fi
    echo "Installed. Desktop Autologin is set for $kiosk_user and screen blanking is off."
else
    echo "Installed. raspi-config was not found: enable Desktop Autologin for $kiosk_user, disable screen blanking"
    if [ "$with_nfc" = true ]; then echo "and enable SPI for the NFC hat,"; fi
    echo "then reboot."
fi
echo "The dedicated live page is http://127.0.0.1:8080/station.html?station=$station_id#kiosk."

# Reload the kiosk user's manager, not root's. New group membership needs a reboot.
if [ -S "$user_bus" ]; then
    user_systemctl daemon-reload
    if user_systemctl show-environment | grep -q '^WAYLAND_DISPLAY='; then
        user_systemctl restart fabric-scanner.target
        echo "The running graphical station was restarted with the updated units."
    fi
fi
echo "Reboot to apply input-group membership, autologin, and any SPI changes."
