"""System health: the host Mac, database, printer, backups and scan stations. Every probe is best effort."""
from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text

from . import backups
from . import version as app_version
from .db import get_db
from .models import AuditEvent, PrintJob, Specimen, Station, utcnow
from .printing import printer_inventory
from .stations import device_status, lan_ip, mdns_name, station_status

router = APIRouter(prefix="/api/system", tags=["System health"])

STARTED = time.time()
SAMPLE_SECONDS = 15
HISTORY = deque(maxlen=240)  # one hour of CPU / memory samples
_LOCK = threading.Lock()
_STATE = {"ticks": None, "sampler": None, "report": None, "report_at": 0.0}
_MACH = {}

# Thresholds for the issue list. Disk "free" excludes purgeable space macOS can reclaim.
DISK_WARN_PERCENT, DISK_CRIT_PERCENT = 85, 95
DISK_WARN_FREE, DISK_CRIT_FREE = 20 * 2**30, 5 * 2**30
CPU_WARN_PERCENT = 85
BATTERY_CRIT_PERCENT = 25
LOG_WARN_BYTES = 1 * 2**30
BACKUP_STALE = timedelta(hours=36)
PI_TEMP_WARN, PI_TEMP_CRIT = 70, 80  # the Pi firmware starts throttling at 80-85 °C
PI_DISK_WARN_PERCENT, PI_DISK_CRIT_PERCENT = 90, 95
PI_DISK_WARN_FREE, PI_DISK_CRIT_FREE = 1 * 2**30, 500 * 2**20
PI_MEMORY_WARN = 90
PI_WIFI_WEAK_DBM = -75
PI_CLOCK_SKEW_SECONDS = 120
PI_RECENT_BOOT_SECONDS = 600


def run(args, timeout=3):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"}).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def sysctl(*names):
    if sys.platform != "darwin":
        return {}
    values = {}
    for name in names:  # one call per name: an unknown name makes sysctl drop the rest
        output = run(["/usr/sbin/sysctl", "-n", name]).strip()
        if output:
            values[name] = output
    return values


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def state_dir():
    return Path(os.environ.get("FABRIC_STATE_DIR", str(Path.home() / "Library/Application Support/Fabric Conditioning")))


def iso_from_epoch(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat() if seconds else None


# ---------- CPU and memory ----------

def cpu_ticks():
    """Cumulative (busy, total) CPU ticks across all cores, or None."""
    if sys.platform == "darwin":
        try:
            if not _MACH:
                lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
                lib.mach_host_self.restype = ctypes.c_uint32
                lib.host_statistics.argtypes = [ctypes.c_uint32, ctypes.c_int,
                                                ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
                _MACH.update(lib=lib, host=lib.mach_host_self())
            info, count = (ctypes.c_uint32 * 4)(), ctypes.c_uint32(4)
            if _MACH["lib"].host_statistics(_MACH["host"], 3, info, ctypes.byref(count)) != 0:  # HOST_CPU_LOAD_INFO
                return None
            user, system, idle, nice = info
            return user + system + nice, user + system + idle + nice
        except (OSError, AttributeError):
            return None
    try:
        values = [int(v) for v in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values) - idle, sum(values)
    except (OSError, ValueError, IndexError):
        return None


def cpu_percent(before, after):
    if not before or not after or after[1] <= before[1] or after[0] < before[0]:
        return None  # first sample, or the 32-bit tick counters wrapped
    return round(100 * (after[0] - before[0]) / (after[1] - before[1]), 1)


def memory():
    """Used memory the way Activity Monitor counts it (app + wired + compressed)."""
    if sys.platform == "darwin":
        values = sysctl("hw.memsize", "kern.memorystatus_vm_pressure_level", "kern.memorystatus_level", "vm.swapusage")
        total = as_int(values.get("hw.memsize"))
        output = run(["/usr/bin/vm_stat"])
        page = re.search(r"page size of (\d+)", output)
        pages = {name.strip('"').lower(): int(count)
                 for name, count in re.findall(r'^("?[^:\n]+"?):\s+(\d+)\.', output, re.MULTILINE)}
        if not total or not page or "anonymous pages" not in pages:
            return None
        size = int(page.group(1))
        app = pages["anonymous pages"] - pages.get("pages purgeable", 0)
        used = (app + pages.get("pages wired down", 0) + pages.get("pages occupied by compressor", 0)) * size
        cached = (pages.get("file-backed pages", 0) + pages.get("pages purgeable", 0)) * size
        level = as_int(values.get("kern.memorystatus_vm_pressure_level"))
        swap = re.search(r"used = ([\d.]+)([KMG])", values.get("vm.swapusage", ""))
        swap_used = int(float(swap.group(1)) * {"K": 2**10, "M": 2**20, "G": 2**30}[swap.group(2)]) if swap else None
        return {"total_bytes": total, "used_bytes": min(used, total), "cached_bytes": cached,
                "percent": round(100 * min(used, total) / total, 1),
                "pressure": {1: "normal", 2: "warning", 4: "critical"}.get(level, "unknown"),
                "available_percent": as_int(values.get("kern.memorystatus_level")), "swap_used_bytes": swap_used}
    try:
        info = {key: int(value.split()[0]) * 1024 for key, value in
                (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)}
        total, available = info["MemTotal"], info.get("MemAvailable", info.get("MemFree", 0))
        return {"total_bytes": total, "used_bytes": total - available, "cached_bytes": info.get("Cached"),
                "percent": round(100 * (total - available) / total, 1), "pressure": "unknown",
                "available_percent": round(100 * available / total), "swap_used_bytes": info.get("SwapTotal", 0) - info.get("SwapFree", 0)}
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        return None


def sample():
    ticks = cpu_ticks()
    with _LOCK:
        previous, _STATE["ticks"] = _STATE["ticks"], ticks
    cpu, mem = cpu_percent(previous, ticks), memory()
    if cpu is not None or mem:
        HISTORY.append({"at": utcnow().isoformat(), "cpu": cpu, "memory": mem["percent"] if mem else None})
    return cpu, mem


def ensure_sampler():
    """Start the background sampler once per process (lifespan and first request both call this)."""
    with _LOCK:
        if _STATE["sampler"] and _STATE["sampler"].is_alive():
            return

        def loop():
            while True:
                try:
                    sample()
                except Exception:  # a probe failure must never stop the history
                    pass
                time.sleep(SAMPLE_SECONDS)

        _STATE["sampler"] = threading.Thread(target=loop, name="system-health-sampler", daemon=True)
        _STATE["sampler"].start()


def current_cpu():
    recent = [row for row in list(HISTORY)[-1:] if row["cpu"] is not None
              and utcnow() - datetime.fromisoformat(row["at"]) < timedelta(seconds=SAMPLE_SECONDS * 2)]
    if recent:
        return recent[0]["cpu"]
    before = cpu_ticks()
    time.sleep(0.4)
    return cpu_percent(before, cpu_ticks())


# ---------- Host facts ----------

def disk(path: Path, label: str):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"label": label, "path": str(path), "total_bytes": usage.total, "used_bytes": usage.total - usage.free,
            "free_bytes": usage.free, "percent": round(100 * (usage.total - usage.free) / usage.total, 1) if usage.total else None}


def folder_bytes(path: Path, pattern="*"):
    total = 0
    try:
        for item in path.rglob(pattern):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                pass
    except OSError:
        return None
    return total


def boot_time():
    if sys.platform == "darwin":
        found = re.search(r"sec = (\d+)", sysctl("kern.boottime").get("kern.boottime", ""))
        return int(found.group(1)) if found else None
    try:
        return time.time() - float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def power():
    if sys.platform != "darwin":
        return None
    battery = run(["/usr/bin/pmset", "-g", "batt"])
    source = re.search(r"drawing from '([^']+)'", battery)
    level = re.search(r"(\d+)%;\s*([^;]+);\s*([^\s;]+)", battery)
    settings = run(["/usr/bin/pmset", "-g"])
    sleep = re.search(r"^\s*sleep\s+(\d+)(.*)$", settings, re.MULTILINE)
    display = re.search(r"^\s*displaysleep\s+(\d+)", settings, re.MULTILINE)
    thermal = run(["/usr/bin/pmset", "-g", "therm"])
    warning = None
    if thermal and "No thermal warning level has been recorded" not in thermal:
        limit = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", thermal)
        level_line = re.search(r"[Tt]hermal warning level[^\n]*", thermal)
        warning = (f"CPU limited to {limit.group(1)}%" if limit and limit.group(1) != "100"
                   else level_line.group(0).strip() if level_line else None)
    return {"source": source.group(1) if source else None,
            "on_battery": bool(source and "Battery" in source.group(1)),
            "has_battery": "InternalBattery" in battery,
            "battery_percent": int(level.group(1)) if level else None,
            "battery_state": level.group(2).strip() if level else None,
            "time_remaining": level.group(3) if level and ":" in level.group(3) else None,
            "sleep_minutes": int(sleep.group(1)) if sleep else None,
            "sleep_note": sleep.group(2).strip(" ()") if sleep and sleep.group(2).strip() else None,
            "display_sleep_minutes": int(display.group(1)) if display else None,
            "thermal_warning": warning}


def host_facts():
    values = sysctl("hw.model", "machdep.cpu.brand_string")
    interface = re.search(r"interface:\s*(\S+)", run(["/sbin/route", "-n", "get", "default"])) if sys.platform == "darwin" else None
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {"hostname": mdns_name(), "ip": lan_ip(), "network_interface": interface.group(1) if interface else None,
            "model": values.get("hw.model"), "chip": values.get("machdep.cpu.brand_string") or platform.processor() or None,
            "os": f"macOS {platform.mac_ver()[0]}" if sys.platform == "darwin" else f"{platform.system()} {platform.release()}",
            "cores": os.cpu_count(), "load_average": [round(value, 2) for value in load] if load else None,
            "booted_at": iso_from_epoch(boot_time()), "python": platform.python_version()}


# ---------- Application, devices ----------

def database_facts(db):
    try:
        row = db.execute(text("SELECT pg_database_size(current_database()), current_setting('server_version'), "
                              "pg_postmaster_start_time(), (SELECT count(*) FROM pg_stat_activity "
                              "WHERE datname = current_database())")).one()
        return {"connected": True, "size_bytes": row[0], "version": row[1],
                "started_at": row[2].isoformat() if row[2] else None, "connections": row[3],
                "specimens": db.scalar(select(func.count()).select_from(Specimen)),
                "audit_events": db.scalar(select(func.count()).select_from(AuditEvent))}
    except Exception:
        db.rollback()
        return {"connected": False}


def printer_facts(db):
    inventory = printer_inventory()
    lpstat = shutil.which("lpstat")
    queues = {item["name"]: {**item, "state": "unknown", "alerts": None, "device": None, "jobs_waiting": 0}
              for item in inventory["printers"]}
    if lpstat:
        for block in re.split(r"(?m)^(?=printer )", run([lpstat, "-l", "-p"])):
            found = re.match(r"printer (\S+) (now printing|is idle|disabled)", block)
            if not found:
                continue
            row = queues.setdefault(found.group(1), {"name": found.group(1), "is_default": False,
                                                     "state": "unknown", "alerts": None, "device": None, "jobs_waiting": 0})
            row["state"] = {"now printing": "printing", "is idle": "idle", "disabled": "disabled"}[found.group(2)]
            alerts = re.search(r"Alerts:\s*(.+)", block)
            row["alerts"] = alerts.group(1).strip() if alerts and alerts.group(1).strip() != "none" else None
        for name, uri in re.findall(r"device for (\S+):\s*(\S+)", run([lpstat, "-v"])):
            if name in queues:
                queues[name]["device"] = re.sub(r"\?.*$", "", uri)
        for line in run([lpstat, "-o"]).splitlines():
            name = line.split(None, 1)[0].rsplit("-", 1)[0] if line.strip() else None
            if name in queues:
                queues[name]["jobs_waiting"] += 1
    since = utcnow() - timedelta(days=1)
    try:
        counts = dict(db.execute(select(PrintJob.status, func.count()).where(PrintJob.created_at >= since)
                                 .group_by(PrintJob.status)).all())
        last = db.scalars(select(PrintJob).order_by(PrintJob.created_at.desc()).limit(1)).first()
    except Exception:
        db.rollback()
        counts, last = {}, None
    return {"cups_available": bool(lpstat), "configured_queue": inventory["configured_queue"], "message": inventory["message"],
            "queues": sorted(queues.values(), key=lambda row: row["name"]),
            "jobs_last_24h": counts,
            "last_job": {"status": last.status, "queue": last.printer_queue, "created_at": last.created_at.isoformat(),
                         "error": last.error} if last else None}


def backup_facts(request):
    try:
        listing = backups.list_backups(request)
    except Exception:
        return {"available": False}
    latest = listing["items"][0] if listing["items"] else None
    return {"available": True, "count": len(listing["items"]), "total_bytes": listing["storage"]["total_bytes"],
            "folder": listing["storage"]["folder"], "latest_at": latest["created_at"] if latest else None,
            "latest_verified": latest.get("verified") if latest else None, **listing["scheduler"]}


# ---------- Issues ----------

def gib(value):
    return f"{value / 2**30:.1f} GB"


def find_issues(report):
    issues = []

    def add(level, area, title, detail=""):
        issues.append({"level": level, "area": area, "title": title, "detail": detail})

    for volume in report["storage"]:
        free, percent = volume["free_bytes"], volume["percent"] or 0
        if free < DISK_CRIT_FREE or percent >= DISK_CRIT_PERCENT:
            add("critical", "Host Mac", f"{volume['label']} is almost full", f"{gib(free)} free.")
        elif free < DISK_WARN_FREE or percent >= DISK_WARN_PERCENT:
            add("warning", "Host Mac", f"{volume['label']} is getting full", f"{gib(free)} free.")
    mem = report["memory"]
    if mem and mem["pressure"] in {"warning", "critical"}:
        add("critical" if mem["pressure"] == "critical" else "warning", "Host Mac", "Memory is low", "Quit other apps.")
    recent = [row["cpu"] for row in list(HISTORY)[-20:] if row["cpu"] is not None]  # about 5 minutes
    if len(recent) >= 8 and sum(recent) / len(recent) >= CPU_WARN_PERCENT:
        add("warning", "Host Mac", "CPU busy", f"{sum(recent) / len(recent):.0f}% for 5 minutes.")
    power_info = report["power"]
    if power_info:
        if power_info["on_battery"]:
            low = (power_info["battery_percent"] or 100) <= BATTERY_CRIT_PERCENT
            add("critical" if low else "warning", "Host Mac", "Mac is on battery",
                f"{power_info['battery_percent']}% left. Plug in the charger.")
        if power_info["sleep_minutes"]:
            add("warning", "Host Mac", f"Mac sleeps after {power_info['sleep_minutes']} min",
                "Stations can't scan while it sleeps. Set sleep to Never.")
        if power_info["thermal_warning"]:
            add("warning", "Host Mac", "Mac is overheating", power_info["thermal_warning"])
    if not report["database"]["connected"]:
        add("critical", "App", "Database not reachable", "Restart the app.")
    if (report["application"]["logs_bytes"] or 0) > LOG_WARN_BYTES:
        add("warning", "App", "Log files are large", f"{gib(report['application']['logs_bytes'])}.")
    for station in report["stations"]:
        name = station["name"]
        device = station.get("device")
        fresh = device is not None and not device["stale"]
        if station["status"] in {"offline", "never_seen"} and fresh:
            add("warning", "Scan stations", f"{name}: page not running", "Restart the Pi.")
        elif station["status"] == "offline":
            add("warning", "Scan stations", f"{name} is offline", "Check power and Wi-Fi.")
        elif station["status"] == "never_seen":
            add("info", "Scan stations", f"{name} not connected")
        if station["status"] == "no_scanner":
            add("warning", "Scan stations", f"{name}: no scanner", "Check the USB cable.")
        if station["status"] in {"ready", "no_scanner"} and device is not None and device["stale"]:
            add("warning", "Scan stations", f"{name}: no report for {device['age_seconds'] // 60} min", "Restart the Pi.")
        if fresh:
            pi_issues(add, name, device)
    printer = report["printer"]
    if not printer["queues"]:
        add("warning", "Label printer", "No printer", "Connect the Brother printer by USB.")
    for queue in printer["queues"]:
        if queue["state"] == "disabled":
            add("warning", "Label printer", f"{queue['name']} is paused", "Resume it in Printers & Scanners.")
        if queue["alerts"]:
            add("warning", "Label printer", f"{queue['name']}: {queue['alerts']}", "Check power, cable and labels.")
        if queue["jobs_waiting"] >= 3:
            add("warning", "Label printer", f"{queue['jobs_waiting']} jobs waiting on {queue['name']}", "Check power and labels.")
    failed = sum(count for status, count in printer["jobs_last_24h"].items() if status in {"failed", "unknown"})
    if failed:
        add("warning", "Label printer", f"{failed} label {'job' if failed == 1 else 'jobs'} failed today", "See Printing in Transactions.")
    backup = report["backups"]
    if backup.get("available"):
        if backup.get("last_error"):
            add("critical", "Backups", "Last backup failed", backup["last_error"])
        if backup.get("enabled") is False:
            add("warning", "Backups", "Daily backups are off")
        elif backup.get("overdue"):
            add("warning", "Backups", "Backup overdue")
        latest = backup.get("latest_at")
        if not latest:
            add("warning", "Backups", "No backups yet")
        elif utcnow() - datetime.fromisoformat(latest) > BACKUP_STALE:
            add("warning", "Backups", "No backup in the last day")
    order = {"critical": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda row: order[row["level"]])
    return issues


def pi_issues(add, name, device):
    area = "Scan stations"
    temperature = device.get("temperature_c")
    if temperature is not None and temperature >= PI_TEMP_CRIT:
        add("critical", area, f"{name} is overheating ({temperature:.0f} °C)")
    elif temperature is not None and temperature >= PI_TEMP_WARN:
        add("warning", area, f"{name} is hot ({temperature:.0f} °C)")
    flags = set((device.get("throttled") or {}).get("flags") or [])
    if "under_voltage" in flags:
        add("critical", area, f"{name}: low power", "Use the official Pi power supply.")
    elif "under_voltage_occurred" in flags:
        add("warning", area, f"{name}: low power since boot", "Check the power supply.")
    if flags & {"throttled", "frequency_capped"} and "under_voltage" not in flags:
        add("warning", area, f"{name} is throttled")
    disk_info = device.get("disk") or {}
    free, percent = disk_info.get("free_bytes"), disk_info.get("percent")
    if disk_info.get("read_only"):
        add("critical", area, f"{name}: SD card is read-only", "Replace the SD card.")
    elif (free is not None and free < PI_DISK_CRIT_FREE) or (percent or 0) >= PI_DISK_CRIT_PERCENT:
        add("critical", area, f"{name}: SD card almost full", f"{gib(free or 0)} free.")
    elif (free is not None and free < PI_DISK_WARN_FREE) or (percent or 0) >= PI_DISK_WARN_PERCENT:
        add("warning", area, f"{name}: SD card getting full", f"{gib(free or 0)} free.")
    memory_percent = (device.get("memory") or {}).get("percent")
    if memory_percent is not None and memory_percent >= PI_MEMORY_WARN:
        add("warning", area, f"{name}: memory {memory_percent:.0f}% used")
    for service, info in (device.get("services") or {}).items():
        if info.get("state") not in {"active", "activating", "reloading"}:
            add("warning", area, f"{name}: {service} is {info.get('state')}", "Restart the Pi.")
    skew = device.get("clock_skew_seconds")
    if skew is not None and abs(skew) > PI_CLOCK_SKEW_SECONDS:
        add("warning", area, f"{name}: clock off by {abs(skew) / 60:.0f} min")
    signal = (device.get("network") or {}).get("wifi_signal_dbm")
    if signal is not None and signal < PI_WIFI_WEAK_DBM:
        add("warning", area, f"{name}: weak Wi-Fi ({signal} dBm)")
    running, expected = device.get("package_version"), app_version.current()["version"]
    if running and expected != "unknown" and running != expected:
        add("warning", area, f"{name}: old version ({running})", f"The Mac runs {expected}.")
    uptime = device.get("uptime_seconds")
    if uptime is not None and uptime < PI_RECENT_BOOT_SECONDS:
        add("info", area, f"{name} restarted {uptime // 60} min ago")


def last_update(state):
    """What deploy/macos/update.py last did: updated, rolled back or refused, and when."""
    try:
        import json
        facts = json.loads((state / "update-status.json").read_text())
        return facts if isinstance(facts, dict) else None
    except (OSError, ValueError):
        return None


def build_report(request, db):
    state = state_dir()
    storage = [volume for volume in [disk(state if state.exists() else Path.home(), "Mac storage")] if volume]
    try:
        archive = backups.archive_folder()
        if storage and archive.exists() and archive.stat().st_dev != (state if state.exists() else Path.home()).stat().st_dev:
            other = disk(archive, "Backup drive")
            if other:
                storage.append(other)
    except OSError:
        pass
    now = utcnow()
    report = {
        "server_time": now.isoformat(),
        "host": host_facts(),
        "cpu": {"percent": current_cpu()},
        "memory": memory(),
        "storage": storage,
        "power": power(),
        "application": {**app_version.current(), "last_update": last_update(state), "started_at": iso_from_epoch(STARTED), "state_folder": str(state),
                        "logs_bytes": folder_bytes(state / "logs"), "pid": os.getpid()},
        "database": database_facts(db),
        "backups": backup_facts(request),
        "stations": [{**station_status(station, now), "device": device_status(station.id, now)}
                     for station in db.scalars(select(Station).order_by(Station.name))],
        "printer": printer_facts(db),
        "history": list(HISTORY),
        "sample_seconds": SAMPLE_SECONDS,
    }
    report["issues"] = find_issues(report)
    levels = {issue["level"] for issue in report["issues"]}
    report["overall"] = "critical" if "critical" in levels else "warning" if "warning" in levels else "ok"
    return report


@router.get("/health")
def system_health(request: Request, db=Depends(get_db)):
    ensure_sampler()
    with _LOCK:
        cached = _STATE["report"] if time.monotonic() - _STATE["report_at"] < 4 else None
    if cached:
        return cached
    report = build_report(request, db)
    with _LOCK:
        _STATE.update(report=report, report_at=time.monotonic())
    return report
