#!/bin/sh
# Usage: sudo install.sh [--nfc] LIVE_UI_PACKAGE API_ORIGIN DESKTOP_USER LAN_DNS_IPV4 [STATION_ID]
# Add --check first for a read-only preflight, including on the development Mac.
# --nfc also installs the optional PN532 NFC bridge (see README).
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
if [ "$with_nfc" = true ]; then
    for name in nfc-bridge.py nfc-requirements.txt fabric-scanner-nfc.service; do
        [ -f "$source_dir/$name" ] || { echo "Missing NFC bridge file: $source_dir/$name" >&2; exit 1; }
    done
    python3 -m py_compile "$source_dir/nfc-bridge.py"
fi
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
# The NFC bridge joins fabric-scanner.target only when installed with --nfc; rerunning without it detaches it.
nfc_wants="$kiosk_home/.config/systemd/user/fabric-scanner.target.wants"
rm -f "$nfc_wants/fabric-scanner-nfc.service"
if [ "$with_nfc" = true ]; then
    install -m 0755 "$source_dir/nfc-bridge.py" /opt/fabric-scanner/bin/
    # System site packages let apt's python3-evdev satisfy the evdev requirement without a compiler.
    [ -x /opt/fabric-scanner/nfc-env/bin/python ] || python3 -m venv --system-site-packages /opt/fabric-scanner/nfc-env
    /opt/fabric-scanner/nfc-env/bin/pip install --quiet -r "$source_dir/nfc-requirements.txt"
    /opt/fabric-scanner/nfc-env/bin/python -c "import evdev, adafruit_pn532"
    # Typed IDs go through a virtual keyboard (/dev/uinput); the PN532 hat uses SPI and GPIO.
    echo uinput > /etc/modules-load.d/fabric-scanner-uinput.conf
    echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
        > /etc/udev/rules.d/60-fabric-scanner-uinput.rules
    modprobe uinput
    udevadm control --reload-rules
    udevadm trigger --subsystem-match=misc --sysname-match=uinput
    for group in input spi gpio; do
        if getent group "$group" >/dev/null; then usermod -aG "$group" "$kiosk_user"; fi
    done
    install -m 0644 -o "$kiosk_user" -g "$kiosk_group" "$source_dir/fabric-scanner-nfc.service" \
        "$kiosk_home/.config/systemd/user/fabric-scanner-nfc.service"
    install -d -o "$kiosk_user" -g "$kiosk_group" "$nfc_wants"
    ln -sf ../fabric-scanner-nfc.service "$nfc_wants/fabric-scanner-nfc.service"
    chown -h "$kiosk_user:$kiosk_group" "$nfc_wants/fabric-scanner-nfc.service"
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
