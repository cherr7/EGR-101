# Mac host

Short version: [QUICKSTART.md](QUICKSTART.md#1-mac-first-time-setup). This page has the details.

The MacBook Neo hosts PostgreSQL, the HTTP API, the staff web app, and print jobs. The three Pi kiosks and staff browsers use this same host. Each Pi additionally runs local nginx and a USB/NFC input service for its own hardware; these do not add another database or a different central API. The packaged Mac app opens the staff interface and starts the local services when necessary.

## Staff browser sizing

Use the browser's actual content viewport rather than the panel's native pixels when checking layout. Apple lists a [2408 × 1506 panel](https://www.apple.com/macbook-neo/specs/); a [hands-on Neo review](https://saggiamente.com/2026/04/recensione-macbook-neo-apple-entra-in-una-nuova-fascia-di-prezzo-senza-snaturare-il-mac/) reports a default “looks like” desktop of 1408 × 881. Browser toolbars, the menu bar, Dock, zoom, and window size change the space available to the app. The Neo has no camera notch, unlike current Air/Pro designs ([hands-on review](https://www.macworld.com/article/3081612)).

The layout suite includes a representative 1408 × 760 browser viewport plus smaller windows; this is a test size, not a claim about every Neo browser's default dimensions. On the actual host, verify at the staff's chosen display scaling and browser zoom.

## Local installation

Install Homebrew, Python 3.14, PostgreSQL 18, and Node.js on the host:

    brew install python@3.14 postgresql@18 node
    deploy/macos/setup.sh

The setup script installs a Python environment outside the app, builds the web interface, and creates:

    output/macOS/Fabric Conditioning.app

Double-click the app. It starts the database and API, waits for a database-backed health response, then opens the staff interface at http://127.0.0.1:8120. It reuses an already healthy host. You can copy the app into Applications after setup; replacing the app does not replace database files.

The local installation stores data in:

    ~/Library/Application Support/Fabric Conditioning/

The directory contains private configuration, the database, logs, runtime dependencies, and backup settings. Database credentials are generated locally and written with owner-only permissions. The app bundle contains application code and compiled web assets; it contains no database or password.

Useful commands from the project directory:

    python3 deploy/macos/host.py status
    python3 deploy/macos/host.py start
    python3 deploy/macos/host.py stop
    python3 deploy/macos/host.py test
    python3 deploy/macos/build_app.py

For a launcher check that opens no browser and changes no service state:

    "output/macOS/Fabric Conditioning.app/Contents/MacOS/Fabric Conditioning" --check

The --no-open option starts or checks services without opening a browser. If PostgreSQL refuses to start with "postmaster became multithreaded during startup", set a locale first (`export LC_ALL=en_US.UTF-8`). Startup errors are logged in ~/Library/Logs/Fabric Conditioning/launcher-error.log; detailed service output is in the state directory's logs folder.

## Start automatically before interactive login

An app in Login Items starts after a person logs in. The supplied system LaunchDaemon starts after macOS boots and runs independently of an interactive session. Apple documents this distinction in [Designing Daemons and Services](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/DesigningDaemons.html) and [Creating Launch Daemons and Agents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).

Prepare the exact property list without making system changes:

    deploy/macos/install-daemon.sh --user YOUR_LOCAL_ACCOUNT

Review output/macOS/daemon-plan/org.egr.fabric-conditioning.plist. The service runs as the specified non-root account because PostgreSQL cannot run as root. Its code and runtime live in /Library/Application Support/Fabric Conditioning, so it does not depend on this repository or an unlocked home Documents/Downloads directory.

When installing on the intended host, first export a checkpoint from the app if the local installation contains records you want to keep. Stop the local host so ports 8120 and 55432 are free, then install:

    python3 deploy/macos/host.py stop
    sudo deploy/macos/install-daemon.sh --apply --user YOUR_LOCAL_ACCOUNT

The installer creates a separate system state directory. It never deletes or migrates the local database implicitly. Import the exported checkpoint into the system installation through the app if needed.

The installer copies application code and compiled assets, installs Python dependencies, initializes PostgreSQL as the service account, installs the reviewed property list with root ownership, and bootstraps the daemon. RunAtLoad and KeepAlive restart the host after boot or process failure. The supplied scripts have been prepared and tested without registering a system daemon on this development Mac.

Inspect or control the installed daemon:

    sudo launchctl print system/org.egr.fabric-conditioning
    sudo launchctl kickstart -k system/org.egr.fabric-conditioning
    sudo launchctl bootout system/org.egr.fabric-conditioning

Bootout stops the running service until the next boot. To disable future startup, boot it out and move its property list out of /Library/LaunchDaemons. Preserve the state directory and exported checkpoints.

For a code update, build the frontend again and rerun the same installer. Existing system data and configuration remain in place. If Homebrew upgrades/removes the Python installation used by the runtime, rerun setup or the installer with the new Python before removing the old interpreter.

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

This is a lab-network prototype. No Cloudflare tunnel or public Internet exposure is configured by these scripts.

## Checkpoints and SharePoint

Use the app's checkpoint export/import interface for database records. Host credentials/configuration, printer queues, and backup settings are configured separately on a fresh host. Automatic daily checkpoints default to 2 a.m. America/New_York with retention of 30 days; choose the destination in the app. A lab share or an existing locally synchronized SharePoint folder can be used when available to the service account.

SharePoint/Microsoft 365 account setup is separate. OneDrive synchronization normally depends on its user session, so pre-login checkpoints must be written to an accessible local destination and synchronized later. Keep the live PostgreSQL directory on the Mac's local disk; synchronize exported checkpoints, not live database files. The native host.py backup command downloads the same checked ZIP and requires a running host; it refuses to replace an existing destination. See [the backup guide](BACKUPS.md) for staged validation, RESTORE confirmation, and retained previous databases. Restoring a checkpoint should be tested on a separate fresh installation before relying on unattended recovery.
