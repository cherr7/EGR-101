# Roadmap: one Mac app that runs and manages everything

**Status:** plan, revised Nov 12, 2026. Nothing below is built yet unless it says so.

## Where things stand

| Piece | Today |
| --- | --- |
| Mac (MacBook Neo) | Runs `main` on a fresh database. The app is started by hand from `output/macOS/Fabric Conditioning.app` (a small launcher, not in `/Applications`). **It doesn't start on its own after a restart.** |
| Pis A, B, C | On Tailscale (`ulscanner-a/b/c`) and reach the Mac by its Tailscale name. **The scanner kiosk isn't installed yet.** |
| Printer | USB is the default. A Wi-Fi printer is also set up on the Mac as a fallback. |
| Updates | By hand: pull, build and restart over SSH. Today's outage (a migration the old database couldn't take) shows why that's risky. |
| Outside access | None. Only DukeOpen and Tailscale. |

## The target

**The Neo runs one real app, `/Applications/Fabric Conditioning.app`, and it manages everything else:**

- **It starts on its own.** The app runs the database and server as a background service at boot, before anyone logs in, and restarts them if they crash.
- **It manages the Pis over Tailscale.** It installs, updates and checks them, with nobody SSHing in from another computer.
- **It updates itself and the Pis** from tagged releases, with a backup first and automatic rollback.
- **It shows everything in one place:** a menu-bar status (server, database, Pis, printer, outside link) and System health in the web app.

Staff never install anything. They open a web address, on site or through the outside link.

## 1. The Mac app in /Applications

- **What it is:** a proper app bundle with the server code, the Python runtime and the built web app inside it. The database stays outside it, in `~/Library/Application Support/Fabric Conditioning`, so replacing the app never touches data.
- **Starting at boot:** the app registers its background service through macOS's own service API (`SMAppService`).
  - The first time, macOS asks once for approval in **System Settings → General → Login Items**, with no Terminal and no `sudo`.
  - After that, the server starts at boot without a login, and `launchd` restarts it if it crashes.
  - This replaces the never-installed `install-daemon.sh` path.
- **Menu-bar icon:** status (green, amber or red), "Open staff app", the outside link with a Copy button, the Pis' status, and "Check for updates".
- **Building and installing:** `deploy/macos/build_app.py` grows into this bundle. Installing is dragging it into `/Applications` and opening it once.
- **Signing:** a free local build works for the Neo, with a right-click → Open the first time. Signing and notarizing with an Apple Developer account ($99/year) is only needed if it's handed to other people.

## 2. The Pis, managed from the Mac over Tailscale

- **Installing:** done once per Pi, from the Mac, by Tailscale name: `scripts/deploy-pi.sh admin@ulscanner-a shared-door`.
  - The script finds the Mac's Tailscale name with the CLI inside `Tailscale.app` when `tailscale` isn't on the PATH.
  - It needs the Pi password once.
- **Remote access without passwords afterwards:**
  - The install turns on **Tailscale SSH** on the Pi (`tailscale set --ssh`), so the Mac reaches each Pi through Tailscale with no SSH keys to copy around.
  - It adds **one narrow sudo rule**: the kiosk user may run only `/opt/fabric-scanner/bin/apply-update` without a password. It does **not** give passwordless sudo for everything.
- **Which address the Pis use:**
  - Each Pi's nginx tries the Mac's **Tailscale name first** and its **local IP second**.
  - It switches back automatically when the first one returns.
  - At UL, only the second address changes, by a redeploy.
- **Updates (pull, not push):**
  - The station agent already reports every 30 s. It also compares its version with the Mac's.
  - When they differ, it downloads the new kiosk package from the Mac, checks its SHA-256, and unpacks it next to the running copy.
  - It waits until the kiosk is idle: no pending scan, undo or print, and no input for 2 minutes.
  - Then it switches over and restarts the browser, with no reboot.
  - If the page doesn't come up within 60 s, it switches back and reports the failure.
- **Seen on the Mac:** System health and the menu bar show each Pi's version, its last report and whether it's on Tailscale. A Pi on an old version is flagged.
- **Changing the password:** `admin`/`admin` gets changed on all three Pis at install time.

## 3. Updating the Mac itself

`scripts/update-host.sh`, later the app's **Check for updates** button:

1. **Fetch the release.** Only tags on `main` are accepted, such as `v2026.11.12-1`; a branch is refused.
2. **Back up** to `~/fabric-backups/before-<tag>.zip`.
3. **Build the new version in its own folder** while the current version keeps running.
4. **Check the migration first.** If the new code has no upgrade path from the database's current revision, stop before touching anything and say so. That's the Nov 12 failure.
5. **Switch:** stop, swap to the new version, start.
6. **Check health:** wait for `/api/health` to report `ok`. If it doesn't, swap back, start the old version, and report "update failed, rolled back" on System health.
7. **Update the Pis:** the Pis then see the new version on their own (section 2).

A daily check only *notifies* that a new release exists. Installing is always a person clicking Update.

## 4. Outside access (Cloudflare quick tunnel)

- **The tunnel:** `cloudflared tunnel --url http://localhost:8120`. No account or domain is needed. It gives a random `https://….trycloudflare.com` link.
- **Keeping it alive:** a background service the app manages restarts the tunnel if it dies. Each restart means a new link, which the app shows on System health and in the menu bar, with Copy. Bookmarks aren't needed.
- **Passcode for outside access only:**
  - Requests that come through Cloudflare carry Cloudflare's headers (`Cf-Ray`, `Cf-Connecting-Ip`), and local requests never do.
  - The server asks for the passcode only when those headers are present. On DukeOpen, on the Neo and on the Pis, nothing changes.
  - A local user who fakes the header just gets asked for the passcode, so it can't be used to skip it.
  - The passcode is set on the Neo, stored hashed, and kept as a cookie for 12 hours.
- **Limits:** quick tunnels have no uptime guarantee and cap simultaneous requests, which is fine for a few staff. A named tunnel with Cloudflare Access is the upgrade if this becomes permanent; that needs a domain.

## 5. Printer

- **USB is the default.** Done: it's set with "Default for everyone".
- **Automatic fallback:** if the USB printer reports offline and the Wi-Fi printer is idle, the Print panel picks Wi-Fi for that job and says so. The saved default stays USB.
- **Wi-Fi address changes:** the Mac looks for the printer's hardware address on the local network and updates its Wi-Fi printer entry. A reserved address from OIT, and from UL IT later, makes this unnecessary.

## Build order

1. **Pi install from the Mac over Tailscale,** with the Tailscale-first and local-IP fallback, Tailscale SSH and the narrow sudo rule. Then install all three kiosks and test them.
2. **The Mac app in `/Applications`,** with the background service at boot, the menu bar, and Login Items approval.
3. **Version stamps and `/api/version`,** with each device's version on System health.
4. **`update-host.sh`,** with backup, migration check and rollback, then the app's Update button.
5. **Pi pull updates,** with idle detection and rollback.
6. **The quick tunnel, its keep-alive service and the outside-only passcode.**
7. **Printer fallback and finding the Wi-Fi printer automatically.**

## Before moving to UL

- **Ask UL IT:**
  - Does their network allow Tailscale and Cloudflare tunnels?
  - Will they reserve addresses for the Mac and the printer?
  - Can the printer join their Wi-Fi?
- **Tailscale's free Personal plan** (3 users, 100 devices) is meant for personal use; use a paid business plan for UL.
- **A full hands-on test on site:** scanning, undo, printing, screen rotation, and unplugging the Mac to watch the Pis recover.

## Not included

- Updating macOS or Raspberry Pi OS. Leave that to the OS's own updates, applied by hand on a quiet day.
- Automatic updates without a person clicking Update.
