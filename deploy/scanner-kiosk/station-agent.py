#!/usr/bin/env python3
"""Report this Pi's health (CPU, memory, temperature, SD card, power, services) to the Mac.

Every 30 seconds the agent posts one JSON report through the station's own nginx,
which already proxies /api to the Mac host. It needs no host address of its own.
Pushing works even when the kiosk browser has crashed, which is when the report
matters most. Standard library only; every reading is best effort and null if
unavailable.
"""
import json
import logging
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

INTERVAL = 30
UNITS = ("fabric-scanner-browser.service", "fabric-scanner-input.service", "fabric-scanner-watchdog.service")
STATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# Raspberry Pi firmware get_throttled bits: https://www.raspberrypi.com/documentation/computers/os.html#get_throttled
THROTTLE_BITS = {0: "under_voltage", 1: "frequency_capped", 2: "throttled", 3: "soft_temperature_limit",
                 16: "under_voltage_occurred", 17: "frequency_capped_occurred", 18: "throttled_occurred",
                 19: "soft_temperature_limit_occurred"}


def read(root, path):
    try:
        return (root / path.lstrip("/")).read_text(errors="replace")
    except OSError:
        return None


def run(args, timeout=3):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def cpu_ticks(root):
    line = (read(root, "/proc/stat") or "").split("\n", 1)[0].split()
    try:
        values = [int(value) for value in line[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values) - idle, sum(values)
    except (ValueError, IndexError):
        return None


def cpu_percent(before, after):
    if not before or not after or after[1] <= before[1] or after[0] < before[0]:
        return None
    return round(100 * (after[0] - before[0]) / (after[1] - before[1]), 1)


def meminfo(root):
    values = {}
    for line in (read(root, "/proc/meminfo") or "").splitlines():
        name, _, rest = line.partition(":")
        if rest.split():
            try:
                values[name] = int(rest.split()[0]) * 1024
            except ValueError:
                pass
    total = values.get("MemTotal")
    if not total:
        return None
    available = values.get("MemAvailable", values.get("MemFree", 0))
    return {"total_bytes": total, "used_bytes": total - available, "percent": round(100 * (total - available) / total, 1),
            "swap_used_bytes": max(0, values.get("SwapTotal", 0) - values.get("SwapFree", 0))}


def disk(root):
    try:
        stats = os.statvfs(root)
    except OSError:
        return None
    total, free = stats.f_blocks * stats.f_frsize, stats.f_bavail * stats.f_frsize
    if not total:
        return None
    readonly = None
    for line in (read(root, "/proc/mounts") or "").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] == "/":
            readonly = "ro" in parts[3].split(",")  # the last "/" entry is the active mount
    return {"total_bytes": total, "free_bytes": free, "percent": round(100 * (total - free) / total, 1), "read_only": readonly}


def temperature(root):
    value = read(root, "/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(value.strip()) / 1000, 1) if value else None
    except ValueError:
        return None


def throttled(root):
    """Firmware power/thermal flags: newer kernels expose a file; otherwise ask vcgencmd."""
    raw = read(root, "/sys/devices/platform/soc/soc:firmware/get_throttled")
    if raw is None and root == Path("/"):
        output = run(["vcgencmd", "get_throttled"])
        found = re.search(r"0x[0-9a-fA-F]+", output or "")
        raw = found.group(0) if found else None
    if raw is None:
        return None
    try:
        value = int(raw.strip(), 16)
    except ValueError:
        return None
    return {"value": value, "flags": [name for bit, name in THROTTLE_BITS.items() if value & (1 << bit)]}


def network(root):
    interface = None
    for line in (read(root, "/proc/net/route") or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) > 1 and parts[1] == "00000000":
            interface = parts[0]
            break
    signal = None
    for line in (read(root, "/proc/net/wireless") or "").splitlines()[2:]:
        parts = line.split()
        if parts and parts[0].rstrip(":") == interface and len(parts) > 3:
            try:
                signal = int(float(parts[3].rstrip(".")))
            except ValueError:
                pass
    return {"interface": interface, "wifi_signal_dbm": signal,
            "wired": bool(interface and not interface.startswith("wl"))}


def services(root):
    if root != Path("/"):
        return None
    result = {}
    for unit in UNITS:
        output = run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "NRestarts"])
        if output is None:
            return None
        fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        restarts = fields.get("NRestarts", "")
        result[unit.removeprefix("fabric-scanner-").removesuffix(".service")] = {
            "state": fields.get("ActiveState", "unknown"), "restarts": int(restarts) if restarts.isdigit() else None}
    return result


def input_status(root):
    if root != Path("/"):
        return None
    try:
        with urllib.request.urlopen("http://127.0.0.1:8766/status", timeout=2) as response:
            value = json.loads(response.read(16384))
        return {key: value.get(key) for key in ("scanner", "scanner_name", "scanner_error", "nfc")}
    except (OSError, ValueError, AttributeError):
        return None


def ntp_synchronized(root):
    if root != Path("/"):
        return None
    output = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    return None if output is None else output.strip() == "yes"


def package_built_at(root):
    try:
        return json.loads(read(root, "/opt/fabric-scanner/ui/station-package.json") or "")["built_at"]
    except (ValueError, KeyError, TypeError):
        return None


def package_version(root):
    try:
        release = json.loads(read(root, "/opt/fabric-scanner/ui/station-package.json") or "").get("release")
    except (ValueError, AttributeError):
        return None
    return release[:64] if isinstance(release, str) else None


def os_name(root):
    found = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', read(root, "/etc/os-release") or "", re.MULTILINE)
    return found.group(1) if found else platform.system()


def report(root, previous_ticks):
    ticks = cpu_ticks(root)
    uptime = (read(root, "/proc/uptime") or "").split()
    load = (read(root, "/proc/loadavg") or "").split()
    try:
        load_average = [float(value) for value in load[:3]] if len(load) >= 3 else None
    except ValueError:
        load_average = None
    return ticks, {
        "agent_version": 1,
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostname": platform.node()[:128] or None,
        "model": (read(root, "/proc/device-tree/model") or "").strip("\x00\n ")[:128] or None,
        "os": os_name(root)[:128], "kernel": platform.release()[:64],
        "uptime_seconds": int(float(uptime[0])) if uptime else None,
        "cpu_percent": cpu_percent(previous_ticks, ticks), "cores": os.cpu_count(), "load_average": load_average,
        "memory": meminfo(root), "disk": disk(root), "temperature_c": temperature(root), "throttled": throttled(root),
        "network": network(root), "ntp_synchronized": ntp_synchronized(root), "services": services(root),
        "input": input_status(root), "package_built_at": package_built_at(root),
        "package_version": package_version(root),
    }


def send(url, payload, timeout=5):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def station_id(environment=os.environ):
    value = environment.get("STATION_ID") or ""
    if not value:  # older installs only wrote KIOSK_URL=...?station=<id>#kiosk
        found = re.search(r"[?&]station=([^&#]+)", environment.get("KIOSK_URL", ""))
        value = found.group(1) if found else ""
    if not STATION_PATTERN.fullmatch(value):
        raise SystemExit("STATION_ID is not set; reinstall the station package.")
    return value


def main(argv=sys.argv[1:]):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    once = "--once" in argv
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else Path("/")
    base = argv[argv.index("--url") + 1] if "--url" in argv else os.environ.get("AGENT_API_BASE", "http://127.0.0.1:8080")
    url = base.rstrip("/") + "/api/stations/" + station_id() + "/device"
    ticks, failures = cpu_ticks(root), 0
    if not once:
        time.sleep(2)  # a first CPU interval, so the first report has a CPU value
    while True:
        ticks, payload = report(root, ticks)
        try:
            send(url, payload)
            if failures:
                logging.info("Reporting to the host again after %s failed attempts", failures)
            failures = 0
        except (OSError, urllib.error.URLError) as error:
            failures += 1
            if failures in {1, 10} or failures % 120 == 0:  # do not fill the journal while the Mac is off
                logging.warning("Could not report to the host (%s attempts): %s", failures, error)
        if once:
            return 0 if failures == 0 else 1
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
