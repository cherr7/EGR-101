# System health

The **System health** tab (`#health`) shows the live state of everything the tracker depends on. It refreshes every 10 seconds, and the sidebar badge shows the warning count every 30 seconds.

| Area | What it shows | Source |
|---|---|---|
| Host Mac | storage used/free, RAM used and memory pressure, CPU % and load, 1-hour CPU/RAM trend, power (AC or battery), the sleep setting, thermal throttling, uptime, LAN IP | `host_statistics` (ctypes), `sysctl`, `vm_stat`, `pmset`, `shutil.disk_usage` |
| Tracker app | how long the server has run, database size, PostgreSQL version and connections, record counts, log size, backup count, size and next run | PostgreSQL, the backup settings, the state folder |
| Scan stations | ready / no scanner / offline / not connected, scanner input mode, Pi IP, last heartbeat | the in-memory heartbeats (`stations.py`) |
| Label printer | CUPS queues, idle/printing/paused, alerts, device URI, jobs waiting, label jobs in the last 24 h | `lpstat`, the `print_jobs` table |

API: `GET /api/system/health`, cached for 4 s. The code is in `backend/app/system_health.py`. It adds no new Python dependencies. On Linux/Docker it falls back to `/proc`, and the macOS-only fields come back as null.

### Issue rules

| Level | Condition |
|---|---|
| Critical | disk ≥ 95% used or < 5 GB free · running on battery at ≤ 25% · memory pressure critical · database unreachable · last scheduled backup failed |
| Warning | disk ≥ 85% used or < 20 GB free · on battery · system sleep enabled · thermal throttling · memory pressure warning · CPU averages ≥ 85% over about 5 minutes · station offline or scanner not ready · no printer queue, queue paused, a printer alert, or ≥ 3 jobs waiting · a failed or unconfirmed label job in the last 24 h · backups paused or overdue, none yet, or the newest > 36 h old · logs > 1 GB |
| Info | a station has not sent a heartbeat since the host started |

"Free" storage excludes purgeable space, so Finder can show a slightly larger number.

## Raspberry Pi stats

Each Pi runs `deploy/scanner-kiosk/station-agent.py` as `fabric-scanner-agent.service`, part of `fabric-scanner.target`. Every 30 s it posts a report to `POST /api/stations/<STATION_ID>/device` through the Pi's own nginx `/api` proxy, so it needs no host address and keeps working when Chromium has crashed. It uses only the standard library, and every reading is null if unavailable.

| Reading | Source on the Pi |
|---|---|
| CPU %, load, cores, uptime | `/proc/stat`, `/proc/loadavg`, `/proc/uptime` |
| RAM and swap | `/proc/meminfo` |
| SoC temperature | `/sys/class/thermal/thermal_zone0/temp` |
| Under-voltage / throttling (now and since boot) | `/sys/devices/platform/soc/soc:firmware/get_throttled`, else `vcgencmd get_throttled` |
| SD-card free space, root mounted read-only | `statvfs("/")`, `/proc/mounts` |
| Default interface, Wi-Fi signal | `/proc/net/route`, `/proc/net/wireless` |
| Clock offset from the Mac, NTP sync | the report's `sent_at`, `timedatectl` |
| Browser / input / watchdog service state and restarts | `systemctl --user show` |
| Scanner device name and errors | the input bridge's `/status` |
| Model, OS, kernel, package build time | device tree, `/etc/os-release`, `station-package.json` |

The Mac validates each report (`DeviceReport` in `stations.py`, unknown fields ignored) and keeps the latest report plus one hour of CPU, RAM and temperature in memory. A report is stale after 90 s. The Scan stations table shows CPU, RAM, temperature and SD free, and **Details** opens the rest with trend charts.

### Pi issue rules

| Level | Condition |
|---|---|
| Critical | ≥ 80 °C · under-voltage now · SD card read-only · SD ≥ 95% used or < 500 MB free |
| Warning | ≥ 70 °C · under-voltage since boot · throttled · SD ≥ 90% or < 1 GB free · RAM ≥ 90% · a station service not active · clock off by > 2 min · Wi-Fi weaker than −75 dBm · the agent stopped reporting while the page is live · the Pi reports but the kiosk page has stopped |
| Info | restarted in the last 10 min · the page is live but the Pi runs an older package with no agent |

### Rollout

Rebuild the package (`scripts/build-station.py`). On each Pi, copy the package and `deploy/scanner-kiosk`, rerun `install.sh` with the same arguments, and reboot. The installer now also writes `STATION_ID` to `/opt/fabric-scanner/station.env`. Older installs fall back to the station in `KIOSK_URL`. Check a Pi with `journalctl --user -u fabric-scanner-agent.service -n 20`.

**Security:** the app has no login, so anyone on the LAN could post fake stats. The risk is low because the data is display-only and validated. If it matters later, add a per-station token that `install.sh` writes and the endpoint checks.

## Other ideas

- **Alerts away from the screen**: email or a Teams/Slack webhook when an issue turns critical (on battery, disk full, station offline for 10+ minutes, backup failed).
- **When the host itself is down**, this page cannot load. The Pis already show "Offline". A LaunchAgent/LaunchDaemon (declined for now) or an outside check (one Pi pinging `/api/health` and alerting) would cover it.
- **Saved history**: keep a daily snapshot of disk/DB size in the database to show growth over the semester.
- **Mac housekeeping**: Time Machine latest backup (`tmutil latestbackup`), pending macOS updates (`softwareupdate -l`), and battery health (cycle count and condition) for a MacBook that stays plugged in.
- **Printer depth**: Brother's CUPS driver reports few states. Media-empty or cover-open detection would need Brother's status protocol, which is likely not worth the effort.
