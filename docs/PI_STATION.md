# Pi room station

Short version: [QUICKSTART.md](QUICKSTART.md#4-pi-station-first-time-setup). This page has the details.

## One-command install from the Mac

`scripts/deploy-pi.sh` does the build, copy and install steps below in one go:

```sh
bash scripts/deploy-pi.sh PI-USER@PI-ADDRESS shared-door
```

It builds the frontend and station package, copies `output/station-ui/` and `deploy/scanner-kiosk/` to the Pi over one SSH connection, installs the apt prerequisites, runs `install.sh --nfc` with this Mac's `en0` address as the API (`http://MAC:8120`) and the Pi's own DNS resolver, adds `KIOSK_DIRECT_START=true` (see "Startup, focus, and recovery"), checks the Pi can reach `/api/health`, and reboots. Use it for updates too. The manual steps follow.

## Architecture and components

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

The exact endpoint, ownership, timing, and failure contract is in [SCANNER_INPUT_PROTOCOL.md](SCANNER_INPUT_PROTOCOL.md). What operators see and do is in [STAFF_GUIDE.md](STAFF_GUIDE.md#room-scanner-pi-station).

This is the supported Pi deployment for the React scanner and shared Mac-hosted API. Use Raspberry Pi OS Desktop (64-bit, labwc/Wayland), a Pi 5, and a Touch Display 2 in landscape at 1280 × 720. Chromium still runs in kiosk mode. Changing to Raspberry Pi OS Lite is unnecessary for this deployment; Lite would need a graphical session installed and configured separately.

Each Pi keeps the actual React assets locally and proxies same-origin `/api` calls to the Mac, normally port 8120. If the Mac is unavailable, the scanner screen remains visible with **Offline**. There is no offline transaction queue. Only an already submitted request is retained for reconciliation with its original UUID.

USB input normally follows **Eyoyo → Linux evdev → local input service → Chromium → Mac API → PostgreSQL confirmation**. The input service grabs the scanner device, so focus elsewhere does not divert ordinary scans or type them into another application. It also handles the optional PN532 reader. A focused keyboard-wedge fallback is available only if the local input service is unreachable, not while a different page owns the service. The app visibly distinguishes database availability, reader readiness, and keyboard fallback.

## Build and preflight on the Mac

```sh
cd frontend
npm run build
cd ..
python3 scripts/build-station.py
sh deploy/scanner-kiosk/install.sh --check output/station-ui http://192.168.1.50:8120 kiosk 192.168.1.1 ppe-door
```

`output/station-ui/` contains the production assets, `station.html`, bootstrap/configuration, heartbeat runtime, and a checksummed manifest. Both entry pages force `#kiosk` before React mounts. The default room is `shared-door`; use `--station ppe-door` at build time or the optional fifth installer argument to change it. The three known stations are `ppe-door`, `shared-door`, and `footwear-door`.

The installed configuration has `inputBridge: true` and `fixed: false`. PPE / Shared / Footwear remain selectable and the highlighted choice survives reloads. The initial room is a default, not a locked room. Operator room changes do not require rebuilding the package.

`--check` only reads files and validates checksums and argument formats. It runs on a Mac without root or Linux services; adding `--nfc` also checks the optional source files. It does not prove API reachability, device permissions, or DNS resolution. Replace example addresses with the actual reserved Mac LAN address and Pi LAN DNS resolver. A lab hostname works only if that resolver knows it; do not assume nginx's unicast resolver can resolve Bonjour `.local` names.

## Install on the Pi

1. Install Raspberry Pi OS Desktop and configure networking, time synchronization, landscape display rotation, and SSH for maintenance. No OS reinstall or Lite conversion is required for an existing compatible Desktop station.
2. Install prerequisites:

   ```sh
   sudo apt update
   sudo apt install nginx chromium python3 python3-venv python3-evdev ca-certificates
   ```

3. Copy `output/station-ui/` and this complete deployment directory to the Pi, then install for the real desktop account:

   ```sh
   sudo sh ~/scanner-kiosk/install.sh ~/station-ui http://192.168.1.50:8120 kiosk 192.168.1.1 ppe-door
   sudo reboot
   ```

The optional last argument overrides the package's initial room. Repeat for all three Pis. When `raspi-config` exists, the installer sets Desktop Autologin for that user (`do_boot_behaviour B4`) and disables screen blanking (`do_blanking 1`). Otherwise it prints the corresponding manual steps. The Pi user is added to `input`, and its **user** systemd manager is reloaded. A running graphical station is restarted after installation; a reboot is still required to apply new supplementary groups and optional SPI settings.

The legacy `conditioning-tracker` nginx site from the removed `deploy/pi` package must be stopped and disabled first if it occupies port 8080. The installer detects that conflicting site. Run one station deployment.

## USB device selection and operator settings

The bridge discovers keyboard-capable USB devices whose names contain `barcode`, `scanner`, or `eyoyo`. It refuses ambiguous matches instead of choosing an arbitrary keyboard. If the Eyoyo reports only a generic name such as `HIDKeyboard`, or more than one candidate exists, pin its stable device path:

```sh
ls -l /dev/input/by-id/
sudo nano /opt/fabric-scanner/scanner.env
```

For example, use the actual scanner's `...-event-kbd` link, not the literal placeholder:

```text
SCANNER_DEVICE=/dev/input/by-id/usb-ACTUAL_SCANNER-event-kbd
# Alternatively, customize candidate name fragments:
# SCANNER_MATCH=eyoyo,barcode,scanner
```

Then, from the kiosk user's session:

```sh
systemctl --user restart fabric-scanner-input.service
```

For hardware testing, add `SCANNER_DEBUG=true` to `/opt/fabric-scanner/scanner.env` and restart the input service. Follow `journalctl --user -fu fabric-scanner-input.service -o cat`. It prints the exact assembled scan string in JSON (including leading zeroes), source, rejection messages, and delivery status. This confirms input delivery, not a database commit; compare with Activity. Remove the setting after testing. Malformed/unfinished frames may not have a complete scan value. The station's IPv4 addresses appear below Online/Offline and refresh every 15 seconds, even when the Mac API is offline.

The installer preserves an existing `/opt/fabric-scanner/scanner.env` on updates. It regenerates `input.env` with `ENABLE_NFC=true` or `false`; `scanner.env` is read afterward and can override those service settings. Use that override deliberately. Do not put account passwords or API credentials in these world-readable station files.

USB unplug/replug, discovery errors, permissions failures, and grab failures are retried and shown as reader status. The Linux reader requests monotonic kernel event timestamps and rejects old buffered frames; an unsupported timestamp configuration stays visibly unavailable rather than accepting unverified input. Device discovery failures do not silently kill the reader thread. A gap or oversized frame is rejected; neither a rejected fragment nor a queued late scan becomes an automatic movement. Configure the physical scanner's Enter suffix; the Data Matrix itself contains only the specimen's label code (its number plus a check digit). The service reads those keystrokes directly and does not depend on a visible text box.

Physical Eyoyo testing on ULScanner1 confirmed that it reports as `USB HIDKeyboard`; pin its by-id path rather than broadening discovery to every keyboard. This unit also sends Num Lock (Linux key 69) around the scan. The decoder ignores that non-character toggle. With `SCANNER_DEBUG=true`, `USB frame` logs preserve the assembled text, its length, and rejection causes even for a rejected frame. A physical scan was verified through a single 12-specimen IN transaction in the shared room; reboot, printer output, and the rest of the physical movement workflow still need acceptance testing.

The same unit emits Down Arrow after the terminating Enter. The decoder consumes this observed trailer only while no payload is active and within 350 ms after Enter. Down Arrow inside a payload, unrelated controls, partial frames, stale data, and lost kernel events still reject the scan. All eight physical diagnostic Data Matrix scans matched their expected payloads, including leading zeros, case, punctuation, URL, and repeat identity. Their 528-key-event capture is a regression fixture. Use this observed keyboard configuration as the station baseline; reprogramming the scanner is not a setup requirement. `SCANNER_TRACE_KEYS=true` adds bounded ordered payload-key/timing diagnostics; normally leave it off. Full hardware movement acceptance remains necessary. Virtual-COM and HID-POS are not implemented transports.

## Optional NFC

A PN532 SPI hat can read NTAG21x NDEF specimen tags alongside the USB scanner:

```sh
sudo sh ~/scanner-kiosk/install.sh --nfc ~/station-ui http://192.168.1.50:8120 kiosk 192.168.1.1 ppe-door
sudo reboot
```

This installs the optional dependencies from `nfc-requirements.txt` into `/opt/fabric-scanner/input-env`, adds `spi` / `gpio` groups where available, and enables SPI using `raspi-config`. Dependency installation needs network access. Rerun without `--nfc` to disable NFC; USB input remains enabled.

NFC is now a reader in `fabric-scanner-input.service`, not a separate virtual keyboard. During an upgrade the installer stops the old target / `fabric-scanner-nfc.service` before replacing its runtime and removes its old target link and unit. This prevents a tag tap reaching the app twice through old and new input routes. The old PR #1 input behavior is preserved through the shared service; `/dev/uinput` is no longer needed by this implementation.

Hat wiring: I1=H, I0=L, RSTPDN→D20; DIP 1–4 (SCK/MISO/MOSI/D4) ON, 5–8 OFF. Tag support is 13.56 MHz HF/NFC at tap distance, not passive UHF RFID. For writing or checking a real registered specimen ID, stop the target to release the hat:

```sh
systemctl --user stop fabric-scanner.target
/opt/fabric-scanner/input-env/bin/python /opt/fabric-scanner/bin/nfc_tags.py write 123459
/opt/fabric-scanner/input-env/bin/python /opt/fabric-scanner/bin/nfc_tags.py read
systemctl --user start fabric-scanner.target
```

The write command takes the label code printed as "No." on the specimen's label and verifies the written tag. `123459` above is only an example. Truncated or invalid NDEF data is rejected.

## Startup, focus, and recovery

The startup sequence is **Desktop Autologin → labwc autostart → user systemd target → Chromium kiosk starting screen → local React scanner**. `browser.sh` immediately launches the self-contained `file://` starting page; it no longer waits silently for nginx before launching Chromium. That page says “Starting station…” and polls only read-only local `/station/ready`. Once the local station page exists it replaces itself with `http://127.0.0.1:8080/station.html?station=…#kiosk`. It never waits for the Mac API or internet. The file-origin CORS permission is restricted to the readiness route; it is not added to API writes. Only a validated loopback station target is accepted.

This provides feedback **after Chromium opens**. It does not claim to remove desktop/compositor startup time, and the exact boot-to-browser delay still needs measurement on the Pi. There can still be a desktop interval before Chromium itself is ready.

On ULScanner1 with Chromium 155, the file-origin starting screen did not reach the station page during hardware onboarding. The tested workaround is `KIOSK_DIRECT_START=true` in `/opt/fabric-scanner/station.env`, followed by a browser-service restart. This opens the local station URL directly; the watchdog handles a failed initial load. The installer regenerates `station.env`, so reapply this workaround after reinstalling until the starting-screen issue is resolved.

The dedicated kiosk Chromium profile uses `--password-store=basic` to avoid an unlock-keyring prompt after desktop autologin. Do not save passwords in this profile; it only displays the lab station. The system keyring and other browser profiles are unchanged. The optional starting screen displays only “Starting…” during normal startup.

`fabric-scanner.target` starts the browser, watchdog, and input service. Chromium's persistent profile retains room selection and interrupted-request IDs across restarts. Do not erase that profile during ordinary updates.

The page posts `/station/heartbeat` every two seconds with `focused`, `visible`, `capture_ready`, and `input_bridge` booleans. `input_bridge` is true only when this page owns a calibrated stream and at least one reader is usable; an open socket alone is insufficient. The watchdog allows loss of browser focus only in that usable direct-input state. Hidden pages, missing capture, or a frozen page still fail health checks. After a 45-second startup grace period, over 15 seconds without a healthy heartbeat triggers browser restart, with restart backoff. A database outage does not cause browser restart loops.

The bridge listens only on loopback port 8766; nginx exposes same-origin `/station/input/events` and `/station/input/status`. The first active event-stream page owns input. An extra page receives an occupied status and cannot steal or duplicate scans. A dropped / overflowing connection closes and must reconnect; delayed frames are rejected and require a rescan. Status and frames carry a process session ID and monotonic timestamps. The browser calibrates against the local status endpoint, so an incorrect wall clock does not decide scan freshness. No scan replay or offline movement queue is provided.

The normal operator flow remains persistent **IN / OUT → scan → automatic database confirmation**, with one-tap Undo of the last transaction. The bottom dock stays visible, history scrolls independently, and green confirmation lasts 12 seconds without blocking the next scan. Choose specimens opens a popup. Early checkout uses an explicit override popup. Scanner Enter cannot press a popup's action button.

## Maintenance and updates

From the kiosk user's graphical session:

```sh
systemctl --user status fabric-scanner.target
systemctl --user status fabric-scanner-browser.service fabric-scanner-watchdog.service fabric-scanner-input.service
journalctl --user -u fabric-scanner-browser.service -n 50
journalctl --user -u fabric-scanner-watchdog.service -n 50
journalctl --user -u fabric-scanner-input.service -n 50
curl -fsS http://127.0.0.1:8080/station/input/status
sudo nginx -t
```

If managing the user services as root over SSH, address the kiosk user's manager, for example with `sudo -u kiosk XDG_RUNTIME_DIR=/run/user/$(id -u kiosk) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u kiosk)/bus systemctl --user …`. The graphical session must already exist. Running bare `sudo systemctl --user` addresses root's manager and is incorrect.

Stop the **target** for maintenance so the watchdog cannot reopen Chromium:

```sh
systemctl --user stop fabric-scanner.target
systemctl --user start fabric-scanner.target
```

To update, rebuild and recopy the station package plus deployment directory, rerun the installer with the same optional NFC setting, and reboot. Existing `scanner.env` and Chromium profile are preserved; install-generated room defaults and service files are refreshed. Recheck the highlighted room after updating. Use a reserved Mac LAN address or working lab DNS and verify access from each Pi.

## Software and physical verification

```sh
python3 -m unittest discover -s deploy/scanner-kiosk -p 'test_*.py' -v
sh -n deploy/scanner-kiosk/install.sh
sh -n deploy/scanner-kiosk/browser.sh
node deploy/scanner-kiosk/check-package.mjs
```

The package check serves real compiled assets on an ephemeral loopback port and opens headless Chromium. Its fixtures cover all three rooms; it asserts a confirmed movement uses the selected **PPE** station rather than silently falling back to Shared. It also checks remembered room selection, offline screen/reload, zero offline movements, bridge delivery/readiness, heartbeat, single-owner behavior, and the standalone starting screen through local-unavailable → local-ready. It requires the frontend's installed Playwright dependency. These are software simulations, not hardware acceptance.

Physical acceptance remains: repeated cold boots including Mac-unavailable boot; observed desktop-to-Chromium and total boot time; touch/display rotation; actual USB Data Matrix+Enter decoding; stable scanner device selection; unplug/replug and permissions; optional NFC taps; focus loss, hidden/frozen/closed browser recovery; popup scan rejection; lost movement/Undo responses; real stock/printed-code readability; and scan-to-confirmation latency. These provisioning scripts have not been installed on a physical Pi in this session.

## Input safety and recovery

One active event-stream page owns direct scanner delivery. A second page is refused while that connection is alive; it must not compete using a different room/direction. Closing the first page releases ownership. This is a simple connection safeguard, not an operator account or permissions system.

Device discovery/open/grab failures are retried and exposed as input status. Worker failures must not leave a healthy-looking service with no reader. A full or stale subscriber is closed rather than silently removed while its stream continues. There is no persistent/offline scan queue or replay to a later page. If no page is available, a frame is stale, or a popup/request blocks scanning, rescan after readiness returns. A scanner's own beep does not establish that a movement was recorded.

The reader requests Linux CLOCK_MONOTONIC event timestamps before grabbing the device. It rejects stale/future kernel-buffered frames and lost-key notifications instead of re-dating them as fresh when the process resumes. If the kernel timestamp setup fails, reader readiness stays false and the service reports/retries the failure. The stream has a process session ID, sequence IDs, and monotonic timestamps. The browser calibrates against the Pi's status endpoint using its own monotonic clock and bounded request timing; it rejects unsupported, duplicate, stale, wrong-session, and implausibly future frames. Wall-clock changes used to display laboratory timestamps do not determine transport freshness. Reconnects establish current stream/clock state without replaying old frames.

Database availability and usable input are separate states. A connected HTTP stream alone is insufficient to show reader readiness or bypass focus recovery. A background/hidden station page does not submit bridged scans. Hosted scanner pages on staff computers continue using keyboard capture. Operators see Connect scanner, Scanner in use, or Tap to resume only when action is needed. Transport details remain in the local status endpoint and service logs. A page refused ownership cannot use fallback to compete with the owning page.

Keyboard capture uses a capture-phase document listener, so no visible input field or focused text box is needed. Touching an IN/OUT button or checkbox does not lose capture. It consumes Enter/Space before controls can activate, rejects incomplete/malformed frames, and clears buffered input on focus/visibility changes. It accepts 2–20 digit label codes with an optional AIM Data Matrix prefix; anything else is rejected, and the server verifies the check digit. OS keyboard capture still requires Chromium focus when used without the direct service. See [MDN key events](https://developer.mozilla.org/en-US/docs/Web/API/Element/keydown_event) and [document.hasFocus](https://developer.mozilla.org/en-US/docs/Web/API/Document/hasFocus).

Before submitting a movement, the page saves its request UUID and exact payload in persistent browser storage. A lost response blocks another scan until the page queries/retries that same request. Changed selections or early-OUT acknowledgment use a new UUID. Pending Undo is also saved and reconciled after reload using the exact transaction status endpoint, independent of log pagination. Keep the dedicated Chromium profile; never clear storage while an outcome is unresolved.

An unreachable API/database shows Offline in red and accepts no offline transactions. Reconnection reconciles previously submitted requests; it does not upload scans taken during an outage. Printer submission, staff corrections, imports, and backups continue using the same central backend.

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

## Hardware acceptance checklist

When hardware testing resumes, verify:

- Cold boot: visible startup feedback, automatic fullscreen scanner, measured boot-to-first-paint and boot-to-ready timing.
- Mac unavailable at boot: local UI loads, Offline, no writes/queued scans; later reconnect recovers.
- Actual Eyoyo stable path, exact label-code digits, Enter suffix, first-read performance, and unplug/replug without a manual restart.
- Visible but unfocused browser, hidden/closed/frozen page, competing second page, service crash, and fallback recovery without duplicate movements.
- Partial three-of-ten OUT followed by remaining-seven OUT; repeated scans preserve times; early override and one-tap Undo behave correctly.
- Lost movement/Undo responses and reboot/reload preserve pending identity and reconcile once.
- Fixed controls/log scrolling and popup action visibility at the target screen size; optional audio on the actual display/speaker arrangement.
- Printed Data Matrix readability, scale, cutter behavior, and adhesion using the actual Brother stock.

Initial physical testing subsequently verified one 12-specimen IN transaction and eight exact Data Matrix payloads, including leading zeros and consistent Enter/Down/Num-Lock trailers. The captured sequence is a regression fixture. The remaining physical checks above still require acceptance; see [SCANNER_INPUT_PROTOCOL.md](SCANNER_INPUT_PROTOCOL.md#supported-eyoyo-keyboard-sequence).
