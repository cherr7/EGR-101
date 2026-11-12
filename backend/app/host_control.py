"""Mac host controls: updates, the outside link with its passcode, the Pi update package, and the menu-bar summary."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import version as app_version
from .db import get_db
from .models import Station, utcnow
from .stations import station_status

router = APIRouter(prefix="/api", tags=["Host"])
TAG = re.compile(r"^v(\d{4})\.(\d{1,2})\.(\d{1,2})-(\d+)$")
CHECK_EVERY = 24 * 3600
COOKIE = "fabric_outside"
COOKIE_HOURS = 12
CF_HEADERS = (b"cf-ray", b"cf-connecting-ip", b"cf-visitor")
OPEN_OUTSIDE = {"/api/health", "/api/outside/status", "/api/outside/login"}


def state_dir() -> Path:
    return Path(os.environ.get("FABRIC_STATE_DIR", str(Path.home() / "Library/Application Support/Fabric Conditioning")))


def read_json(path: Path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path: Path, value: dict, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644), "w") as stream:
        json.dump(value, stream)
    os.replace(temporary, path)


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


# ---------- updates ----------

def release_key(version: str | None):
    found = TAG.match((version or "").strip())
    return tuple(int(part) for part in found.groups()) if found else None


def source_repo() -> Path | None:
    value = os.environ.get("FABRIC_SOURCE_REPO")
    return Path(value) if value and (Path(value) / "scripts/update-host.sh").is_file() else None


def check_latest() -> dict:
    """Ask GitHub (through the Mac's git checkout) for the newest release tag. Changes nothing."""
    repo = source_repo()
    result = {"latest": None, "checked_at": utcnow().isoformat(), "error": None}
    if not repo:
        result["error"] = "No source checkout to update from."
    else:
        try:
            output = subprocess.run(["git", "-C", str(repo), "ls-remote", "--tags", "--refs", "origin", "v*"],
                                    capture_output=True, text=True, timeout=20, check=True).stdout
            tags = [line.rsplit("refs/tags/", 1)[-1] for line in output.splitlines()]
            tags = [tag for tag in tags if release_key(tag)]
            result["latest"] = max(tags, key=release_key) if tags else None
        except (OSError, subprocess.SubprocessError) as error:
            result["error"] = "Can't reach GitHub." if isinstance(error, subprocess.CalledProcessError) else str(error)[:200]
    write_json(state_dir() / "update-check.json", result)
    return result


def update_running() -> bool:
    facts = read_json(state_dir() / "update.lock") or {}
    return pid_alive(facts.get("pid"))


def update_status() -> dict:
    running = app_version.current()["version"]
    check = read_json(state_dir() / "update-check.json") or {}
    try:
        age = time.time() - (state_dir() / "update-check.json").stat().st_mtime
    except OSError:
        age = None
    if source_repo() and (age is None or age > CHECK_EVERY):
        threading.Thread(target=check_latest, daemon=True).start()   # once a day; only tells, never installs
    latest = check.get("latest")
    newer = bool(release_key(latest) and release_key(running) and release_key(latest) > release_key(running))
    return {"running": running, "latest": latest, "available": newer, "checked_at": check.get("checked_at"),
            "check_error": check.get("error"), "in_progress": update_running(), "can_update": source_repo() is not None,
            "last": read_json(state_dir() / "update-status.json")}


@router.get("/system/update")
def get_update():
    return update_status()


@router.post("/system/update/check")
def post_update_check():
    check_latest()
    return update_status()


@router.post("/system/update", status_code=202)
def start_update(request: Request):
    if is_outside(request.scope):
        raise HTTPException(403, "Update from a computer at the lab.")
    repo = source_repo()
    if not repo:
        raise HTTPException(409, "No source checkout to update from.")
    if update_running():
        raise HTTPException(409, "An update is already running.")
    log = state_dir() / "logs/update.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if key not in {"DATABASE_URL", "FABRIC_APP_ROOT", "FRONTEND_DIST", "PGPASSWORD"}}
    with log.open("wb") as stream:
        # Its own session: the update stops this server and must outlive it.
        process = subprocess.Popen(["/bin/bash", str(repo / "scripts/update-host.sh")], cwd=repo, env=env,
                                   stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True)
    write_json(state_dir() / "update.lock", {"pid": process.pid, "started_at": utcnow().isoformat()})
    return update_status()


# ---------- outside access ----------

def is_outside(scope) -> bool:
    """Requests through the Cloudflare tunnel carry Cloudflare's headers; local ones never do.
    Faking them only gets a local user asked for the passcode."""
    return any(name in CF_HEADERS for name, _ in scope.get("headers", []))


def outside_settings() -> dict:
    return read_json(state_dir() / "outside.json") or {}


def hash_passcode(passcode: str, salt: bytes) -> str:
    return hashlib.scrypt(passcode.encode(), salt=salt, n=2 ** 14, r=8, p=1).hex()


def cookie_value(secret: str, expires: int) -> str:
    return f"{expires}.{hmac.new(bytes.fromhex(secret), str(expires).encode(), hashlib.sha256).hexdigest()}"


def signed_in(cookie: str | None, settings: dict) -> bool:
    if not cookie or "." not in cookie or not settings.get("secret"):
        return False
    expires, _ = cookie.split(".", 1)
    try:
        return int(expires) > time.time() and hmac.compare_digest(cookie, cookie_value(settings["secret"], int(expires)))
    except ValueError:
        return False


def cookie_from(scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"cookie":
            for part in value.decode("latin-1").split(";"):
                key, _, item = part.strip().partition("=")
                if key == COOKIE:
                    return item
    return None


class OutsideGate:
    """Ask for the passcode on API calls that came through the tunnel. Local requests pass untouched."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path in OPEN_OUTSIDE or not is_outside(scope):
            return await self.app(scope, receive, send)
        settings = outside_settings()
        if not (settings.get("enabled") and settings.get("passcode_hash")):
            response = JSONResponse(status_code=403, content={"detail": "Outside access is off."})
        elif signed_in(cookie_from(scope), settings):
            return await self.app(scope, receive, send)
        else:
            response = JSONResponse(status_code=401, content={"detail": "Passcode required."})
        await response(scope, receive, send)


_ATTEMPTS: dict[str, list[float]] = {}
_ATTEMPTS_LOCK = threading.Lock()


def too_many(client: str) -> bool:
    now = time.time()
    with _ATTEMPTS_LOCK:
        recent = [at for at in _ATTEMPTS.get(client, []) if now - at < 600]
        recent.append(now)
        _ATTEMPTS[client] = recent
        return len(recent) > 10


class PasscodeBody(BaseModel):
    passcode: str = Field(min_length=1, max_length=200)


@router.post("/outside/login")
def outside_login(body: PasscodeBody, request: Request, response: Response):
    settings = outside_settings()
    if not (settings.get("enabled") and settings.get("passcode_hash")):
        raise HTTPException(403, "Outside access is off.")
    client = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    if too_many(client):
        raise HTTPException(429, "Too many tries. Wait 10 minutes.")
    salt = bytes.fromhex(settings.get("salt", ""))
    if not hmac.compare_digest(hash_passcode(body.passcode, salt), settings["passcode_hash"]):
        raise HTTPException(401, "Wrong passcode.")
    expires = int(time.time()) + COOKIE_HOURS * 3600
    response.set_cookie(COOKIE, cookie_value(settings["secret"], expires), max_age=COOKIE_HOURS * 3600,
                        httponly=True, secure=True, samesite="lax", path="/")
    return {"ok": True}


def tunnel_facts() -> dict:
    return read_json(state_dir() / "tunnel.json") or {}


@router.get("/outside/status")
def outside_status(request: Request):
    settings = outside_settings()
    outside = is_outside(request.scope)
    facts = {"outside": outside, "signed_in": outside and signed_in(request.cookies.get(COOKIE), settings)}
    if outside:
        return facts
    tunnel = tunnel_facts()
    return {**facts, "enabled": bool(settings.get("enabled")), "has_passcode": bool(settings.get("passcode_hash")),
            "url": tunnel.get("url") if settings.get("enabled") else None, "error": tunnel.get("error"),
            "started_at": tunnel.get("started_at")}


class OutsideBody(BaseModel):
    enabled: bool | None = None
    passcode: str | None = Field(default=None, min_length=6, max_length=200)


@router.put("/outside")
def set_outside(body: OutsideBody, request: Request):
    if is_outside(request.scope):
        raise HTTPException(403, "Change this from a computer at the lab.")
    settings = outside_settings()
    if body.passcode is not None:
        salt = secrets.token_bytes(16)
        # A new passcode also signs everyone out.
        settings.update(salt=salt.hex(), passcode_hash=hash_passcode(body.passcode, salt), secret=secrets.token_hex(32))
    if body.enabled is not None:
        if body.enabled and not settings.get("passcode_hash"):
            raise HTTPException(409, "Set a passcode first.")
        settings["enabled"] = body.enabled
    write_json(state_dir() / "outside.json", settings, private=True)
    return outside_status(request)


# ---------- Pi update package ----------

def package_path() -> Path:
    return app_version.ROOT / "station-package.tar.gz"


def package_facts() -> dict | None:
    """Version and checksum of the Pi package built with this release (cached by file time)."""
    path = package_path()
    try:
        stat = path.stat()
    except OSError:
        return None
    cached = _PACKAGE.get("facts")
    if cached and cached["mtime"] == stat.st_mtime:
        return cached["value"]
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    value = {"version": app_version.current()["version"], "sha256": digest.hexdigest(), "size": stat.st_size}
    _PACKAGE["facts"] = {"mtime": stat.st_mtime, "value": value}
    return value


_PACKAGE: dict = {}


@router.get("/station/package/info")
def station_package_info():
    facts = package_facts()
    if not facts:
        raise HTTPException(404, "No station package in this release.")
    return facts


@router.get("/station/package")
def station_package():
    facts = package_facts()
    if not facts:
        raise HTTPException(404, "No station package in this release.")
    return FileResponse(package_path(), media_type="application/gzip",
                        headers={"X-Package-Sha256": facts["sha256"], "Cache-Control": "no-store"})


# ---------- menu-bar summary ----------

@router.get("/host/summary")
def host_summary(db=Depends(get_db)):
    from .printing import printer_inventory, preferred_queue
    now = utcnow()
    stations = [station_status(station, now) for station in db.scalars(select(Station).order_by(Station.name))]
    inventory = printer_inventory()
    queues = inventory.get("printers", [])
    preferred = preferred_queue()
    queue = next((q for q in queues if q["name"] == preferred), None) or next((q for q in queues if q["is_default"]), None)
    update = update_status()
    return {"version": app_version.current()["version"],
            "update": {"latest": update["latest"], "available": update["available"], "in_progress": update["in_progress"]},
            "outside_url": tunnel_facts().get("url") if outside_settings().get("enabled") else None,
            "stations": [{"name": re.sub(r"( conditioning room)? (door|station)$", "", s["name"], flags=re.I), "status": s["status"]}
                         for s in stations],
            "printer": f"{queue['name']} · {queue['state']}" if queue else None}
