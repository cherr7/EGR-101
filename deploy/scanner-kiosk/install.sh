#!/bin/sh
# Usage: sudo install.sh [--nfc] LIVE_UI_PACKAGE API_ORIGIN DESKTOP_USER LAN_DNS_IPV4 [STATION_ID]
# Add --check first for a read-only preflight, including on the development Mac.
# --nfc also streams NFC tag taps from a PN532 hat through the input bridge (see README).
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
for name in input-bridge.py nfc_tags.py nfc-requirements.txt fabric-scanner-input.service; do
    [ -f "$source_dir/$name" ] || { echo "Missing input bridge file: $source_dir/$name" >&2; exit 1; }
done
python3 -c 'import ast, sys; [ast.parse(open(path).read(), path) for path in sys.argv[1:]]' \
    "$source_dir/input-bridge.py" "$source_dir/nfc_tags.py"
if [ "$check_only" = true ]; then
    echo "Live React station package validated for $station_id. No system changes made."
    exit 0
fi
if [ "$(id -u)" -ne 0 ]; then echo "Installation requires root; use --check for read-only preflight." >&2; exit 2; fi
for executable in nginx chromium python3 systemctl getent; do
    command -v "$executable" >/dev/null || { echo "Install prerequisite: $executable" >&2; exit 1; }
done
python3 -c "import evdev, ensurepip" 2>/dev/null || { echo "Install prerequisites: python3-evdev python3-venv" >&2; exit 1; }
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
install -m 0755 "$source_dir/browser.sh" "$source_dir/watchdog.py" "$source_dir/input-bridge.py" /opt/fabric-scanner/bin/
install -m 0644 "$source_dir/nfc_tags.py" /opt/fabric-scanner/bin/
python3 - "$station_id" "$with_nfc" <<'PY'
from pathlib import Path
import json
import sys
station, with_nfc = sys.argv[1], sys.argv[2] == "true"
rooms = {"shared-door": ("shared", "Shared conditioning room"),
         "ppe-door": ("ppe", "PPE conditioning room"),
         "footwear-door": ("footwear", "Footwear conditioning room")}
room_id, room_name = rooms.get(station, (station, "Conditioning station " + station))
Path("/opt/fabric-scanner/ui/station-config.js").write_text(
    "window.fabricStationConfig = " + json.dumps({"stationId": station, "stationName": room_name + " door",
                                               "roomId": room_id, "roomName": room_name, "fixed": True,
                                               "targetWidth": 1280, "targetHeight": 720,
                                               "inputBridge": True}) + ";\n")
Path("/opt/fabric-scanner/station.env").write_text(
    "KIOSK_URL=http://127.0.0.1:8080/station.html?station=" + station + "#kiosk\n")
Path("/opt/fabric-scanner/input.env").write_text("NFC=" + ("1" if with_nfc else "0") + "\n")
# scanner.env is the operator's: written once, never overwritten by a reinstall.
scanner_env = Path("/opt/fabric-scanner/scanner.env")
if not scanner_env.exists():
    scanner_env.write_text("# Pin the scanner by its stable path if auto-detection picks the wrong device:\n"
                           "# SCANNER_DEVICE=/dev/input/by-id/usb-...-event-kbd\n"
                           "# Or change the device-name fragments auto-detection looks for:\n"
                           "# SCANNER_MATCH=barcode,scanner,hidkeyboard,eyoyo\n")
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
for name in fabric-scanner.target fabric-scanner-browser.service fabric-scanner-watchdog.service fabric-scanner-input.service; do
    install -m 0644 -o "$kiosk_user" -g "$kiosk_group" "$source_dir/$name" "$kiosk_home/.config/systemd/user/$name"
done
# Remove the earlier keystroke-typing NFC bridge; NFC now goes through the input bridge.
rm -f "$kiosk_home/.config/systemd/user/fabric-scanner.target.wants/fabric-scanner-nfc.service" \
    "$kiosk_home/.config/systemd/user/fabric-scanner-nfc.service" /opt/fabric-scanner/bin/nfc-bridge.py \
    /etc/modules-load.d/fabric-scanner-uinput.conf /etc/udev/rules.d/60-fabric-scanner-uinput.rules
rm -rf /opt/fabric-scanner/nfc-env
# System site packages let apt's python3-evdev serve the bridge without a compiler or network.
[ -x /opt/fabric-scanner/input-env/bin/python ] || python3 -m venv --system-site-packages /opt/fabric-scanner/input-env
if [ "$with_nfc" = true ]; then
    /opt/fabric-scanner/input-env/bin/pip install --quiet -r "$source_dir/nfc-requirements.txt"
    /opt/fabric-scanner/input-env/bin/python -c "import adafruit_pn532"
fi
/opt/fabric-scanner/input-env/bin/python -c "import evdev"
# Reading /dev/input needs the input group; the PN532 hat also needs spi and gpio.
groups=input
if [ "$with_nfc" = true ]; then groups="input spi gpio"; fi
for group in $groups; do
    if getent group "$group" >/dev/null; then usermod -aG "$group" "$kiosk_user"; fi
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
# raspi-config reads the account to autologin from SUDO_USER, so point it at the kiosk user
# rather than whoever ran sudo. For do_blanking and do_spi, nonint 1 means off and 0 means on.
if command -v raspi-config >/dev/null; then
    env SUDO_USER="$kiosk_user" raspi-config nonint do_boot_behaviour B4
    raspi-config nonint do_blanking 1
    if [ "$with_nfc" = true ]; then raspi-config nonint do_spi 0; fi
    echo "Installed. Desktop Autologin is set for $kiosk_user and screen blanking is off; reboot to start the kiosk."
else
    echo "Installed. raspi-config was not found: enable Desktop Autologin for $kiosk_user, disable screen blanking"
    if [ "$with_nfc" = true ]; then echo "and enable SPI for the NFC hat,"; fi
    echo "then reboot."
fi
echo "The dedicated live page is http://127.0.0.1:8080/station.html?station=$station_id#kiosk."
