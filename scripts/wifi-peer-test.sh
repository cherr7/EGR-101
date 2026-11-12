#!/bin/bash
# Does this Wi-Fi let our devices see each other, and can they reach Tailscale and Cloudflare?
# Run it on two devices on the UL guest Wi-Fi (the Mac and one Pi), at the same time:
#
#   device A:  bash scripts/wifi-peer-test.sh listen
#   device B:  bash scripts/wifi-peer-test.sh probe <A's IP address>
#
# "listen" prints A's addresses and answers on TCP 8799 for 5 minutes. "probe" tries to reach A
# directly (ping, TCP, Bonjour), then checks the outbound HTTPS paths Tailscale and Cloudflare use.
# Nothing is installed or changed. Paste the PROBE SUMMARY into docs/DEVICE_DISCOVERY.md.
set -u
PORT=8799
mode="${1:-}"
os="$(uname -s)"

addresses() {
    if [ "$os" = Darwin ]; then ipconfig getifaddr en0 2>/dev/null; ipconfig getifaddr en1 2>/dev/null
    else hostname -I 2>/dev/null | tr ' ' '\n'; fi
}

case "$mode" in
listen)
    echo "Host name: $(hostname)"
    echo "Addresses (give one to the probing device):"; addresses | sed 's/^/  /'
    echo "Answering on TCP $PORT for 5 minutes (Ctrl-C to stop)..."
    python3 - "$PORT" <<'PY'
import socket, sys, time
server = socket.socket(); server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", int(sys.argv[1]))); server.listen(); server.settimeout(1)
end = time.time() + 300
while time.time() < end:
    try:
        connection, peer = server.accept()
    except socket.timeout:
        continue
    print("  connection from", peer[0], flush=True)
    connection.sendall(b"fabric-peer-test ok\n"); connection.close()
PY
    ;;
probe)
    target="${2:?usage: wifi-peer-test.sh probe <IP of the listening device> [its host name]}"
    name="${3:-}"
    result() { printf '  %-44s %s\n' "$1" "$2"; }
    echo "PROBE SUMMARY ($(hostname), $(date '+%Y-%m-%d %H:%M'))"
    if ping -c 3 -W 2 "$target" >/dev/null 2>&1 || ping -c 3 -t 6 "$target" >/dev/null 2>&1; then
        result "ping $target" "reachable"; else result "ping $target" "BLOCKED (client isolation likely)"; fi
    reply="$(python3 -c "import socket,sys; s=socket.create_connection((sys.argv[1], $PORT), 4); print(s.recv(64).decode().strip())" "$target" 2>/dev/null)"
    if [ "$reply" = "fabric-peer-test ok" ]; then result "TCP $target:$PORT" "reachable"; else result "TCP $target:$PORT" "BLOCKED"; fi
    if [ -n "$name" ]; then
        if [ "$os" = Darwin ]; then found="$(dscacheutil -q host -a name "$name.local" 2>/dev/null | awk '/ip_address/ {print $2; exit}')"
        else found="$(getent hosts "$name.local" 2>/dev/null | awk '{print $1; exit}')"; fi
        result "Bonjour $name.local" "${found:-not found (mDNS blocked or not running)}"
    fi
    for url in https://controlplane.tailscale.com/key?v=71 https://login.tailscale.com https://derp1.tailscale.com/derp/probe \
               https://www.cloudflare.com/cdn-cgi/trace; do
        code="$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$url")"
        if [ "$code" != 000 ]; then result "HTTPS ${url#https://}" "reachable ($code)"; else result "HTTPS ${url#https://}" "BLOCKED"; fi
    done
    # cloudflared reaches Cloudflare's edge on port 7844 (QUIC/UDP first, then TCP), not 443.
    if python3 -c "import socket; socket.create_connection(('region1.v2.argotunnel.com', 7844), 6)" 2>/dev/null; then
        result "TCP region1.v2.argotunnel.com:7844" "reachable (Cloudflare Tunnel)"
    else result "TCP region1.v2.argotunnel.com:7844" "BLOCKED (cloudflared needs port 7844 out)"; fi
    if command -v tailscale >/dev/null && tailscale status >/dev/null 2>&1; then
        echo "  Tailscale is up here. 'tailscale ping <other device>' shows 'via DERP' (relayed) or a direct address."
    fi
    echo
    echo "Reading it: if ping/TCP are BLOCKED but the HTTPS lines are reachable, the guest Wi-Fi isolates"
    echo "clients. Tailscale still connects through its relays (slower, still fine for this app), and"
    echo "Cloudflare Tunnel is unaffected. If the HTTPS lines are BLOCKED, ask UL IT before going further."
    ;;
*)
    sed -n '2,10p' "$0"; exit 2 ;;
esac
