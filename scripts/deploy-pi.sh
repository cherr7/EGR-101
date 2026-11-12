#!/bin/bash
# Build the station package on this Mac and install it on one Pi over SSH.
#
#   scripts/deploy-pi.sh USER@PI-HOST [STATION-ID]
#
# The Pi reaches the Mac by name, never by an IP baked in here:
#   MAC_HOST  the Mac's name as the Pi resolves it. Default: this Mac's Tailscale name
#             (e.g. fabric-mac.tail1234.ts.net), read from `tailscale status`.
#   DNS       the resolver the Pi's nginx asks for MAC_HOST. Default for a *.ts.net name:
#             100.100.100.100 (Tailscale MagicDNS). nginx cannot resolve Bonjour .local names.
#   MAC_LAN   the Mac's local-network address, used by the Pi only while MAC_HOST cannot be
#             reached. Default: this Mac's Wi-Fi/Ethernet IPv4 address. MAC_LAN=none turns it off.
#             It changes when the Mac gets a new address; redeploy then (or reserve the address).
#   DISPLAY_ROTATION  how the installer turns the Touch Display 2 (a portrait panel): 90 (default),
#             270 if the picture is upside down, or none.
#
# SSH checks the Pi's host key. A Pi seen before must present the same key; a new Pi's key
# is recorded on first contact. If a Pi was re-imaged, remove its old key yourself:
#   ssh-keygen -R PI-HOST
#
# Exit status is 0 only if the install succeeded AND the Pi came back after reboot with its
# kiosk page served and the Mac API reachable through it.
set -euo pipefail
die() { echo "ERROR: $*" >&2; exit 1; }

PI="${1:-}"
[ -n "$PI" ] || die "usage: scripts/deploy-pi.sh USER@PI-HOST [STATION-ID]"
STATION="${2:-shared-door}"
case "$STATION" in shared-door|ppe-door|footwear-door) ;; *) die "station must be shared-door, ppe-door or footwear-door" ;; esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAILSCALE="$(command -v tailscale || true)"
[ -n "$TAILSCALE" ] || [ ! -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ] || TAILSCALE=/Applications/Tailscale.app/Contents/MacOS/Tailscale
if [ -z "${MAC_HOST:-}" ] && [ -n "$TAILSCALE" ]; then
    MAC_HOST="$("$TAILSCALE" status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
fi
[ -n "${MAC_HOST:-}" ] || die "Set MAC_HOST to the Mac's name the Pis can resolve, e.g. MAC_HOST=fabric-mac.tail1234.ts.net (start Tailscale on this Mac first)."
case "$MAC_HOST" in
    *.local) die "MAC_HOST=$MAC_HOST is a Bonjour name; the Pi's nginx cannot resolve it. Use the Mac's Tailscale name." ;;
    *.ts.net) DNS="${DNS:-100.100.100.100}" ;;
esac
[ -n "${DNS:-}" ] || die "Set DNS to the IPv4 resolver that knows $MAC_HOST."
API="http://$MAC_HOST:8120"
if [ -z "${MAC_LAN:-}" ]; then
    for interface in en0 en1 en2 en3 en4 en5 en6 en7 en8; do
        MAC_LAN="$(ipconfig getifaddr "$interface" 2>/dev/null || true)"
        [ -n "$MAC_LAN" ] && break
    done
fi
FALLBACK=""
[ -z "${MAC_LAN:-}" ] || [ "$MAC_LAN" = none ] || FALLBACK="http://$MAC_LAN:8120"
echo "==> Station $STATION on $PI; Mac API $API (resolved by $DNS)${FALLBACK:+, local fallback $FALLBACK}"

echo "==> Building frontend + station package"
(cd frontend && { [ -d node_modules/vite ] || npm ci; } && npm run build)
python3 scripts/build-station.py --station "$STATION"
API_FALLBACK="$FALLBACK" sh deploy/scanner-kiosk/install.sh --check output/station-ui "$API" kiosk "$DNS" "$STATION" >/dev/null

# One SSH connection, so the password is asked only once.
SOCK="$(mktemp -u /tmp/pi-ssh.XXXXXX)"
SSH=(ssh -o ControlMaster=auto -o ControlPath="$SOCK" -o ControlPersist=10m
     -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
trap '"${SSH[@]}" -O exit "$PI" 2>/dev/null || true' EXIT
"${SSH[@]}" "$PI" true || die "Cannot SSH to $PI. If its host key changed after a re-image, run: ssh-keygen -R ${PI#*@}"

# Let this Mac reach the Pi with its SSH key from now on, so later updates need no login password.
KEY="$(ls ~/.ssh/id_ed25519.pub ~/.ssh/id_ecdsa.pub ~/.ssh/id_rsa.pub 2>/dev/null | head -1 || true)"
if [ -n "$KEY" ]; then
    echo "==> Authorizing this Mac's SSH key ($KEY) on the Pi"
    "${SSH[@]}" "$PI" 'umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; key=$(cat); grep -qxF "$key" ~/.ssh/authorized_keys || echo "$key" >> ~/.ssh/authorized_keys' < "$KEY"
fi

echo "==> Copying files"
"${SSH[@]}" "$PI" 'rm -rf ~/station-ui ~/scanner-kiosk'
tar -C output -cf - station-ui | "${SSH[@]}" "$PI" 'tar -C ~ -xf -'
tar -C deploy -cf - scanner-kiosk | "${SSH[@]}" "$PI" 'tar -C ~ -xf -'

echo "==> Installing on the Pi"
"${SSH[@]}" -t "$PI" "set -eu
sudo apt-get update
sudo apt-get install -y nginx chromium python3 python3-venv python3-evdev ca-certificates
if [ '$DNS' = 100.100.100.100 ] && ! tailscale status >/dev/null 2>&1; then
    echo 'This Pi is not on the tailnet. Install and log in to Tailscale first (docs/DEVICE_DISCOVERY.md).' >&2
    exit 3
fi
sudo DISPLAY_ROTATION='${DISPLAY_ROTATION:-90}' API_FALLBACK='$FALLBACK' sh ~/scanner-kiosk/install.sh --nfc ~/station-ui '$API' \"\$(whoami)\" '$DNS' '$STATION'
grep -qx KIOSK_DIRECT_START=true /opt/fabric-scanner/station.env || echo KIOSK_DIRECT_START=true | sudo tee -a /opt/fabric-scanner/station.env >/dev/null
curl -fsS -m 5 '$API/api/health' >/dev/null || { echo 'The Pi cannot reach $API. Is the app running and the Mac on the tailnet?' >&2; exit 4; }
" || die "Install on $PI failed (status $?). The Pi was not rebooted."

echo "==> Rebooting $PI"
"${SSH[@]}" "$PI" 'sudo systemctl reboot' || true    # the connection drops as the Pi goes down
"${SSH[@]}" -O exit "$PI" 2>/dev/null || true
HOST="${PI#*@}"
for attempt in $(seq 1 36); do   # wait up to 3 minutes for SSH to answer again
    sleep 5
    nc -z -w 3 "$HOST" 22 2>/dev/null && break
done
# Then give the kiosk services up to 2 more minutes, checked from the Pi itself.
if "${SSH[@]}" "$PI" 'for i in $(seq 1 24); do
        curl -fsS -m 5 http://127.0.0.1:8080/station/ready >/dev/null && curl -fsS -m 5 -D - -o /dev/null http://127.0.0.1:8080/api/health | grep -i x-fabric-upstream && exit 0
        sleep 5
    done; exit 1'; then
    echo
    echo "==> Done. $PI is back, serving the kiosk, and reaching the Mac API through it."
    exit 0
fi
die "$PI did not come back healthy. On the Pi: journalctl --user -u fabric-scanner-browser.service; curl http://127.0.0.1:8080/api/health"
