# Raspberry Pi station

These provisioning files target **64-bit Raspberry Pi OS Desktop with labwc/Wayland** and a Pi 5 touchscreen. Hardware boot, scanner, and print validation are still pending. The UI is stored on the Pi; only `/api` requests go to the central server. A cold boot without that server still loads the interface and displays **NOT AVAILABLE**. No sample movements are queued offline.

1. Use Raspberry Pi Imager to install Raspberry Pi OS Desktop, configure the desktop user, networking, and SSH. In `sudo raspi-config`, enable **Desktop Autologin** and disable **Screen Blanking** for the dedicated station.
2. Install prerequisites on the Pi: `sudo apt update` then `sudo apt install nginx chromium ca-certificates python3`.
3. On the development computer, run `npm ci && npm run build` in `frontend/`. Copy `frontend/dist/` and `deploy/pi/` to the Pi.
4. Run the installer on the Pi, replacing the API host, desktop user, and LAN DNS resolver:

   ```sh
   sudo ./deploy/pi/install.sh ./frontend/dist http://192.168.1.50:8080 labuser 192.168.1.1
   sudo reboot
   ```

   The central origin is the Compose frontend on port 8080, which already proxies `/api`. It can also be the central backend on port 8000 if intentionally exposed to the Pi. The origin must have no path. An IP address avoids relying on central-host DNS; hostname origins use the configured LAN DNS resolver at request time. Existing nginx port-80 sites remain available; this station uses local port 8080.

The labwc autostart snippet imports the graphical session environment and starts a user-level systemd service. `Restart=always` restarts Chromium after an exit or crash. The service retains its browser profile, including the station selection and unresolved request IDs. It does not use `--no-sandbox` or disable certificate verification.

After changing the compiled UI, rerun the installer and reboot. For browser/service diagnosis from the desktop user session:

```sh
systemctl --user status conditioning-kiosk.service
journalctl --user -u conditioning-kiosk.service -n 100
sudo nginx -t
```

Physical acceptance: select the correct room station; disconnect networking and power-cycle the Pi; confirm the local screen reaches **NOT AVAILABLE**, scans cannot record movements, and no green receipt appears. Reconnect and verify health recovery before scanning. Kill Chromium and verify restart. Test the actual USB scanner in keyboard/HID mode with an Enter suffix. Print labels from a staff PC and confirm the selected label stock and actual printer read reliably; compatibility with the unspecified old DYMO model is not established.

The labwc startup approach follows [Raspberry Pi's official kiosk tutorial](https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/). The local-server and supervised-browser arrangement is specific to this prototype and needs the physical checks above.
