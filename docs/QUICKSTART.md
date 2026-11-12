# Quickstart

Short steps for every piece of hardware. Details are in the linked guides.

**What runs where**

| Device | Runs | Needs |
| --- | --- | --- |
| MacBook Neo | Database, server (port 8120), staff app, printing | Power, lab network, the printer |
| Raspberry Pi 5 ×3 | Full-screen room scanner, one per room | Power, lab network, Eyoyo scanner, touch display |
| Brother QL-810W | Labels | Power, plus a USB cable to the Mac **or** the same Wi-Fi as the Mac |

The Pis and staff browsers only talk to the Mac. They never talk to the printer or to the database directly.

---

## 1. Mac: first-time setup

Do this once, on the MacBook Neo that will host everything.

1. Install [Homebrew](https://brew.sh), then in Terminal:

   ```bash
   brew install python@3.14 postgresql@18 node
   ```

2. Get the code and run setup from the project folder:

   ```bash
   git clone https://github.com/cherr7/EGR-101.git && cd EGR-101
   deploy/macos/setup.sh
   ```

   This installs the Python runtime, builds the web app and creates `output/macOS/Fabric Conditioning.app`.

3. Double-click **output/macOS/Fabric Conditioning.app**. It starts the database and server and opens **http://127.0.0.1:8120**. (You can drag the app into Applications; your data isn't stored in it.)
4. When macOS asks whether Python may accept incoming connections, click **Allow**. Otherwise the Pis can't reach the Mac.
5. Keep it awake: **System Settings → Battery → Options → "Prevent automatic sleeping on power adapter when the display is off"** on. Keep it plugged in. Locking the screen is fine; sleeping is not.
6. Find the Mac's address for the Pis:

   ```bash
   ipconfig getifaddr en0
   ```

   Ask IT to **reserve that address** (DHCP reservation) so it never changes. The Pis are configured with it.

7. Check from another computer: open `http://MAC-ADDRESS:8120`. You should see the staff app.

Optional: start automatically after a reboot, even before anyone logs in. See [MAC_HOST.md → Start automatically](MAC_HOST.md#start-automatically-before-interactive-login).

## 2. Mac: everyday

- **Start:** double-click the app. If it's already running, it just opens the browser.
- **Status / stop** (from the project folder):

  ```bash
  python3 deploy/macos/host.py status
  ```

  ```bash
  python3 deploy/macos/host.py stop
  ```

- **Update the code:** `git pull`, then `deploy/macos/setup.sh` again. Data is kept.
- **Logs:** `~/Library/Logs/Fabric Conditioning/launcher-error.log` and `~/Library/Application Support/Fabric Conditioning/logs/`.
- **If PostgreSQL won't start** with "postmaster became multithreaded during startup": run `export LC_ALL=en_US.UTF-8`, then start again.

## 3. Printer: connect to the Mac

Full guide, including Wi-Fi and roll details: [PRINTER_AND_LABELS.md](PRINTER_AND_LABELS.md).

1. Plug the printer's power into the wall (always needed; it has no battery) and turn it on.
2. Make sure the green **Editor Lite** light is **off**; hold its button until it goes off.
3. Connect it, **either:**
   - **USB (easiest):** USB-B on the printer to USB-C on the Mac.
   - **Wi-Fi:** printer and Mac on the same network. Use WPS (hold the printer's Wi-Fi button ~5 s after pressing the router's WPS button), or Brother's wireless setup tool over USB if there's no WPS.
4. **System Settings → Printers & Scanners → Add Printer…** → pick **Brother QL-810W** → Add. AirPrint is fine to start.
5. Check: `lpstat -p` lists it, and the app's Print dialog shows it.
6. Load a **62 mm** roll (DK-1209 for real use; DK-2251 continuous works for testing). Print one label from the app and scan it.

## 4. Pi station: first-time setup

Repeat for each room: `ppe-door`, `shared-door`, `footwear-door`.

1. With [Raspberry Pi Imager](https://www.raspberrypi.com/software/), flash **Raspberry Pi OS (64-bit) with desktop** (not Lite). In Imager's settings, set a username and password, the Wi-Fi network (or use Ethernet), and **enable SSH**.
2. Connect the Touch Display 2, the Eyoyo scanner (USB) and power. Boot it and finish the desktop setup.
3. Set the display to **landscape (1280 × 720)**: Raspberry Pi menu → Preferences → Screen Configuration.
4. Find the Pi's address (on the Pi: `hostname -I`) and ask IT to reserve it.
5. On the **Mac**, with the app running, from the project folder:

   ```bash
   bash scripts/deploy-pi.sh PI-USER@PI-ADDRESS shared-door
   ```

   This builds the scanner package, copies it to the Pi, installs everything (nginx, Chromium kiosk, scanner input service, optional NFC), checks the Pi can reach the Mac, and reboots the Pi. It asks for the Pi's password once.

6. After the reboot the Pi opens straight into the full-screen scanner. The header should show **Online** in green.
7. Scan a label. If nothing happens, pin the scanner (step 8).
8. **Pin the scanner** (only if needed). Over SSH on the Pi:

   ```bash
   ls -l /dev/input/by-id/
   ```

   Find the line ending `-event-kbd` for the Eyoyo (it may be called `USB HIDKeyboard`). Put it in `/opt/fabric-scanner/scanner.env` as `SCANNER_DEVICE=/dev/input/by-id/…-event-kbd`, then:

   ```bash
   systemctl --user restart fabric-scanner-input.service
   ```

The deploy script uses this Mac's current address (`en0`). If the Mac's address changes later, rerun the script for each Pi. Manual install steps and all options: [PI_STATION.md](PI_STATION.md).

## 5. Pi station: everyday

- It starts by itself on power-up. No keyboard or mouse needed.
- **Offline** (red) means it can't reach the Mac. Check the Mac is awake and the app is running. Nothing is recorded while offline.
- **Connect scanner** means the Eyoyo isn't detected. Replug it.
- **Restart the station software** over SSH: `systemctl --user restart fabric-scanner.target`, or just reboot the Pi.
- **Update:** rerun `bash scripts/deploy-pi.sh PI-USER@PI-ADDRESS ROOM` from the Mac.

## 6. Daily lab use

1. **Specimens → Prepare / import:** upload the prep sheet, review, fill in what's missing, **Register**.
2. **Preview & print** the labels and stick one on each piece.
3. At the room's Pi: tap **IN**, scan any label in the stack, wait for green.
4. After 24 hours, the **Stacks** page shows the stack as ready. At the Pi: tap **OUT**, scan.
5. Mistakes: **Undo** on the Pi for the last scan, or fix anything on the **Transactions** page.

Full workflow: [STAFF_GUIDE.md](STAFF_GUIDE.md).

## 7. Backups

The app makes a backup every day at 2 a.m. and keeps 30 days on the Mac. Once a week, **Backups → Create backup now → Download** and keep the file somewhere off the Mac. Details: [BACKUPS.md](BACKUPS.md).

## Quick fixes

| Problem | Fix |
| --- | --- |
| Pi shows Offline | Mac asleep, app not running, firewall blocked, or Mac address changed. Open `http://MAC-ADDRESS:8120/api/health` from another computer to check. |
| Pi scans do nothing | Pin the scanner (section 4, step 8). Check the scanner is plugged into the **Pi**, not the Mac. |
| Printer missing in the app | `lpstat -p` on the Mac. If it's empty, add the printer again (section 3). Check Editor Lite is off. |
| Labels wrong size or not cut | Use a 62 mm roll. If it still happens, install Brother's QL-810W driver and re-add the printer with it ([PRINTER_AND_LABELS.md](PRINTER_AND_LABELS.md#can-i-just-use-airprint-if-theyre-on-the-same-wi-fi)). |
| App won't start | Check `~/Library/Logs/Fabric Conditioning/launcher-error.log`; try `export LC_ALL=en_US.UTF-8` then `python3 deploy/macos/host.py start`. |
