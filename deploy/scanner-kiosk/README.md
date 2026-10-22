# Dedicated live scanner station

This is the Pi deployment for the working React app and shared Mac-hosted API. It replaces the old prototype-only station package. Target hardware is Raspberry Pi 5, Touch Display 2 in landscape at 1280 × 720, and the Eyoyo EYH2 USB keyboard-wedge scanner.

Each Pi stores its UI locally. Only same-origin `/api` calls are proxied to the Mac, normally port 8120. An unavailable Mac therefore leaves the scanner page visible with NOT AVAILABLE. No transactions are queued offline. The React app preserves only the exact pending request needed to reconcile an interrupted response.

## Build on the Mac

Build the working frontend, then package it:

    cd frontend
    npm run build
    cd ..
    python3 scripts/build-station.py

The output is `output/station-ui/`: `index.html`, `station.html`, compiled assets, station bootstrap/configuration, heartbeat runtime, and a checksummed manifest. Both entry pages force `#kiosk` before the React module mounts. There are no mock records or offline write adapters in this package.

The default station is shared-door. Override it at build time with `--station ppe-door`, or supply the station ID to the Pi installer. The fixed IDs seeded by this project are shared-door, ppe-door, and footwear-door.

Read-only preflight can run on the Mac without root or Linux services:

    sh deploy/scanner-kiosk/install.sh --check output/station-ui http://192.168.1.50:8120 kiosk 192.168.1.1 ppe-door

This validates package integrity and argument formats. It does not prove API reachability or DNS resolution. Replace the example IP with the Mac's reserved LAN address. An ordinary lab DNS hostname also works when the supplied DNS resolver knows it. Do not assume Bonjour `.local` names work through nginx's configured unicast DNS resolver.

## Install on the Pi

1. Install 64-bit Raspberry Pi OS Desktop with labwc/Wayland. Configure network access, desktop autologin, time synchronization, and landscape display settings. Disable screen blanking. Enable SSH for maintenance.
2. Install prerequisites:

       sudo apt update
       sudo apt install nginx chromium python3 python3-evdev python3-venv ca-certificates

3. Copy `output/station-ui/` and `deploy/scanner-kiosk/` to the Pi, then install with the Mac's actual LAN origin and the Pi's actual LAN DNS resolver:

       sudo sh ~/scanner-kiosk/install.sh ~/station-ui http://192.168.1.50:8120 kiosk 192.168.1.1 ppe-door
       sudo reboot

The fifth argument is optional; omitting it keeps the station ID stored in the package. Repeat with the appropriate station ID for all three Pis. When `raspi-config` is present, the installer also sets Desktop Autologin for the kiosk user (`do_boot_behaviour B4`) and turns off screen blanking (`do_blanking 1`). Otherwise it prints those steps for you to do by hand. The installer writes the fixed station configuration and a systemd environment file. It validates file checksums and host settings before making changes.

If the older `deploy/pi` service occupies port 8080, stop its user service and disable its nginx site before installing this deployment. Run only one kiosk deployment. The installer detects that conflict and stops with instructions.

## Scanner input bridge

Chromium only receives a keyboard-wedge scanner's keystrokes while it has OS keyboard focus, and kiosk mode does not guarantee that: system dialogs, notifications, or a browser restart can take focus away. On the Pi, `input-bridge.py` therefore reads the scanner directly through Linux evdev. It takes an exclusive grab, so scans arrive whatever window has focus and are never typed into anything else.

Each completed scan goes to the kiosk page as a server-sent event on `/station/input/events`, which local nginx proxies to the bridge on 127.0.0.1:8766. The page applies exactly the same rules as keyboard capture:

- payload validation;
- database-health gating;
- rejection, not queuing, while a popup or pending request is open;
- the normal lookup → movement → green confirmation.

The bridge only delivers to pages connected at that moment and never replays scans. The page also ignores any scan more than 3 seconds old. The header shows **Scanner ready**, **Scanner not detected** (unplugged, or not found by name), or **Scanner service offline**.

If the bridge service stops, its grab is released and the scanner types into Chromium again. The page's keyboard listener remains attached as that fallback. While the bridge is connected, the watchdog no longer restarts Chromium merely for lost focus; it still does so for a hidden page or a missing capture listener.

The bridge runs as `fabric-scanner-input.service` under `fabric-scanner.target`. It auto-detects the first input device whose name contains `barcode`, `scanner`, `hidkeyboard` or `eyoyo` and that can send Enter. If that picks the wrong device, pin the scanner by its stable path in `/opt/fabric-scanner/scanner.env`; reinstalling never overwrites that file:

    /opt/fabric-scanner/input-env/bin/python /opt/fabric-scanner/bin/input-bridge.py list
    ls -l /dev/input/by-id/
    echo 'SCANNER_DEVICE=/dev/input/by-id/usb-…-event-kbd' | sudo tee -a /opt/fabric-scanner/scanner.env
    systemctl --user restart fabric-scanner-input.service

`curl http://127.0.0.1:8766/status` shows the detected scanner, NFC state, and how many kiosk pages are connected.

## Optional NFC tags

A station with a PN532 NFC hat (SPI mode) can also accept NTAG21x tags. Add `--nfc` before the other installer arguments:

       sudo sh ~/scanner-kiosk/install.sh --nfc ~/station-ui http://192.168.1.50:8120 kiosk 192.168.1.1 ppe-door

The input bridge then reads each tag's NDEF text record and sends the specimen ID through the same event stream as a barcode scan. Tags holding anything else appear on the kiosk as rejected scans. With `--nfc`, the installer:

- installs `nfc-requirements.txt` into `/opt/fabric-scanner/input-env` (this needs network access);
- adds the kiosk user to `spi` and `gpio`;
- enables SPI through `raspi-config`;
- sets `NFC=1` in `/opt/fabric-scanner/input.env`.

Rerunning without `--nfc` turns NFC off again.

Hat wiring: I1=H, I0=L, RSTPDN→D20, DIP 1–4 (SCK/MISO/MOSI/D4) ON, 5–8 OFF. To write a specimen ID onto a tag, stop the target and hold the tag on the coil:

       systemctl --user stop fabric-scanner.target
       /opt/fabric-scanner/input-env/bin/python /opt/fabric-scanner/bin/nfc_tags.py write 4d2a1c3b-9e57-4f60-8b1a-2f6c0d7e9a15
       /opt/fabric-scanner/input-env/bin/python /opt/fabric-scanner/bin/nfc_tags.py read
       systemctl --user start fabric-scanner.target

The ID can be the UUID or its 39-digit payload. The command verifies the tag by reading it back. The PN532 is 13.56 MHz HF/NFC and needs a tap within a few centimetres; it is not the passive UHF RFID considered in the design memo.

## Startup and recovery

Desktop autologin runs the labwc autostart entry. It imports the Wayland environment and starts fabric-scanner.target, which starts the supervised browser, watchdog, and input bridge. The browser waits only for local nginx, then opens the dedicated station page. Chromium's persistent profile retains station preferences and pending-request IDs across restarts.

Every two seconds, the page reports focus, visibility, whether the React scan listener is attached, and whether the input bridge stream is connected. No specimen data or database-health status appears in that heartbeat. After a 45-second startup grace period, more than 15 seconds without a healthy page triggers a browser restart. Database failure therefore leaves NOT AVAILABLE visible instead of restarting the browser repeatedly.

The installed URL is `http://127.0.0.1:8080/station.html?station=ppe-door#kiosk`. Local nginx and the watchdog bind to loopback. With the input bridge running, scans reach the page without browser focus. If the bridge is down, the browser must be the active application to receive the scanner's keyboard events, and the watchdog attempts recovery after persistent focus loss. Physical validation is still required for the actual Pi desktop/compositor.

Normal scanning is automatic in the persistent IN/OUT direction. No visible input box or ordinary Submit is required. Partial selection and early-checkout override use explicit popup actions. Scanner Enter never reaches those buttons: the bridge grabs the device, and the fallback keyboard listener consumes Enter. The printed Data Matrix payload is the specimen's 128-bit ID represented as 39 decimal characters, with Enter supplied separately by the scanner setting.

## Maintenance

From the kiosk user's graphical session:

    systemctl --user status fabric-scanner.target
    systemctl --user status fabric-scanner-browser.service fabric-scanner-watchdog.service fabric-scanner-input.service
    journalctl --user -u fabric-scanner-browser.service -n 50
    journalctl --user -u fabric-scanner-watchdog.service -n 50
    journalctl --user -u fabric-scanner-input.service -n 50
    sudo nginx -t

Stop the entire target during maintenance so the watchdog cannot reopen Chromium:

    systemctl --user stop fabric-scanner.target
    systemctl --user start fabric-scanner.target

To update, rebuild the frontend and station package, recopy it, rerun the installer, and restart the target. Do not erase the Chromium profile: it may contain an unresolved movement request. Use lab DNS or a reserved LAN address and test access from each Pi.

## Verification

Local checks:

    python3 -m unittest discover -s deploy/scanner-kiosk -p 'test_*.py' -v
    sh -n deploy/scanner-kiosk/install.sh
    sh -n deploy/scanner-kiosk/browser.sh
    node deploy/scanner-kiosk/check-package.mjs

The last command serves the generated local package on an ephemeral loopback port and opens headless Chromium. It checks dedicated routing, fixed station identity, offline readiness, heartbeat, local assets, reload, and zero offline movement submissions; it requires the frontend's installed Playwright dependency.

Physical acceptance remains: repeated cold boots, scanner detection by the input bridge (scanning while another window has focus), API-unavailable boot, focus loss, frozen/closed browser, USB unplug/replug, exact Data Matrix decoding from printed stock, popup scan rejection, interrupted-response recovery, and measured scan-to-confirmation latency. These provisioning files have not been run on a physical Pi.
