# Scanner station implementation guide

The current station targets a Raspberry Pi 5, a **1280 × 720 landscape Touch Display 2**, and an Eyoyo EYH2 USB keyboard-wedge scanner. It uses the working application's HTTP API on the host Mac. The old mockups have been removed; the current app also has a compact fallback layout.

The core behavior is: choose persistent IN or OUT, scan a registered specimen label, and automatically record the eligible whole-stack movement. There is no visible input field and no routine Submit step. The staff database editor is a separate desktop interface.

## Components and deployment boundary

| Component | Implementation |
| --- | --- |
| Scanner page and movement/recovery state | frontend/src/Kiosk.tsx |
| Page-wide keyboard capture | frontend/src/scannerCapture.ts |
| HTTP adapter and timeout handling | frontend/src/api.ts |
| Central database/movement rules | backend/app/movements.py and services.py |
| Local station package builder | scripts/build-station.py |
| Generated local UI | output/station-ui/ |
| Pi installer, browser service, nginx, watchdog | deploy/scanner-kiosk/ |
| Mac host/app startup | deploy/macos/ |

The Pi serves compiled frontend assets locally through nginx and proxies /api requests to the Mac at port 8120. It does not host its own SQL database or accept offline transactions. A browser restart or unavailable Mac must still load the local UI and show the unavailable state.

Use deploy/scanner-kiosk for this implementation. The superseded deploy/pi sources and prototype galleries were removed. Stop any service installed from those old sources before installing this version.

## Barcode and scanner input

Each physical specimen has its own permanent 128-bit UUID linked to a stack. App-generated labels encode that value as **39 zero-padded decimal digits** in an ECC200 Data Matrix. The scanner should type those digits followed by Enter. The Enter suffix is configured on the scanner; it is not encoded in the label.

Decimal text is the agreed production format. Data Matrix does not mean binary keyboard output. Raw USB bytes and WebHID access are unnecessary for a scanner configured as a keyboard. Verify the device's real output in an editor before installing it on a room station. See [barcode and printer details](BARCODE_AND_PRINTER.md).

The generated code carries no URL or prep information. Scanning a member's ID lets the API resolve the whole stack and current eligibility. Keep IDs as strings across HTTP/JSON and in JavaScript; use BigInt for conversion if required, never Number or JavaScript bitwise arithmetic for a 128-bit value.

## Capture without an input box

The scanner route attaches one capture-phase keyboard listener to the document. Keyboard events reach it even when a direction button or checkbox has focus, so touching the screen does not require refocusing a text input. The listener is mounted only on the dedicated kiosk route and is removed on unmount; normal desktop typing remains unaffected. [MDN: keydown event](https://developer.mozilla.org/en-US/docs/Web/API/Element/keydown_event)

The implementation:

1. Buffers printable scanner characters until Enter.
2. Consumes the scanner's key events before focused controls can act on them.
3. Consumes Enter even when the buffer is empty, so a trailing scanner suffix cannot click an override or Undo button.
4. Rejects malformed/oversized input, interrupted scans, modifier shortcuts, and composition input.
5. Invalidates an incomplete frame after a **350 ms inter-character gap**; it waits for the frame's Enter before resetting validity.
6. Clears the input buffer on visibility/focus changes.
7. Delivers a complete valid ID only when database health is confirmed and no popup, write, pending movement, or pending undo blocks scanning.

New scans arriving while blocked are consumed and rejected, not queued. A dedicated scanner document cannot distinguish another keyboard from a keyboard-wedge scanner through key events alone. Touch is the ordinary action input; use the maintenance procedure below for general keyboard work.

The browser window itself must be active. A webpage cannot receive scanner keystrokes while another application, browser toolbar, or OS dialog owns keyboard focus. Kiosk mode and the Pi watchdog reduce those failure conditions; they do not grant a webpage control over the operating system. [MDN: document.hasFocus](https://developer.mozilla.org/en-US/docs/Web/API/Document/hasFocus)

## Movement and correction behavior

Keep room/station identity, connection state, direction, scope, and Undo visible. The large readiness/result region is separate from the recent log. Only the recent log scrolls in the main screen; popup bodies may scroll while their actions remain fixed.

An ordinary scan first looks up its stack and version, then automatically submits the movement. The backend locks records and validates that version before committing. IN and OUT remain persistent across successful scans and browser restarts.

- **Whole-stack IN:** move only members awaiting their first entry.
- **Whole-stack OUT:** move only members currently inside. After 3 of 10 leave, the next whole-stack OUT affects the remaining 7.
- **Individual specimens:** scanning opens numbered checkboxes; confirm the exact selection. A successful partial movement returns to whole-stack scope.
- **Early checkout:** before 24 hours, show affected specimens and require an explicit override. The actual exit and incomplete conditioning remain recorded.
- **Repeat movement:** show Already recorded and preserve timestamps. A finished specimen cannot start another normal conditioning period.
- **Undo last:** briefly confirm and reverse the entire last transaction. Original movement and undo remain in history. Intervening activity can make an undo unsafe, in which case the backend rejects it.

Only a confirmed database commit produces the temporary full green result. An API request being sent, a scanner beep, or a successful lookup is not a successful movement. After a short result interval the station returns to readiness; the confirmed transaction remains in recent activity.

Corrections and missed entries are available from the staff transaction log without supervisor authentication or mandatory reasons. They update the effective ENTRY/EXIT record and retain original observations. They do not create another conditioning cycle.

## Pending responses, reloads, and outages

Before sending a movement, the kiosk saves its request UUID and exact payload in persistent browser storage. If a response is lost, the station blocks another scan, checks that request's status, and retries only the same UUID/details if it was not recorded. Changing a selection or acknowledging early removal requires a new request UUID.

A pending undo is also preserved and reconciled against the transaction log before scanning resumes. Browser reloads and watchdog restarts retain these pending identities in the dedicated Chromium profile. Do not delete that profile or clear its storage while an outcome is unresolved.

The kiosk starts with movement submission disabled until database-backed health is confirmed. Loss of API/database access shows **NOT AVAILABLE**, disables writes, and accepts no offline transactions. Reconnection permits reconciliation of already submitted requests; it does not replay a queue of scans taken during the outage. Cached UI, direction preferences, and pending-request identity are not a local specimen database.

## Build the Pi UI package

Build from the current working application on the Mac:

    cd frontend
    npm ci
    npm run build
    cd ..
    python3 scripts/build-station.py

The default output is output/station-ui; --source, --output, and --station can select another compiled frontend, package folder, or default room. For example, python3 scripts/build-station.py --station ppe-door builds the PPE station package. The package contains the compiled station page/assets and deployment metadata/configuration. It is a real API-connected kiosk package, not the archived mock gallery. Rebuild it after frontend changes and transfer that directory plus deploy/scanner-kiosk to each Pi. No customer workbooks or application database files belong in this package.

## Provision a room station

Use 64-bit Raspberry Pi OS Desktop with labwc/Wayland. Configure a dedicated desktop account, lab networking, and an SSH maintenance path. Set the display to landscape at 1280 × 720. Enable **Desktop Autologin** for that account and disable screen blanking for the station. These settings are deployment actions on the physical Pi; this repository does not prove they have been applied.

Raspberry Pi's official kiosk tutorial documents the 64-bit desktop, labwc autostart, and fullscreen Chromium approach. This project's service supervision and local page add recovery around that foundation. [Raspberry Pi kiosk tutorial](https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/)

Install the required packages on the Pi:

    sudo apt update
    sudo apt install nginx chromium python3

Run the project's installer with the transferred package, Mac API origin, desktop username, a reachable LAN DNS resolver, and default station ID:

    sudo sh deploy/scanner-kiosk/install.sh /path/to/station-ui http://MAC_RESERVED_IP:8120 KIOSK_USER LAN_DNS_IPV4 shared-door

Replace the placeholders with actual values. The fifth station argument is optional and defaults to the station embedded in the package; the builder defaults to shared-door. Use ppe-door or footwear-door for the other room stations. The installed station is the initial room. The PPE, Shared, and Footwear buttons may change it; that selection is remembered on this device across reloads.

For read-only validation on the Mac or Pi before installation, run the same arguments with --check as the first argument; no root permission or system changes are needed:

    sh deploy/scanner-kiosk/install.sh --check /path/to/station-ui http://MAC_RESERVED_IP:8120 KIOSK_USER LAN_DNS_IPV4 shared-door

The API origin is only a scheme/host/port, with no /api path or credentials. Prefer a reserved Mac LAN address or a hostname resolvable by the configured LAN DNS. If the network does not resolve .local names through that resolver, use the reserved IP rather than relying on browser-side mDNS behavior.

The installer places the UI under /opt/fabric-scanner/ui, configures nginx on local port 8080, installs browser/watchdog user services, and adds a marked block to the chosen account's labwc autostart. Where raspi-config exists, it also sets Desktop Autologin for that account and disables screen blanking. Add --nfc to also install the optional PN532 NFC tag bridge (see deploy/scanner-kiosk/README.md). Follow its output, confirm the autologin and blanking settings took effect, then reboot the Pi.

## Boot and recovery sequence

    Pi power on
      → Raspberry Pi OS desktop autologin
      → labwc imports the graphical environment into the user service manager
      → fabric-scanner.target starts browser and watchdog
      → local nginx serves station.html
      → Chromium opens the local page in kiosk mode
      → capture listener attaches; API health enables scanning

The browser launcher waits only for the local page, not internet access or the Mac. It uses a dedicated persistent profile and runs Chromium in fullscreen kiosk mode. Systemd restarts the browser after a crash with a short delay.

A local heartbeat reports page focus, visibility, and whether the scanner listener is attached. The watchdog allows startup time, then restarts the browser if healthy page heartbeats stop. The current settings allow 45 seconds at startup, treat more than 15 seconds without a healthy page as a fault, and limit rapid restart loops. This heartbeat does not require database connectivity, so a Mac outage leaves the unavailable screen visible rather than deliberately restarting a healthy page.

This recovery is best effort: a hung OS, failed display, removed scanner, unresolved system dialog, lost power, or broken network still needs physical/maintenance attention. Interrupted scans must be rescanned after focus returns. The kiosk should not display a guarantee that a scan was recorded until the database confirms it.

## Maintenance and troubleshooting

Use SSH to the dedicated desktop account. Stop the target before opening another application on the touchscreen, otherwise the focus watchdog may restart Chromium during maintenance:

    systemctl --user stop fabric-scanner.target
    systemctl --user status fabric-scanner-browser.service fabric-scanner-watchdog.service
    journalctl --user -u fabric-scanner-browser.service -u fabric-scanner-watchdog.service -n 100

After maintenance:

    systemctl --user restart fabric-scanner.target

For a locally loaded screen showing NOT AVAILABLE, check the Mac's /api/health, LAN reachability/firewall, configured API origin, and DNS. Check nginx configuration/service separately if the local page itself does not load. Do not clear browser storage to fix an unresolved transaction; inspect/reconcile that transaction first.

Kiosk mode is an operating mode, not an authentication boundary. Keep the station dedicated to this page, and manage physical/maintenance access through the lab's chosen setup. The application does not add operator badges or supervisor login.

## Acceptance on real hardware

Verify each station with the actual scanner, screen, printer stock, and lab network:

- Cold boot to the local fullscreen UI with no manual browser launch.
- Boot while the Mac/API is unavailable: the local page loads, shows NOT AVAILABLE, and records nothing.
- Reconnect the Mac: health recovers without an offline scan queue.
- Touch direction/scope controls, then scan without focusing an input.
- Ordinary scan records automatically; selection/early-checkout popups require touch confirmation, and scanner Enter cannot activate their buttons.
- Check out 3 of 10, then use whole-stack OUT for only the remaining 7.
- Verify repeat scans preserve entry/exit times and Undo restores precisely its transaction.
- Interrupt a response or restart Chromium during a pending request/undo, then confirm exactly one effective result after recovery.
- Kill Chromium, hide/unfocus its page, and confirm supervised recovery without losing pending identity.
- Confirm fixed controls remain visible with a long log and many numbered specimens.
- Measure final-Enter-to-visible-confirmation latency against the one-second target and record physical first-read accuracy.

Automated browser/watchdog tests cover software behavior. Actual Pi cold-start, focus recovery on the installed OS, scanner suffix configuration, print readability, and physical timing remain hardware acceptance checks.

## Current scanner controls

The top row has PPE, Shared, and Footwear room buttons with a persistent highlighted selection. The installed station is the initial default; a room selected on the touchscreen survives reloads and reboot. Room changes are blocked while a transaction, popup, or unresolved result is active. Changing room clears the local Undo target; earlier transactions remain accessible through the staff log.

The main scan area groups Whole stack and Choose specimens. The bottom dock contains only persistent IN, OUT, and one-tap Undo last. Ordinary scans record automatically. A green confirmed result stays for 12 seconds, and another scan can start immediately. The last-result receipt remains when the green period ends. Early removal still requires the explicit override popup, and numbered partial movement needs its Record selected action.
