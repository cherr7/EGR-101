# Scanner implementation and deployment guide

The room station runs Chromium in fullscreen kiosk mode on 64-bit Raspberry Pi OS Desktop with labwc/Wayland. It uses the current React scanner interface, stored locally on the Pi, and the shared FastAPI/PostgreSQL host on the MacBook Neo. No specimen database is stored on the Pi. Ordinary scans record automatically in the persistent IN/OUT direction; only numbered selection and early checkout require popup actions.

## Architecture and supported hardware

The target is a Raspberry Pi 5, Touch Display 2 in 1280 × 720 landscape, and an Eyoyo EYH2 USB keyboard scanner. The default labels carry a specimen's label code (its number plus a check digit) in a 16 × 16 ECC200 Data Matrix. The supported physical unit already emits Enter, Down Arrow, and Num Lock; the local reader handles that observed sequence without reprogramming the scanner. Keep scanned codes as strings; the server parses and verifies them.

On an installed Pi, a local Python service reads the USB scanner directly through Linux evdev and an exclusive device grab. Completed frames travel through local nginx to the React page over a server-sent event stream. Chromium remains the display and the page uses the same lookup/movement API as hosted scanner pages. Reading the hardware directly removes the normal dependency on Chromium having OS keyboard focus. A visible, healthy scanner page is still required, and only a database-confirmed commit produces green success.

Optional PN532 NFC input uses the same service. It reads the app specimen ID from an NDEF text record, rather than treating the tag's hardware UID as the specimen ID. NFC is enabled only by installing with `--nfc`; Data Matrix labels and the USB workflow remain unchanged.

| Component | Source |
| --- | --- |
| Scanner UI, persistent preferences, pending movement/Undo recovery | frontend/src/Kiosk.tsx |
| Browser adapter for direct input and freshness checks | frontend/src/inputBridge.ts |
| Shared code validation and keyboard capture | frontend/src/scannerCapture.ts |
| Direct USB reader, discovery/recovery, status, event stream | deploy/scanner-kiosk/input-bridge.py |
| NFC normalization, tag read/write, bounded NDEF parsing | deploy/scanner-kiosk/nfc_tags.py |
| Local startup page and browser launcher | deploy/scanner-kiosk/startup.html and browser.sh |
| Local page heartbeat and browser recovery | deploy/scanner-kiosk/runtime.js and watchdog.py |
| Station package and integrity validation | scripts/build-station.py and deploy/scanner-kiosk/validate-package.py |
| Database transactions and history | backend/app/movements.py, corrections.py, services.py |

The superseded deploy/pi implementation and old prototype galleries are no longer deployment sources. Use only deploy/scanner-kiosk. Stop any old installation occupying port 8080 before installing this one.

The exact endpoint, ownership, timing, and failure contract is documented in [SCANNER_INPUT_PROTOCOL.md](SCANNER_INPUT_PROTOCOL.md).

## What operators see

The header contains PPE / Shared / Footwear on the left, Stack / Individual in the middle, and a green Online or red Offline database indicator on the right. There is no repeated room title, sound toggle, input-transport jargon, or explanatory ready-screen paragraph. Room and persistent direction survive reloads. The installed room is the initial default. Room changes are blocked during requests or popups; Activity and Undo then switch to the selected room.

The main ready/result area stays separate from Activity. The fixed bottom dock contains equally sized IN, OUT, and Undo buttons, without subtitles. Only Activity scrolls during ordinary scanning; popup bodies scroll while their actions remain visible. The target is 1280 × 720, with a fallback layout at 800 × 480.

- Stack IN moves members awaiting their first entry. Stack OUT moves remaining members still inside: after three of ten leave, the next stack OUT moves seven.
- Individual opens numbered checkboxes; Stack/Individual remains selected after a movement, override, or Undo and survives reloads. Only an explicit mode change switches it.
- Early OUT requires an explicit override and records actual removal/incomplete conditioning.
- Repeat scans preserve original times and do not create another conditioning period.
- The confirmed commit response immediately inserts the scan at the top of Activity. A follow-up detail/history request is not required, and an older poll cannot erase the new row.
- Undo targets the newest scan shown in Activity, subject to the backend's intervening-activity checks. It marks that row Undone and disables; it never silently steps backward to another scan. A new recorded scan can become the new target. There is no kiosk Redo or per-row Undo button.
- Activity shows scans with their current Undone/Modified status. The staff transaction log retains separate original, amendment and Undo audit events.
- Green lasts 12 seconds without blocking a new scan. It shows count/time, project/material, test/treatment and inside/finished/total counts. The main area then returns to Ready for IN/OUT; Activity retains the scan.
- Ordinary scan errors, duplicate notices and successful Undo feedback clear after four seconds. Another scan can replace them immediately. Offline, unavailable input, unresolved movements/Undo and popup warnings are persistent conditions; they remain until resolved and block the affected operation.

Readiness means recorded residence reached 24 hours. Environmental measurements remain in the laboratory's existing system. This app tracks one designated room visit per specimen, not subsequent oven/laundry/testing stages.

## Input safety and recovery

One active event-stream page owns direct scanner delivery. A second page is refused while that connection is alive; it must not compete using a different room/direction. Closing the first page releases ownership. This is a simple connection safeguard, not an operator account or permissions system.

Device discovery/open/grab failures are retried and exposed as input status. Worker failures must not leave a healthy-looking service with no reader. A full or stale subscriber is closed rather than silently removed while its stream continues. There is no persistent/offline scan queue or replay to a later page. If no page is available, a frame is stale, or a popup/request blocks scanning, rescan after readiness returns. A scanner's own beep does not establish that a movement was recorded.

The reader requests Linux CLOCK_MONOTONIC event timestamps before grabbing the device. It rejects stale/future kernel-buffered frames and lost-key notifications instead of re-dating them as fresh when the process resumes. If the kernel timestamp setup fails, reader readiness stays false and the service reports/retries the failure. The stream has a process session ID, sequence IDs, and monotonic timestamps. The browser calibrates against the Pi's status endpoint using its own monotonic clock and bounded request timing; it rejects unsupported, duplicate, stale, wrong-session, and implausibly future frames. Wall-clock changes used to display laboratory timestamps do not determine transport freshness. Reconnects establish current stream/clock state without replaying old frames.

Database availability and usable input are separate states. A connected HTTP stream alone is insufficient to show reader readiness or bypass focus recovery. A background/hidden station page does not submit bridged scans. Hosted scanner pages on staff computers continue using keyboard capture. Operators see Connect scanner, Scanner in use, or Tap to resume only when action is needed. Transport details remain in the local status endpoint and service logs. A page refused ownership cannot use fallback to compete with the owning page.

Keyboard capture uses a capture-phase document listener, so no visible input field or focused text box is needed. Touching an IN/OUT button or checkbox does not lose capture. It consumes Enter/Space before controls can activate, rejects incomplete/malformed frames, and clears buffered input on focus/visibility changes. It accepts 2–20 digit label codes with an optional AIM Data Matrix prefix; anything else is rejected, and the server verifies the check digit. OS keyboard capture still requires Chromium focus when used without the direct service. See [MDN key events](https://developer.mozilla.org/en-US/docs/Web/API/Element/keydown_event) and [document.hasFocus](https://developer.mozilla.org/en-US/docs/Web/API/Document/hasFocus).

Before submitting a movement, the page saves its request UUID and exact payload in persistent browser storage. A lost response blocks another scan until the page queries/retries that same request. Changed selections or early-OUT acknowledgment use a new UUID. Pending Undo is also saved and reconciled after reload using the exact transaction status endpoint, independent of log pagination. Keep the dedicated Chromium profile; never clear storage while an outcome is unresolved.

An unreachable API/database shows Offline in red and accepts no offline transactions. Reconnection reconciles previously submitted requests; it does not upload scans taken during an outage. Printer submission, staff corrections, imports, and backups continue using the same central backend.

## Build and install

Build the current frontend and station package on the Mac:

    cd frontend
    npm ci
    npm run build
    cd ..
    python3 scripts/build-station.py --station shared-door

The package is output/station-ui. It contains compiled React assets, station configuration, bootstrap/runtime, and a checksummed manifest. The self-contained startup page ships in the deployment directory and is installed under /opt/fabric-scanner/bin. Rebuild after frontend or station-runtime changes. Transfer that directory plus deploy/scanner-kiosk to each Pi. Do not transfer the Mac database, credentials, or customer workbooks.

Use ordinary 64-bit Raspberry Pi OS Desktop, a dedicated desktop account, LAN networking, SSH maintenance, and 1280 × 720 landscape. The installer configures Desktop Autologin and disables screen blanking where raspi-config is available. Raspberry Pi OS Full includes additional applications that are not required. Lite lacks a graphical desktop and is not a drop-in target for this installer. The official [kiosk tutorial](https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/) describes the Desktop/labwc foundation; its rotating-tab demonstration is not needed here. See [OS image descriptions](https://www.raspberrypi.com/software/operating-systems/).

On the Pi, install prerequisites:

    sudo apt update
    sudo apt install nginx chromium python3 python3-evdev python3-venv

Run a read-only preflight first, substituting actual paths/account/address/resolver:

    sh deploy/scanner-kiosk/install.sh --check /path/to/station-ui http://MAC_RESERVED_IP:8120 KIOSK_USER LAN_DNS_IPV4 shared-door

Install, optionally adding --nfc:

    sudo sh deploy/scanner-kiosk/install.sh /path/to/station-ui http://MAC_RESERVED_IP:8120 KIOSK_USER LAN_DNS_IPV4 shared-door
    sudo reboot

Use ppe-door or footwear-door for the other stations. The API origin is a scheme/host/port without an /api suffix or credentials. Use a reserved Mac LAN IP or a hostname resolved by the supplied LAN DNS server. nginx does not use the browser's mDNS lookup; .local works only if the configured resolver actually resolves it.

The installer places the UI under /opt/fabric-scanner/ui, programs under /opt/fabric-scanner/bin, and services in the chosen account's user service manager. It provisions nginx on port 8080, the input environment/configuration, autostart, and required device-group access. Group changes take effect after a fresh login/reboot. Optional NFC dependencies require installation-time network access. Normal startup and the unavailable screen do not require Internet access.

Exact scanner pinning, optional NFC wiring/CLI, fresh installation, and migration commands are maintained in the [deployment README](../deploy/scanner-kiosk/README.md). A stable SCANNER_DEVICE path is preferable to broad device-name matching; never guess the Eyoyo's Linux device identity from its retail name.

## Startup and upgrades

The intended chain is:

    Pi power on
      → Raspberry Pi OS graphical autologin
      → labwc imports the graphical environment and starts fabric-scanner.target
      → Chromium immediately opens the local Starting station page
      → local station service becomes ready; page navigates to the React scanner
      → input ownership/reader health and database health establish readiness

The launcher no longer silently polls the local web server before opening Chromium. Its self-contained startup page shows progress while waiting for that server. The startup page does not wait for the Mac API; the scanner UI can show Offline after a backend-unavailable cold boot. The initial OS/graphical/Chromium startup time still requires measurement on hardware. This does not claim zero desktop visibility before Chromium itself appears or establish the cause of the reported 20–30-second delay.

Systemd restarts a crashed browser/input process. A local heartbeat every two seconds reports visibility, focus, capture attachment, and usable bridge state. The watchdog allows 45 seconds at startup, then restarts a browser after more than 15 seconds without healthy page state, with restart-loop limiting. Usable direct input can allow a visible page to work without OS keyboard focus. Connection alone or a failed reader cannot grant that exemption. Database outage alone does not trigger a browser restart.

For an upgrade, stop the station target before replacing files. The installer migrates the old standalone NFC service into the shared input service, reloads user service definitions, and preserves the operator-owned scanner.env. Use the documented restart/reboot path; do not delete an old running NFC runtime manually. Reboot is the normal completion step, particularly after device-group changes. Preserve the Chromium profile and unresolved request identity throughout.

## Maintenance and acceptance

Run these as the dedicated kiosk account over SSH:

    systemctl --user status fabric-scanner.target fabric-scanner-browser.service fabric-scanner-input.service fabric-scanner-watchdog.service
    journalctl --user -u fabric-scanner-browser.service -u fabric-scanner-input.service -u fabric-scanner-watchdog.service -b
    curl http://127.0.0.1:8766/status

For maintenance, stop fabric-scanner.target before opening other software or changing scanner configuration. After code/unit changes, daemon-reload and restart, or reboot according to the deployment guide. Stopping the input process releases its evdev grab. Scanner list/read/write diagnostics are in that guide. A hung OS, failed display, removed device, or unavailable network still needs maintenance; no software confirmation should imply otherwise.

Software verification commands:

    python3 -m unittest discover -s deploy/scanner-kiosk -p 'test_*.py'
    node deploy/scanner-kiosk/check-package.mjs
    python3 deploy/macos/host.py test
    cd frontend
    API_URL=http://127.0.0.1:8121 npx playwright test

Use a disposable *_test PostgreSQL database/API for browser workflows. The normal lab database is never a test target. Automated checks simulate the input transport and exercise real HTTP/database workflows; they do not qualify actual evdev hardware or Pi power-on behavior.

When hardware testing resumes, verify:

- Cold boot: visible startup feedback, automatic fullscreen scanner, measured boot-to-first-paint and boot-to-ready timing.
- Mac unavailable at boot: local UI loads, Offline, no writes/queued scans; later reconnect recovers.
- Actual Eyoyo stable path, exact label-code digits, Enter suffix, first-read performance, and unplug/replug without a manual restart.
- Visible but unfocused browser, hidden/closed/frozen page, competing second page, service crash, and fallback recovery without duplicate movements.
- Partial three-of-ten OUT followed by remaining-seven OUT; repeated scans preserve times; early override and one-tap Undo behave correctly.
- Lost movement/Undo responses and reboot/reload preserve pending identity and reconcile once.
- Fixed controls/log scrolling and popup action visibility at the target screen size; optional audio on the actual display/speaker arrangement.
- Printed Data Matrix readability, scale, cutter behavior, and adhesion using the actual Brother stock.

Initial physical testing subsequently verified one 12-specimen IN transaction and eight exact Data Matrix payloads, including leading zeros and consistent Enter/Down/Num-Lock trailers. The captured sequence is a regression fixture. The remaining physical checks above still require acceptance; see [the input contract](SCANNER_INPUT_PROTOCOL.md#supported-eyoyo-keyboard-sequence).

## Activity and room inventory

The scanner divides its main screen equally between the result and Activity. The upper-right expand button opens detailed Activity with a minus button for each movement; minus reverses that exact transaction, preserves its audit trail, and is disabled after Undo or intervening activity. The dock still targets only the newest movement. Inventory opens a read-only popup for the selected room, defaulting to Ready, with Inside, Next 2h, and project filters. Rows show ready/inside counts, elapsed time, and expandable specimen numbers. Inventory refreshes every ten seconds, updates readiness against server time, and clears stale results on failure. Scans are discarded while either popup is open; close it before scanning. Only the list scrolls, leaving filters and Close visible.
