#!/bin/bash
# Build the station package on this Mac and install it on a Pi over SSH.
# Usage: bash scripts/deploy-pi.sh [user@pi-ip] [station-id]
set -euo pipefail
PI="${1:-test@10.10.97.75}"
STATION="${2:-shared-door}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MAC_IP="$(ipconfig getifaddr en0 || ipconfig getifaddr en1 || true)"
[ -n "$MAC_IP" ] || { echo "Could not find this Mac's LAN IP" >&2; exit 1; }
API="http://$MAC_IP:8120"
echo "==> Mac API will be $API ; station $STATION ; target $PI"

echo "==> Building frontend + station package"
(cd frontend && { [ -d node_modules/vite ] || npm ci; } && npm run build)
python3 scripts/build-station.py --station "$STATION"

# One SSH connection, so the password is asked only once.
SOCK="$(mktemp -u /tmp/pi-ssh.XXXXXX)"
SSH=(ssh -o ControlMaster=auto -o ControlPath="$SOCK" -o ControlPersist=10m -o StrictHostKeyChecking=accept-new)
trap '"${SSH[@]}" -O exit "$PI" 2>/dev/null || true' EXIT
ssh-keygen -R "${PI#*@}" >/dev/null 2>&1 || true
"${SSH[@]}" "$PI" true

echo "==> Copying files"
"${SSH[@]}" "$PI" 'rm -rf ~/station-ui ~/scanner-kiosk'
tar -C output -cf - station-ui | "${SSH[@]}" "$PI" 'tar -C ~ -xf -'
tar -C deploy -cf - scanner-kiosk | "${SSH[@]}" "$PI" 'tar -C ~ -xf -'

echo "==> Installing on the Pi (prereqs, NFC, services)"
"${SSH[@]}" -t "$PI" "set -e
sudo apt-get update
sudo apt-get install -y nginx chromium python3 python3-venv python3-evdev ca-certificates
DNS=\$(awk '/^nameserver/ && \$2 !~ /^127\\./ && \$2 ~ /^[0-9.]+\$/ {print \$2; exit}' /etc/resolv.conf)
[ -n \"\$DNS\" ] || DNS=\$(ip route | awk '/default/ {print \$3; exit}')
echo \"Pi DNS resolver: \$DNS\"
sudo DISPLAY_ROTATION=${DISPLAY_ROTATION:-90} sh ~/scanner-kiosk/install.sh --nfc ~/station-ui $API \$(whoami) \$DNS $STATION
echo KIOSK_DIRECT_START=true | sudo tee -a /opt/fabric-scanner/station.env
echo '==> Checking the Pi can reach the Mac API'
curl -fsS -m 5 $API/api/health || echo 'WARNING: Pi cannot reach $API yet (is the app running / firewall open?)'
echo '==> Rebooting'
sudo reboot" || true
echo "==> Done. The Pi is rebooting into the kiosk."
