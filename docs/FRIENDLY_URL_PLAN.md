# Plan: an easy, text-based URL for UL staff

Status: **proposed, not started** (saved 2026-11-10). Today the staff app is reached at `http://<host IP>:8120`. The goal is something like `http://conditioning/` or `http://ul-conditioning.local`.

## Phase 1: on the host Mac, no IT needed (~15 min)

1. **Rename the Mac's local hostname** from `ULs-MacBook-Neo` to e.g. `ul-conditioning` (System Settings → General → Sharing → Local hostname, or `sudo scutil --set LocalHostName ul-conditioning`). Same-network devices then reach it at `ul-conditioning.local`.
2. **Serve on port 80 instead of 8120** so no port is needed in the URL. Verified 2026-11-10: macOS lets a non-root process bind port 80 on this Mac. The port is the `http_port` value in `<state dir>/config.json`, which `deploy/macos/host.py` reads. The sidebar's host address follows the actual port.
3. Result: **`http://ul-conditioning.local`**

Limits of `.local` (mDNS/Bonjour): it usually works only on the same network segment, corporate Wi-Fi often blocks it, and the Pis' nginx can't resolve it (nginx uses real DNS only). It's good for testing and nearby staff, but it's not the permanent answer.

## Phase 2: the real fix (one ticket to UL IT)

Ask UL IT for:
1. **A DHCP reservation** for the host Mac's Wi-Fi hardware (MAC) address (or the Ethernet adapter's, if it's docked), so its IP never changes.
2. **An internal DNS A record** pointing a name at that reserved IP, e.g. `conditioning.<site>.<ul-internal-domain>`. Ideally also a short alias with the DNS search domain configured, so plain **`http://conditioning/`** works from UL PCs.

Install the Pi stations against that name: `deploy/scanner-kiosk/install.sh` takes `API_ORIGIN` (e.g. `http://conditioning.<site>.<domain>`) and `LAN_DNS_IPV4` (UL's internal DNS server).

## Phase 3: discoverability

- IT pushes a browser bookmark or desktop shortcut to the lab PCs.
- Print QR codes for the URL at each room door and the prep bench. An optional "Share link" QR code in the sidebar could be added.
- Once DNS exists, switch the sidebar's "This host" address to show the friendly name.

## Not recommended

- **No public URL** (Cloudflare Tunnel, ngrok, bit.ly pointing at an internal IP). The app has **no login** and holds audit-relevant records, so anyone with the link could edit or undo transactions. The README also states it's LAN-only by design.
- If remote access is needed later, use UL's VPN, or a private network like Tailscale with IT approval, **and** add authentication to the app first.

## Open decisions

1. Final name: `ul-conditioning`, `conditioning`, `fabric-tracker`, …
2. Which network the host lives on long-term (UL's network vs. Duke's during development). DNS work only makes sense on the final network.
3. Moving 8120 → 80: any Pi that's already installed must be reinstalled against the new origin. None are installed yet as of 2026-11-10.
