# Scanner station implementation guide

Updated October 22, 2026. Target: the 800 × 480 touchscreen and Eyoyo EYH2 USB scanner, using Chromium on 64-bit Raspberry Pi OS Desktop with labwc/Wayland.

The scanner sends keyboard characters consisting of binary digits followed by Enter, as confirmed during the hardware trial. This guide defines the dedicated frontend and the station startup/recovery configuration. The staff transaction editor remains a separate desktop screen.

## 1. What is implemented

The focused demo now captures keyboard scans across its entire page. No text box or input focus is needed on the actual station screen. The optional paste field appears only in the development preview controls. Binary text is the default.

The scanner still uses the local mock adapter in demo-api.js. It does not yet call the project's arbitrary HTTP API. The new deployment files provide a concrete Pi startup and focus-recovery setup; they have not been installed or cold-boot tested on a physical Pi.

Relevant files:

| Purpose | Source |
| --- | --- |
| Scanner UI and movement workflow | ../output/prototypes/round-three/scanner-template.html |
| Page-wide keyboard capture | ../output/prototypes/round-three/scanner-capture.js |
| Fullscreen standalone demo | ../output/prototypes/round-three/station.html |
| Local demo state across reloads | ../output/prototypes/round-three/station-state.js |
| Mock API boundary | ../output/prototypes/round-three/demo-api.js |
| Pi installer, launcher, watchdog and nginx | ../deploy/scanner-kiosk/ |
| Read-only desktop overview | ../output/prototypes/round-three/stacks-template.html |

Use the new deploy/scanner-kiosk setup for this design. The older deploy/pi directory targets the earlier application and is an alternative deployment, not an additional service to run on the same port.

## 2. Capture a scan without an input field

Attach one capture-phase keydown listener to the document when the scanner page mounts. The implementation is FabricScannerCapture.attach in scanner-capture.js.

1. Collect the characters into an in-memory buffer. Accept 1–40 binary digits, including leading zeroes.
2. Consume those keyboard events before they reach focused buttons or checkboxes.
3. On Enter, consume Enter too, validate the complete frame, decode the binary value, and look up that individual specimen.
4. Show its stack and the exact members selected by direction and scope. Lookup does not submit a movement.
5. A deliberate touchscreen Submit records the reviewed selection.

Use BigInt for conversion and range validation, then a JavaScript Number for the API if needed: all unsigned 40-bit integers are exactly representable. Do not use JavaScript bitwise operators, which truncate ordinary Number operands to 32 bits. One specimen ID identifies one individual; the API supplies its stack.

The capture module uses a configurable 250 ms maximum gap between characters. A timed-out or malformed frame stays invalid until its terminating Enter, so a suffix cannot become a second valid scan. Window blur and visibility changes clear the buffer. Modifier shortcuts and IME composition are not scanner input. Tune the gap against the actual scanner before acceptance.

While a selection popup, early-checkout override, database request, or unresolved confirmation is active, consume and reject new scans. Do not queue them. In particular, a scanner's Enter must never activate a focused Submit, Use selection, or Override button.

Reserve Enter for scanning even when the buffer is empty; an isolated suffix after a focus change must not activate a button. Touch remains the normal action input. For keyboard maintenance, use Space to activate a focused button.

The capture module has a dispose method; call it when unmounting a real React scanner route. Attach it once per scanner document, outside a component render loop. Keep it out of desktop editing routes so it does not intercept normal typing.

The browser cannot distinguish a keyboard-wedge scanner from another keyboard using keydown events alone. Keep this document dedicated to scanning. Keyboard events can reach the document even when a button has focus; this follows the browser's [keydown event model](https://developer.mozilla.org/en-US/docs/Web/API/Element/keydown_event).

### Label payload

The current demo labels encode a zero-padded 40-character binary string and show the decimal specimen ID as readable text. The builder decodes each generated Data Matrix back to that exact integer. Symbol size is selected by the encoder, not fixed at the earlier proposed 12 × 12. A 40-bit integer and 40 printed binary characters have different barcode storage requirements.

The scanner's Enter suffix is a scanner setting; it is not part of the Data Matrix payload. Test the actual label and scanner combination. Decimal and hex remain explicit development options, not automatic guesses.

## 3. Preserve the agreed movement behavior

Keep room name, database connection, persistent IN/OUT direction, Whole stack / Individual scope, Undo and Submit visible at 800 × 480. Only the recent log scrolls in the main view. Popups may scroll their bodies while their action buttons stay visible.

Each specimen conditions once: ENTRY, then EXIT. Whole-stack IN includes only members awaiting entry. Whole-stack OUT includes only members still inside; after 3 of 10 leave, the next whole-stack OUT selects the remaining 7. Partial movement uses numbered checkboxes. Early OUT opens a warning and requires an explicit override.

Green means the API confirmed a committed movement. Repeated movements preserve the original times. Undo reverses the entire transaction and preserves its observation in the log. No supervisor login or mandatory reason is introduced.

For the real API adapter:

- Configure a fixed station ID and room for each Pi. The demo currently uses Shared room / Station 01.
- Fetch specimen, stack membership, ENTRY/EXIT, room and selection version together.
- Submit one request containing all affected IDs. The API must commit all or none.
- Save the pending request ID and exact payload durably before sending. On page load or reconnection, reconcile that same request before enabling another scan.
- Changed selections or acknowledgment require a new request ID.
- After a browser restart, restore preferences and pending-request identity, then re-fetch current records. Do not restore a green success screen from cached data.
- Start with movements disabled until database health is confirmed. Poll with a bounded timeout; show NOT AVAILABLE on failure and recover when healthy.
- Do not submit or queue offline transactions.

The standalone demo's local storage preserves its simulated records and direction across reloads. This is useful for kiosk testing; it is not an offline database or a substitute for real API idempotency.

## 4. Startup and automatic recovery

The boot path is:

    power on
      → Raspberry Pi OS graphical desktop autologin
      → labwc autostart imports the Wayland environment
      → fabric-scanner.target starts the browser and watchdog
      → launcher waits for the local nginx page
      → Chromium opens station.html fullscreen
      → scan listener attaches and begins accepting complete frames

The UI files are installed locally, so browser startup does not wait for the internet or the central API. A real API outage should show NOT AVAILABLE inside the loaded UI. Keep frontend assets local too; do not depend on external fonts or script CDNs on the Pi.

The browser service uses Restart=always with a three-second delay. A dedicated persistent Chromium profile retains local state. Systemd owns the browser process; do not append an ampersand to the launcher or start a second browser outside the service.

The page sends a tiny same-origin heartbeat to local nginx every two seconds. Nginx forwards it to the loopback watchdog. It contains only three booleans: focused, visible, and capture_ready. It carries no specimen data.

The watchdog allows 45 seconds for startup. After startup, more than 15 seconds without a healthy page triggers a browser restart. It waits for startup again after restarting, avoiding a rapid restart loop. A frozen page, missing listener, closed tab, or persistent loss of focus can therefore recover without an operator finding a text box.

Database availability is deliberately absent from this heartbeat. A database outage must not cause the kiosk to restart repeatedly. The UI must remain available to display the outage.

Set window.fabricScannerCaptureReady to true only after installing the listener; set it false on unmount. The current scanner does this after attaching its handler. The installer adds the separate runtime.js heartbeat script to station.html.

Chromium must still be the active application to receive USB keyboard input. Fullscreen alone does not provide operating-system-wide capture. Use a dedicated desktop account and avoid launching other applications or opening OS dialogs on this station. Persistent focus loss is recovered by restarting Chromium; events already typed into another application cannot be recovered. The [document focus API](https://developer.mozilla.org/en-US/docs/Web/API/Document/hasFocus) distinguishes page focus from a merely active DOM element.

## 5. Install on the Pi

These commands are a deployment recipe. They have not been run on your Mac or a connected Pi.

1. Install 64-bit Raspberry Pi OS Desktop. Configure the display at 800 × 480, the network and a dedicated desktop user. Enable SSH for maintenance.
2. In raspi-config, enable Desktop Autologin for that user and disable screen blanking. Use the labwc/Wayland desktop session, not an older LXDE or Wayfire recipe. Configure automatic time synchronization; API timestamps remain authoritative UTC, displayed in America/New_York.
3. Install the prerequisites on the Pi:

       sudo apt update
       sudo apt install nginx chromium python3 ca-certificates

4. On the development Mac, rebuild and copy the demo and deployment files. Replace the SSH destination:

       .venv/bin/python output/prototypes/round-three/build.py
       rsync -av output/prototypes/round-three/ kiosk@pi-kiosk.local:~/fabric-station-ui/
       scp -r deploy/scanner-kiosk kiosk@pi-kiosk.local:~/

5. On the Pi, install using the chosen API origin, desktop user, and LAN DNS resolver:

       sudo sh ~/scanner-kiosk/install.sh ~/fabric-station-ui http://192.168.1.50:8000 kiosk 192.168.1.1
       sudo reboot

The origin example is a placeholder. The API route names remain the backend team's contract. Installing the current mock demo tests scanner capture and kiosk recovery only; it still displays DEMO and does not record laboratory movements. Replace the mock adapter and complete the API acceptance checks before operational use.

Nginx and the watchdog bind to loopback. No Cloudflare tunnel is required. Keep the browser URL fixed at http://127.0.0.1:8080/station.html. If a different service already uses port 8080, resolve that conflict before installation.

The labwc startup mechanism is documented in the [labwc configuration manual](https://labwc.github.io/labwc-config.5.html). Automatic service restart and intentional stops follow [systemd's service behavior](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html). The [Raspberry Pi kiosk tutorial](https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/) provides the platform background; the watchdog and request-reconciliation behavior here are project-specific.

## 6. Maintenance

Run these from the kiosk user's session. From SSH, first set XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS to that user's active session if systemctl reports no user bus.

    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus

    systemctl --user status fabric-scanner.target
    systemctl --user status fabric-scanner-browser.service fabric-scanner-watchdog.service
    journalctl --user -u fabric-scanner-watchdog.service -n 50
    journalctl --user -u fabric-scanner-browser.service -n 50
    sudo nginx -t

For maintenance, stop the whole target so the watchdog does not reopen the browser:

    systemctl --user stop fabric-scanner.target

Resume:

    systemctl --user start fabric-scanner.target

Reinstall updated UI files using the installer, then restart the target. Retain the dedicated browser profile and pending-request data. Do not erase the profile as a routine recovery step.

## 7. Physical acceptance

| Check | Expected result |
| --- | --- |
| Ten cold boots / normal restarts | Fullscreen station appears; first scan works without touching an input. |
| Tap IN, OUT, scroll the log, then scan | Complete binary frame is captured; all fixed controls remain visible. |
| Scan while Submit or Override has keyboard focus | Enter performs lookup or is rejected; it never confirms a movement. |
| Scan while a popup or request is active | Scan is rejected, not queued or applied later. |
| Incomplete, overlong, malformed or interrupted scan | No movement; complete rescan required. |
| Close or kill Chromium | Browser returns automatically. |
| Leave another window focused or hang the page | Watchdog relaunches Chromium after the configured recovery interval. |
| Disconnect central API, then reboot | Local UI loads; real adapter shows NOT AVAILABLE; no false green or offline writes. |
| Reboot after an API commit with a lost response | Same request ID reconciles exactly once; no duplicate movement. |
| USB unplug/replug | Next complete scan works without choosing a text box. |
| Actual printed Data Matrix labels | Registered specimen ID matches the readable decimal ID. |
| API-confirmed scan timing | Measure with actual hardware/API against the outline's one-second target. |

Browser tests exercise scan capture after button focus, scanner Enter suppression, popup blocking, malformed frames, timeouts, and reload state. Python tests exercise the local watchdog's startup, focus-loss and healthy-heartbeat behavior. Those checks do not replace the physical boot and scanner tests above.

## 8. Separate stack overview

The staff overview lists one row per registered stack and brings stacks with ready specimens to the top, then sorts by longest time inside. It also offers explicit longest-inside and next-ready sorting, search, room and status filters, and a read-only specimen popup.

For each specimen still inside:

    elapsed = server_now_utc - ENTRY
    ready = ENTRY exists AND EXIT is null AND elapsed >= 24 hours

A fully ready stack has all remaining members inside ready and no members still awaiting entry. A partially ready stack shows the exact ready count. Removed specimens remain visible in detail but cannot contribute to current readiness or current time-inside sorting. Different entry times produce an elapsed-time range.

The example E demonstrates 3 ready specimens among 7 inside, with 5 already removed. The overview has no checkout button; movement stays on the scanner. The existing transaction editor handles corrections.

For the real HTTP adapter, return per-stack counts plus earliest/latest current ENTRY, ready member IDs, next ready time, required duration, room and a server timestamp/version. Refresh counts after movements and corrections. A short local timer can advance elapsed time from that confirmed server timestamp, but stale or disconnected data must remain clearly labeled and must not promote new authoritative ready results. The prototype's time-advance controls are demo-only.
