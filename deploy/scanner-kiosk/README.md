# Dedicated live scanner station

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

USB unplug/replug, discovery errors, permissions failures, and grab failures are retried and shown as reader status. The Linux reader requests monotonic kernel event timestamps and rejects old buffered frames; an unsupported timestamp configuration stays visibly unavailable rather than accepting unverified input. Device discovery failures do not silently kill the reader thread. A gap or oversized frame is rejected; neither a rejected fragment nor a queued late scan becomes an automatic movement. Configure the physical scanner's Enter suffix; the Data Matrix itself contains the specimen's 128-bit ID as 39 decimal characters. The service reads those keystrokes directly and does not depend on a visible text box.

Physical Eyoyo testing on ULScanner1 confirmed that it reports as `USB HIDKeyboard`; pin its by-id path rather than broadening discovery to every keyboard. This unit also sends Num Lock (Linux key 69) around the scan. The decoder ignores that non-character toggle. With `SCANNER_DEBUG=true`, `USB frame` logs preserve the assembled text, its length, and rejection causes even for a rejected frame. A 39-digit physical scan was verified through a single 12-specimen IN transaction in the shared room; reboot, printer output, and the rest of the physical movement workflow still need acceptance testing.

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
/opt/fabric-scanner/input-env/bin/python /opt/fabric-scanner/bin/nfc_tags.py write 4d2a1c3b-9e57-4f60-8b1a-2f6c0d7e9a15
/opt/fabric-scanner/input-env/bin/python /opt/fabric-scanner/bin/nfc_tags.py read
systemctl --user start fabric-scanner.target
```

The write command accepts a UUID or its 39-digit payload and verifies the written tag. The example UUID above is only an example: use the registered specimen's actual ID. Truncated or invalid NDEF data is rejected.

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
