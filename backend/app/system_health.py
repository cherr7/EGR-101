"""System health: the host Mac, database, printer, backups and scan stations. Every probe is best effort."""
from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text

from . import backups
from . import version as app_version
from .db import get_db
from .paths import read_json, run_quiet, state_dir
from .models import AuditEvent, PrintJob, Specimen, Station, utcnow
from .printing import is_label_queue, printer_inventory
from . import printer_network
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
BATTERY_CRIT_PERCENT = 25
BACKUP_STALE = timedelta(hours=36)
PI_TEMP_CRIT = 80  # the Pi firmware starts throttling at 80-85 °C
PI_DISK_WARN_PERCENT, PI_DISK_CRIT_PERCENT = 90, 95
PI_DISK_WARN_FREE, PI_DISK_CRIT_FREE = 1 * 2**30, 500 * 2**20


def run(args, timeout=3):
    return run_quiet(args, timeout)


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
    # Something holding a sleep assertion (the boot service's caffeinate, Amphetamine) keeps the Mac awake.
    assertions = run(["/usr/bin/pmset", "-g", "assertions"])
    prevented = bool(re.search(r"^\s*(PreventUserIdleSystemSleep|PreventSystemSleep)\s+1\s*$", assertions, re.MULTILINE))
    return {"source": source.group(1) if source else None,
            "on_battery": bool(source and "Battery" in source.group(1)),
            "has_battery": "InternalBattery" in battery,
            "battery_percent": int(level.group(1)) if level else None,
            "battery_state": level.group(2).strip() if level else None,
            "time_remaining": level.group(3) if level and ":" in level.group(3) else None,
            "sleep_minutes": int(sleep.group(1)) if sleep else None,
            "sleep_note": sleep.group(2).strip(" ()") if sleep and sleep.group(2).strip() else None,
            "sleep_prevented": prevented,
            "display_sleep_minutes": int(display.group(1)) if display else None}


@lru_cache(maxsize=1)
def fixed_host_facts():
    """What doesn't change while the app runs: asked once, not on every report."""
    values = sysctl("hw.model", "machdep.cpu.brand_string")
    return {"model": values.get("hw.model"), "chip": values.get("machdep.cpu.brand_string") or platform.processor() or None,
            "os": f"macOS {platform.mac_ver()[0]}" if sys.platform == "darwin" else f"{platform.system()} {platform.release()}",
            "cores": os.cpu_count(), "booted_at": iso_from_epoch(boot_time()), "python": platform.python_version()}


def host_facts():
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {"hostname": mdns_name(), "ip": lan_ip(), **fixed_host_facts(),
            "load_average": [round(value, 2) for value in load] if load else None}


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


def label_printer_ready(inventory, uris):
    """Can a label print now, on the chosen printer or its USB/Wi-Fi partner? None when there's no label printer."""
    labels = [item for item in inventory["printers"] if item.get("label_printer")]
    if not labels:
        return None
    names = [item["name"] for item in labels]
    preferred = inventory.get("preferred_queue")
    chosen = preferred if preferred in names else next((q["name"] for q in labels if q["is_default"]), names[0])
    return printer_network.working_queue(chosen, inventory["printers"], uris) is not None


def printer_facts(db):
    inventory = printer_inventory()
    lpstat = shutil.which("lpstat")
    uris = printer_network.devices(lpstat) if lpstat else {}
    queues = {item["name"]: {**item, "state": "unknown", "alerts": None, "device": None, "jobs_waiting": 0}
              for item in inventory["printers"]}
    if lpstat:
        for block in re.split(r"(?m)^(?=printer )", run([lpstat, "-l", "-p"])):
            found = re.match(r"printer (\S+) (now printing|is idle|disabled)", block)
            if not found:
                continue
            name = found.group(1)
            row = queues.setdefault(name, {"name": name, "is_default": False, "label_printer": is_label_queue(name),
                                           "state": "unknown", "alerts": None, "device": None, "jobs_waiting": 0})
            row["state"] = {"now printing": "printing", "is idle": "idle", "disabled": "disabled"}[found.group(2)]
            alerts = re.search(r"Alerts:\s*(.+)", block)
            row["alerts"] = alerts.group(1).strip() if alerts and alerts.group(1).strip() != "none" else None
        for name, uri in uris.items():
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
            "ready": label_printer_ready(inventory, uris),
            "queues": sorted(queues.values(), key=lambda row: row["name"]),
            "jobs_last_24h": counts, "wifi": printer_network.last_status(),
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
    power_info = report["power"]
    if power_info:
        if power_info["on_battery"]:
            low = (power_info["battery_percent"] or 100) <= BATTERY_CRIT_PERCENT
            add("critical" if low else "warning", "Host Mac", "Mac is on battery",
                f"{power_info['battery_percent']}% left. Plug in the charger.")
        if power_info["sleep_minutes"] and not power_info.get("sleep_prevented"):
            add("warning", "Host Mac", f"Mac sleeps after {power_info['sleep_minutes']} min",
                "Stations can't scan while it sleeps. Run it from the Fabric Conditioning app, or set sleep to Never.")
    if not report["database"]["connected"]:
        add("critical", "App", "Database not reachable", "Restart the app.")
    for station in report["stations"]:
        name = station["name"]
        device = station.get("device")
        fresh = device is not None and not device["stale"]
        if station["status"] in {"offline", "never_seen"} and fresh:
            add("warning", "Scan stations", f"{name}: page not running", "Restart the Pi.")
        elif station["status"] == "offline":
            add("warning", "Scan stations", f"{name} is offline", "Check power and Wi-Fi.")
        if station["status"] == "no_scanner":
            add("warning", "Scan stations", f"{name}: no scanner", "Check the USB cable.")
        if station["status"] in {"ready", "no_scanner"} and device is not None and device["stale"]:
            add("warning", "Scan stations", f"{name}: no report for {device['age_seconds'] // 60} min", "Restart the Pi.")
        if fresh:
            pi_issues(add, name, device)
    printer = report["printer"]
    labels = [queue for queue in printer["queues"] if queue["label_printer"]]
    if not labels:
        add("warning", "Label printer", "No label printer", "Connect the Brother printer by USB.")
    elif printer.get("ready") is False:
        add("warning", "Label printer", "Label printer not connected", "Check the USB cable and that it's on.")
    for queue in labels:
        if queue["state"] == "disabled":
            add("warning", "Label printer", f"{queue['name']} is paused", "Resume it in Printers & Scanners.")
        if queue["jobs_waiting"] >= 3:
            add("warning", "Label printer", f"{queue['jobs_waiting']} jobs waiting on {queue['name']}", "Check power and labels.")
    wifi = printer.get("wifi") or {}
    if wifi.get("result") == "found":
        add("warning", "Label printer", f"Wi-Fi printer is now at {wifi.get('ip')}", "Update its address in Printers & Scanners.")
    failed = sum(count for status, count in printer["jobs_last_24h"].items() if status in {"failed", "unknown"})
    if failed:
        add("warning", "Label printer", f"{failed} label {'job' if failed == 1 else 'jobs'} failed today", "See Printing in Transactions.")
    backup = report["backups"]
    if backup.get("available"):
        if backup.get("last_error"):
            add("critical", "Backups", "Last backup failed", backup["last_error"])
        latest = backup.get("latest_at")
        if backup.get("enabled") is False:
            add("warning", "Backups", "Daily backups are off")
        elif not latest or utcnow() - datetime.fromisoformat(latest) > BACKUP_STALE:
            add("warning", "Backups", "No backup in the last day")
    order = {"critical": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda row: order[row["level"]])
    return issues


def pi_issues(add, name, device):
    """Only what stops a station working or needs someone to go to it."""
    area = "Scan stations"
    temperature = device.get("temperature_c")
    if temperature is not None and temperature >= PI_TEMP_CRIT:
        add("critical", area, f"{name} is overheating ({temperature:.0f} °C)", "Check its vents and fan.")
    if "under_voltage" in set((device.get("throttled") or {}).get("flags") or []):
        add("critical", area, f"{name}: low power", "Use the official Pi power supply.")
    disk_info = device.get("disk") or {}
    free, percent = disk_info.get("free_bytes"), disk_info.get("percent")
    if disk_info.get("read_only"):
        add("critical", area, f"{name}: SD card is read-only", "Replace the SD card.")
    elif (free is not None and free < PI_DISK_CRIT_FREE) or (percent or 0) >= PI_DISK_CRIT_PERCENT:
        add("critical", area, f"{name}: SD card almost full", f"{gib(free or 0)} free.")
    elif (free is not None and free < PI_DISK_WARN_FREE) or (percent or 0) >= PI_DISK_WARN_PERCENT:
        add("warning", area, f"{name}: SD card getting full", f"{gib(free or 0)} free.")
    for service, info in (device.get("services") or {}).items():
        if info.get("state") not in {"active", "activating", "reloading"}:
            add("warning", area, f"{name}: {service} is {info.get('state')}", "Restart the Pi.")
    running, expected = device.get("package_version"), app_version.current()["version"]
    update = device.get("update") or {}
    if update.get("state") in {"rolled back", "failed"}:
        add("warning", area, f"{name}: update to {update.get('version')} failed", update.get("error") or "")
    elif running and expected != "unknown" and running != expected and not update:
        # A Pi that reports no update state was installed before self-update.
        add("warning", area, f"{name}: old version ({running})", "Run deploy-pi.sh for it once.")


def last_update(state):
    """What deploy/macos/update.py last did: updated, rolled back or refused, and when."""
    return read_json(state / "update-status.json")


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
                        "pid": os.getpid()},
        "database": database_facts(db),
        "backups": backup_facts(request),
        "stations": [{**station_status(station, now), "device": device_status(station.id, now)}
                     for station in db.scalars(select(Station).order_by(Station.name))],
        "printer": printer_facts(db),
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
