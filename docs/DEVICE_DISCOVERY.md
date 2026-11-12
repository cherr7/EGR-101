# Device discovery and remote access

**Setup:** one Mac running the app, and three Pi kiosks (`name-a`, `name-b`, `name-c`), all on UL
guest Wi-Fi.

**Goals:**

- The Pis find the Mac **by name**, not by an IP written in at deploy time.
- Everything comes back on its own after restarts and power cuts.
- Staff can reach the app from their browsers, behind a login.

**Plan:**

- **Tailscale** joins the Mac and the Pis.
- **Cloudflare Tunnel** on our own domain, with Cloudflare Access as the login in front, serves the staff browsers.

**Status (Nov 12, 2026):**

- The repo side is done. `scripts/deploy-pi.sh` points each Pi at the Mac's Tailscale name. It refuses an IP-less `.local` name and checks the Pi is on the tailnet.
- Not done yet:
  - the on-site Wi-Fi test,
  - the conversation with UL IT,
  - creating the Tailscale tailnet, the Cloudflare account and the domain.

Those need the team's accounts and someone on the UL network.

## 1. Test the guest Wi-Fi first

Guest networks often use **client isolation**: devices reach the internet but not each other.
Today the Pis talk to the Mac directly over the LAN, so isolation would break them.

Run this on the Mac and one Pi, both on the guest Wi-Fi, at the same time:

```sh
bash scripts/wifi-peer-test.sh listen                         # on the Mac: prints its IP
bash scripts/wifi-peer-test.sh probe <Mac IP> <Mac host name> # on the Pi
```

How to read the result:

| ping / TCP between devices | Tailscale + Cloudflare lines | Meaning |
| --- | --- | --- |
| reachable | reachable | Devices can see each other. Tailscale connects them directly. |
| BLOCKED | reachable | Client isolation. Tailscale still works through its relays (DERP, HTTPS 443): slower but fine for scans. Cloudflare Tunnel is unaffected. |
| any | BLOCKED | Outbound traffic is filtered. Neither plan works without UL IT. Ask them (section 5). |

Record the result here:

- Date / location:
- Network name:
- Mac → Pi ping / TCP:
- Bonjour:
- Tailscale HTTPS:
- Cloudflare 7844:

## 2. How the Pis find the Mac (Tailscale)

1. Create one tailnet for the project. Turn on **MagicDNS** and set the Pis' and Mac's keys to
   **not expire** (Machines → ⋯ → Disable key expiry), so a device left alone for months stays in.
2. Mac: `brew install tailscale`, then `sudo brew services start tailscale` and
   `sudo tailscale up --hostname fabric-mac`. That runs `tailscaled` as a system LaunchDaemon. It
   starts at boot, before anyone logs in, like the app's own LaunchDaemon
   ([MAC_HOST.md](MAC_HOST.md)).
3. Each Pi:

   ```sh
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up --hostname name-a
   ```

   The service is enabled at boot.
4. Deploy from the Mac:

   ```sh
   scripts/deploy-pi.sh pi@name-a shared-door
   ```

   The script reads the Mac's name (`fabric-mac.<tailnet>.ts.net`) from `tailscale status`. It
   writes `http://fabric-mac.<tailnet>.ts.net:8120` into the Pi's nginx with MagicDNS
   (`100.100.100.100`) as its resolver. A deploy only reports success when the Pi has rebooted and
   reached the Mac through that name.

Why a name and not Bonjour (`.local`): nginx's `resolver` does plain DNS and can't resolve mDNS.
Bonjour also stops at the subnet edge and is often filtered on guest Wi-Fi.

### Recovering on their own

| Event | What happens |
| --- | --- |
| Mac restarts or loses power | The PostgreSQL + API LaunchDaemon and `tailscaled` both start at boot without login. The Pis show **Offline** meanwhile and return to **Online** by themselves; no scans are queued. |
| Mac gets a new IP (DHCP, new access point) | Its Tailscale address (`100.x.y.z`) and name don't change. nginx re-resolves the name every 10 s (`resolver ... valid=10s`). |
| Pi restarts or loses power | `tailscaled`, nginx and the kiosk services start at boot. The kiosk shows "Starting station…", then Offline until the Mac answers. |
| Wi-Fi drops for a while | Tailscale reconnects on its own. The kiosk's health check turns it back Online. A scan that was in flight is checked again with its same retry ID. |
| Tailscale relays only (isolation) | Each scan adds a relay round trip, roughly 20–80 ms. The target is 1 s from scan to confirmation. |

## 3. Staff browsers (Cloudflare Tunnel + Access)

1. Put the team's domain on Cloudflare, for example `example-lab.org` (a placeholder; use the
   real one). Free plan.
2. On the Mac:

   ```sh
   brew install cloudflared
   cloudflared tunnel login
   cloudflared tunnel create fabric
   cloudflared tunnel route dns fabric tracker.example-lab.org
   ```

   Config `~/.cloudflared/config.yml`:

   ```yaml
   tunnel: fabric
   credentials-file: /Users/<mac user>/.cloudflared/<tunnel id>.json
   ingress:
     - hostname: tracker.example-lab.org
       service: http://127.0.0.1:8120
     - service: http_status:404
   ```

   Then run `sudo cloudflared service install`, so it starts at boot like the app.
3. In Cloudflare Zero Trust → Access → Applications, add a self-hosted app for
   `tracker.example-lab.org`. Use the policy "Allow: emails in <staff list>" with one-time PIN login,
   or the team's identity provider. Nobody reaches the app without passing Access.
4. Set `COOKIE_SECURE=true` for the API: the tunnel serves HTTPS, so the session cookie should be
   HTTPS-only.

The tunnel only makes outbound connections from the Mac to Cloudflare, on port 7844. Nothing on
the UL network is opened inbound. The kiosks keep using Tailscale, not the public hostname, so a
Cloudflare outage never stops scanning.

## 4. What stays the same

- The Pis still serve their own copy of the kiosk UI and only proxy `/api` to the Mac.
- No offline scanning: if the Mac can't be reached, the kiosk says Offline and records nothing.
- PostgreSQL still listens only on `127.0.0.1` on the Mac.

## 5. Clearing it with UL IT (draft, not sent)

> **Subject:** EGR 101 conditioning tracker: devices on the guest Wi-Fi
>
> Hello,
>
> We are a Duke EGR 101 student team building a specimen tracker for the conditioning rooms. It
> runs on one MacBook and three Raspberry Pi touch-screen scanners at the room doors, all on the
> guest Wi-Fi. Before we install it we would like to check it is acceptable:
>
> 1. **Tailscale (WireGuard VPN).** The four devices connect to each other over Tailscale. It uses
>    outbound HTTPS (443) and UDP 41641. Nothing inbound is opened. If the guest Wi-Fi isolates
>    clients, Tailscale relays the traffic over HTTPS instead.
> 2. **Cloudflare Tunnel.** The MacBook makes an outbound connection on port 7844 to Cloudflare, so
>    lab staff can open the app at our own web address. A Cloudflare Access login sits in front.
>    Nothing inbound is opened on UL's network.
> 3. **Data.** Specimen numbers, project numbers and scan times only. No personal data, nothing
>    from UL systems, and no connection to UL servers.
>
> Is this allowed on the guest network? If not, is there a network or VLAN we should use instead,
> or ports we should avoid? We are happy to adjust or meet.
>
> Thank you,
> [names], Duke EGR 101 team

Send it only once the team has agreed the wording and who it goes to.
