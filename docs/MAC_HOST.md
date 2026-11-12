# Mac host

Short version: [QUICKSTART.md](QUICKSTART.md#1-mac-first-time-setup). This page has the details.

The MacBook Neo hosts PostgreSQL, the HTTP API, the staff web app, and print jobs. The three Pi kiosks and staff browsers use this same host. Each Pi additionally runs local nginx and a USB/NFC input service for its own hardware; these do not add another database or a different central API. The packaged Mac app opens the staff interface and starts the local services when necessary.

## Staff browser sizing

Use the browser's actual content viewport rather than the panel's native pixels when checking layout. Apple lists a [2408 × 1506 panel](https://www.apple.com/macbook-neo/specs/); a [hands-on Neo review](https://saggiamente.com/2026/04/recensione-macbook-neo-apple-entra-in-una-nuova-fascia-di-prezzo-senza-snaturare-il-mac/) reports a default “looks like” desktop of 1408 × 881. Browser toolbars, the menu bar, Dock, zoom, and window size change the space available to the app. The Neo has no camera notch, unlike current Air/Pro designs ([hands-on review](https://www.macworld.com/article/3081612)).

The layout suite includes a representative 1408 × 760 browser viewport plus smaller windows; this is a test size, not a claim about every Neo browser's default dimensions. On the actual host, verify at the staff's chosen display scaling and browser zoom.

## Local installation

Install Homebrew, Python 3.14, PostgreSQL 18, Node.js and (for the outside link) cloudflared, then set up and install the app:

    brew install python@3.14 postgresql@18 node cloudflared
    deploy/macos/setup.sh
    python3 deploy/macos/build_app.py --install

This puts **Fabric Conditioning.app** in /Applications and opens it. The app is a menu-bar dot (green, amber or red) with: Open Fabric Conditioning, the outside link and Copy, each station's state, the printer, Update / Check for updates, and System health.

The app holds no code or data. Data lives in `~/Library/Application Support/Fabric Conditioning/` (database, config, logs). Code lives in `releases/<version>` there, and `releases/current` is the one that runs. Before the first update it runs this checkout.

## Starting at boot

The app registers a **boot service** with macOS (`SMAppService`, a launchd daemon that runs as this account, not root). The first time, macOS asks to allow it in **System Settings → General → Login Items**. After that:

- It starts at boot, before anyone logs in, and launchd restarts it if it stops.
- It runs `host.py serve`: the database, the web app and, when it's on, the Cloudflare outside link.
- Until it's approved, the app runs the same service at login instead (a LaunchAgent), and the menu says "Allow in Login Items to start at boot".

Useful commands from the project directory:

    python3 deploy/macos/host.py status
    python3 deploy/macos/host.py test
    scripts/update-host.sh --status

## Updates

**System health → Update to vX**, the menu-bar **Update** item, or `scripts/update-host.sh`:

1. Only release tags on `main` (`vYYYY.MM.DD-N`) are installed.
2. Build the new release in its own folder while the app keeps running, including the Pi update package.
3. Stop if the database's migration is unknown to the new release.
4. Back up to `~/fabric-backups/before-<tag>-<time>.zip`.
5. Switch `releases/current`. The boot service starts the new release. If it isn't healthy within 3 minutes, switch back.

Once a day the app checks GitHub for a newer tag and shows **Update to vX**. It never installs on its own. Updates can't be started through the outside link.

## Outside link (Cloudflare quick tunnel)

System health → **Outside link**: set a passcode (6+ characters), then **Turn on**. The service runs `cloudflared tunnel --url http://127.0.0.1:8120` and shows the random `https://….trycloudflare.com` link, with Copy. A restart gives a new link.

Requests through the tunnel carry Cloudflare's headers, so only they are asked for the passcode (kept 12 hours in a cookie; 10 tries per 10 minutes). On the lab network, on the Neo and on the Pis nothing changes. Setting a new passcode signs everyone out. Updates and outside-link settings can only be changed from the lab. A named tunnel with Cloudflare Access is the upgrade if this becomes permanent.

## What reboot recovery can guarantee

Once macOS has booted, the installed daemon requires no person to open the app or sign in to a desktop account. FileVault can require a person to unlock the startup disk before macOS can finish booting; an application daemon cannot bypass that preboot unlock. This implementation does not turn FileVault off or enable automatic login. See Apple's [FileVault recovery guidance](https://support.apple.com/en-gb/guide/mac-help/mh35881/mac) and [automatic login/FileVault explanation](https://support.apple.com/en-au/guide/mac-help/mchlp1158/mac).

The MacBook must remain powered on and awake for other devices to use it. For the lab host, keep it connected to power, keep the lid open unless an approved docking configuration has been tested, and use the macOS setting “Prevent automatic sleeping on power adapter when the display is off.” Screen locking is compatible with continued hosting. No power settings are changed automatically. See Apple's [sleep and wake settings](https://support.apple.com/en-gb/guide/mac-help/-mchle41a6ccd/mac).

After installation, verify a real cold start: unlock FileVault if prompted, leave the desktop logged out, and check /api/health and the kiosk from a second device. Then terminate the API process and confirm that the daemon restores service. These physical reboot and network tests remain installation acceptance checks.

## Lab network and printers

The API/web server listens on port 8120; PostgreSQL listens only on localhost port 55432. Staff browsers can use the MacBook's local hostname or reserved address, for example:

    http://MACBOOK-HOSTNAME.local:8120
    http://MACBOOK-HOSTNAME.local:8120/kiosk?station=shared-door

For the Pi's nginx API proxy, use a reserved LAN IP or a hostname resolved by the configured LAN DNS. A .local name is suitable only if that resolver actually resolves it; nginx's DNS resolver is not browser-side mDNS. See the [scanner guide](PI_STATION.md).

Allow the app's Python process through the Mac firewall for the trusted lab network when macOS prompts. Set a stable hostname or DHCP reservation with lab IT. The MacBook and printer must be reachable on the selected network. Add the Brother printer as a macOS printer queue, then select that queue in the app; Wi-Fi support does not create the queue automatically. See [PRINTER_AND_LABELS.md](PRINTER_AND_LABELS.md).

Outside access is off until a passcode is set and the outside link is turned on (see above).

## Checkpoints and SharePoint

Use the app's checkpoint export/import interface for database records. Host credentials/configuration, printer queues, and backup settings are configured separately on a fresh host. Automatic daily checkpoints default to 2 a.m. America/New_York with retention of 30 days; choose the destination in the app. A lab share or an existing locally synchronized SharePoint folder can be used when available to the service account.

SharePoint/Microsoft 365 account setup is separate. OneDrive synchronization normally depends on its user session, so pre-login checkpoints must be written to an accessible local destination and synchronized later. Keep the live PostgreSQL directory on the Mac's local disk; synchronize exported checkpoints, not live database files. The native host.py backup command downloads the same checked ZIP and requires a running host; it refuses to replace an existing destination. See [the backup guide](BACKUPS.md) for staged validation, RESTORE confirmation, and retained previous databases. Restoring a checkpoint should be tested on a separate fresh installation before relying on unattended recovery.
