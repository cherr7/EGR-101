"""Scanner station presence (in memory) and the host's address for the sidebar."""
from collections import deque
from datetime import datetime, timedelta, timezone
import re
import socket
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from .db import get_db
from .models import Station, utcnow

router = APIRouter(prefix="/api", tags=["Stations"])
LOCK = threading.Lock()
PRESENCE = {}  # station_id -> {client_ip -> record}
ONLINE_FOR = timedelta(seconds=15)
FORGET_AFTER = timedelta(hours=12)
DEVICES = {}  # station_id -> latest Pi health report from station-agent.py
DEVICE_STALE = timedelta(seconds=90)  # three missed 30-second reports
DEVICE_HISTORY = 120  # one hour of reports


class Heartbeat(BaseModel):
    scanner_ready: bool
    input: str = Field(default="keyboard", max_length=32)


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DeviceMemory(_Loose):
    total_bytes: int | None = Field(default=None, ge=0)
    used_bytes: int | None = Field(default=None, ge=0)
    percent: float | None = Field(default=None, ge=0, le=100)
    swap_used_bytes: int | None = Field(default=None, ge=0)


class DeviceDisk(_Loose):
    total_bytes: int | None = Field(default=None, ge=0)
    free_bytes: int | None = Field(default=None, ge=0)
    percent: float | None = Field(default=None, ge=0, le=100)
    read_only: bool | None = None


class DeviceThrottle(_Loose):
    value: int = Field(ge=0, le=2**32)
    flags: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("flags")
    @classmethod
    def short_flags(cls, values):
        return [value[:40] for value in values]


class DeviceNetwork(_Loose):
    interface: str | None = Field(default=None, max_length=32)
    wifi_signal_dbm: int | None = Field(default=None, ge=-127, le=0)
    wired: bool | None = None


class DeviceService(_Loose):
    state: str = Field(default="unknown", max_length=32)
    restarts: int | None = Field(default=None, ge=0)


class DeviceInput(_Loose):
    scanner: str | None = Field(default=None, max_length=64)
    scanner_name: str | None = Field(default=None, max_length=200)
    scanner_error: str | None = Field(default=None, max_length=500)
    nfc: str | None = Field(default=None, max_length=64)


class DeviceUpdate(_Loose):
    state: str | None = Field(default=None, max_length=32)
    version: str | None = Field(default=None, max_length=64)
    error: str | None = Field(default=None, max_length=300)


class DeviceReport(_Loose):
    """What deploy/scanner-kiosk/station-agent.py posts every 30 s; every reading is optional."""
    agent_version: int = Field(default=1, ge=1, le=1000)
    sent_at: datetime | None = None
    hostname: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    os: str | None = Field(default=None, max_length=128)
    kernel: str | None = Field(default=None, max_length=64)
    uptime_seconds: int | None = Field(default=None, ge=0)
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    cores: int | None = Field(default=None, ge=1, le=1024)
    load_average: list[float] | None = Field(default=None, max_length=3)
    memory: DeviceMemory | None = None
    disk: DeviceDisk | None = None
    temperature_c: float | None = Field(default=None, ge=-60, le=200)
    throttled: DeviceThrottle | None = None
    network: DeviceNetwork | None = None
    ntp_synchronized: bool | None = None
    services: dict[str, DeviceService] | None = Field(default=None, max_length=8)
    input: DeviceInput | None = None
    package_built_at: str | None = Field(default=None, max_length=64)
    package_version: str | None = Field(default=None, max_length=64)
    update: DeviceUpdate | None = None


def client_ip(request: Request):
    return request.client.host if request.client else "unknown"


@router.post("/stations/{station_id}/heartbeat")
def station_heartbeat(station_id: str, body: Heartbeat, request: Request, db=Depends(get_db)):
    if db.get(Station, station_id) is None:
        raise HTTPException(404, "Unknown station.")
    now = utcnow()
    with LOCK:
        clients = PRESENCE.setdefault(station_id, {})
        clients[client_ip(request)] = {"ip": client_ip(request), "last_seen": now,
                                       "scanner_ready": body.scanner_ready, "input": body.input}
        for ip in [ip for ip, row in clients.items() if now - row["last_seen"] > FORGET_AFTER]:
            del clients[ip]
    return {"ok": True, "server_time": now.isoformat()}


@router.post("/stations/{station_id}/device")
def station_device(station_id: str, body: DeviceReport, request: Request, db=Depends(get_db)):
    if db.get(Station, station_id) is None:
        raise HTTPException(404, "Unknown station.")
    now = utcnow()
    report = body.model_dump(mode="json")
    skew = None
    if body.sent_at:
        sent = body.sent_at if body.sent_at.tzinfo else body.sent_at.replace(tzinfo=timezone.utc)
        skew = round((sent - now).total_seconds(), 1)
    with LOCK:
        previous = DEVICES.get(station_id)
        history = previous["history"] if previous else deque(maxlen=DEVICE_HISTORY)
        history.append({"at": now.isoformat(), "cpu": body.cpu_percent,
                        "memory": body.memory.percent if body.memory else None, "temperature": body.temperature_c})
        DEVICES[station_id] = {"ip": client_ip(request), "received_at": now, "clock_skew_seconds": skew,
                               "report": report, "history": history}
    return {"ok": True, "server_time": now.isoformat()}


def device_status(station_id, now):
    """The latest Pi report for a station, or None if its agent has never reported."""
    with LOCK:
        record = DEVICES.get(station_id)
        if record is None:
            return None
        record = {**record, "history": list(record["history"])}
    age = (now - record["received_at"]).total_seconds()
    return {**record["report"], "ip": record["ip"], "received_at": record["received_at"].isoformat(),
            "age_seconds": round(age), "stale": age > DEVICE_STALE.total_seconds(),
            "clock_skew_seconds": record["clock_skew_seconds"], "history": record["history"]}


def lan_ip():
    """Primary IPv4 address; the UDP connect sends no packets, it only selects a route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        address = probe.getsockname()[0]
        if not address.startswith("127."):
            return address
    except OSError:
        pass
    finally:
        probe.close()
    try:
        address = socket.gethostbyname(socket.gethostname())
        return None if address.startswith("127.") else address
    except OSError:
        return None


_ADDRESSES = {"at": 0.0, "values": frozenset()}


def local_addresses():
    """Every IP assigned to this machine (cached briefly; Wi-Fi addresses can change)."""
    now = time.monotonic()
    if now - _ADDRESSES["at"] > 10:
        found = {"127.0.0.1", "::1"}
        try:
            output = subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True, timeout=2).stdout
            for match in re.finditer(r"^\s*inet6?\s+([0-9a-fA-F:.]+)(?:%\S+)?", output, re.MULTILINE):
                found.add(match.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
        address = lan_ip()
        if address:
            found.add(address)
        _ADDRESSES.update(at=now, values=frozenset(found))
    return _ADDRESSES["values"]


def is_local_client(request: Request):
    """True when the browser runs on this host, whichever of its addresses it used."""
    host = client_ip(request)
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in {"localhost"} | local_addresses()


def mdns_name():
    if sys.platform == "darwin":
        try:
            name = subprocess.run(["/usr/sbin/scutil", "--get", "LocalHostName"], capture_output=True,
                                  text=True, timeout=2).stdout.strip()
            if name:
                return name + ".local"
        except (OSError, subprocess.SubprocessError):
            pass
    name = socket.gethostname()
    return name if "." in name else name + ".local"


def station_status(station, now):
    with LOCK:
        clients = sorted(PRESENCE.get(station.id, {}).values(), key=lambda row: row["last_seen"], reverse=True)
    live = [row for row in clients if now - row["last_seen"] <= ONLINE_FOR]
    latest = (live or clients or [None])[0]
    if live:
        status = "ready" if latest["scanner_ready"] else "no_scanner"
    else:
        status = "offline" if clients else "never_seen"
    return {"id": station.id, "name": station.name, "room_id": station.room_id, "status": status,
            "ip": latest["ip"] if latest else None,
            "last_seen": latest["last_seen"].isoformat() if latest else None,
            "scanner_ready": bool(latest and latest["scanner_ready"]), "input": latest["input"] if latest else None,
            "live_clients": [row["ip"] for row in live]}


@router.get("/network/status")
def network_status(request: Request, db=Depends(get_db)):
    now = utcnow()
    port = request.scope.get("server", (None, 8120))[1] or 8120
    ip, hostname = lan_ip(), mdns_name()
    stations = [station_status(station, now) for station in db.scalars(select(Station).order_by(Station.name))]
    return {"host": {"ip": ip, "hostname": hostname, "port": port,
                     "url": f"http://{ip}:{port}" if ip else None, "hostname_url": f"http://{hostname}:{port}"},
            "stations": stations, "online_window_seconds": int(ONLINE_FOR.total_seconds()),
            "server_time": now.isoformat()}
