"""Portable, consistent JSON checkpoints; restore never executes uploaded SQL."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, create_engine, select, text

from .db import Base, engine
from .models import utcnow

router = APIRouter(prefix="/api/backups", tags=["Backups"])
LOCK = threading.RLock()
LIMIT = 64 * 1024 * 1024
EXPANDED_LIMIT = 256 * 1024 * 1024
SCHEMA = 1


def folder():
    base = Path(os.environ.get("FABRIC_STATE_DIR", str(Path.home() / "Library/Application Support/Fabric Conditioning")))
    result = base / "backups" / (engine.url.database or "conditioning")
    result.mkdir(parents=True, exist_ok=True, mode=0o700)
    return result


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2))
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def settings():
    path = folder() / "settings.json"
    value = {"destination": None, "retention_days": 30, "daily_hour": 2, "enabled": True}
    if path.exists():
        value.update(json.loads(path.read_text()))
    return value


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def item_paths(identifier):
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise HTTPException(404, "Backup not found.")
    return folder() / (identifier + ".zip"), folder() / (identifier + ".info.json")


def encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError("Unsupported snapshot value.")


def make_backup():
    with LOCK:
        identifier = uuid.uuid4().hex
        path, info_path = item_paths(identifier)
        created = utcnow().isoformat()
        # All tables are read at one PostgreSQL MVCC snapshot, including print and request state.
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                tables = {table.name: [dict(row) for row in connection.execute(select(table)).mappings()]
                          for table in Base.metadata.sorted_tables}
                revisions = list(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
        raw = json.dumps({"tables": tables}, default=encode, separators=(",", ":")).encode()
        manifest = {"format": "fabric-conditioning", "version": SCHEMA, "created_at": created,
                    "schema_revisions": revisions, "sha256": hashlib.sha256(raw).hexdigest(),
                    "counts": {name: len(rows) for name, rows in tables.items()}}
        temporary = path.with_suffix(".partial")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("snapshot.json", raw)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        info = {"id": identifier, "created_at": created, "size": path.stat().st_size,
                "sha256": digest(path), "filename": "fabric-" + created[:10] + "-" + identifier[:8] + ".zip",
                "verified": True, "verified_at": created, "counts": manifest["counts"],
                "cloud_copy_state": "not_configured"}
        config = settings()
        if config["destination"]:
            try:
                destination = Path(config["destination"]).expanduser()
                destination.mkdir(parents=True, exist_ok=True)
                copied = destination / info["filename"]
                partial = copied.with_suffix(".partial")
                shutil.copyfile(path, partial)
                if digest(partial) != info["sha256"]:
                    raise OSError("Destination checksum mismatch.")
                partial.replace(copied)
                info["cloud_copy_state"] = "copied_to_destination"
                info["destination_note"] = "Local copy verified; the sync provider handles SharePoint upload."
            except OSError as error:
                info["cloud_copy_state"] = "copy_failed"
                info["destination_note"] = str(error)
        save_json(info_path, info)
        config["last_day"] = utcnow().astimezone(ZoneInfo("America/New_York")).date().isoformat()
        config["last_backup_id"] = identifier
        config["last_error"] = None
        save_json(folder() / "settings.json", config)
        if info["cloud_copy_state"] != "copy_failed":
            cutoff = utcnow() - timedelta(days=config["retention_days"])
            for old in folder().glob("*.info.json"):
                record = json.loads(old.read_text())
                if record["id"] != identifier and datetime.fromisoformat(record["created_at"]) < cutoff:
                    item_paths(record["id"])[0].unlink(missing_ok=True)
                    old.unlink()
        return info


def read_archive(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if set(archive.namelist()) != {"manifest.json", "snapshot.json"} or len(archive.infolist()) != 2:
                raise ValueError("Expected only manifest.json and snapshot.json.")
            if any(item.file_size > EXPANDED_LIMIT or item.flag_bits & 1 for item in archive.infolist()):
                raise ValueError("Archive is too large or encrypted.")
            manifest = json.loads(archive.read("manifest.json"))
            raw = archive.read("snapshot.json")
        if manifest.get("format") != "fabric-conditioning" or manifest.get("version") != SCHEMA:
            raise ValueError("Unsupported backup format.")
        if hashlib.sha256(raw).hexdigest() != manifest.get("sha256"):
            raise ValueError("Snapshot checksum failed.")
        snapshot = json.loads(raw)
        known = {table.name for table in Base.metadata.sorted_tables}
        if set(snapshot["tables"]) != known or set(manifest["counts"]) != known:
            raise ValueError("Snapshot table set differs from this app version.")
        for name, records in snapshot["tables"].items():
            if not isinstance(records, list) or len(records) != manifest["counts"][name]:
                raise ValueError("Snapshot row counts do not match its manifest.")
        return manifest, snapshot
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError, RuntimeError) as error:
        raise HTTPException(422, "Invalid backup: " + str(error))


def admin_engine():
    return create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")


def create_staging(content):
    manifest, snapshot = read_archive(content)
    identifier = uuid.uuid4().hex
    name = "fabric_restore_" + identifier[:20]
    admin = admin_engine()
    target = create_engine(engine.url.set(database=name))
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql('CREATE DATABASE "' + name + '"')
        env = os.environ.copy()
        env["DATABASE_URL"] = engine.url.set(database=name).render_as_string(hide_password=False)
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       cwd=Path(__file__).resolve().parents[1], env=env, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        with target.begin() as connection:
            for table in Base.metadata.sorted_tables:
                converted = []
                columns = {column.name: column for column in table.columns}
                for raw in snapshot["tables"][table.name]:
                    if set(raw) != set(columns):
                        raise ValueError("Backup column set does not match " + table.name)
                    row = dict(raw)
                    for key, column in columns.items():
                        if row[key] is not None and isinstance(column.type, DateTime):
                            row[key] = datetime.fromisoformat(row[key])
                            if row[key].tzinfo is None:
                                raise ValueError("Snapshot timestamps must include a timezone.")
                        elif row[key] is not None and isinstance(column.type, Date):
                            row[key] = date.fromisoformat(row[key])
                    converted.append(row)
                if converted:
                    connection.execute(table.insert(), converted)
        record = {"id": identifier, "status": "validated", "counts": manifest["counts"],
                  "created_at": utcnow().isoformat(), "snapshot_at": manifest["created_at"],
                  "database": name}
        save_json(folder() / (identifier + ".staged.json"), record)
        return {key: value for key, value in record.items() if key != "database"}
    except Exception as error:
        target.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql('DROP DATABASE IF EXISTS "' + name + '" WITH (FORCE)')
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(422, "Backup could not be validated against the database constraints.")
    finally:
        target.dispose()
        admin.dispose()


def apply_staging(identifier):
    item_paths(identifier)  # Validate the opaque identifier before constructing a path.
    path = folder() / (identifier + ".staged.json")
    if not path.exists():
        raise HTTPException(404, "Validated restore not found.")
    record = json.loads(path.read_text())
    if record["status"] != "validated":
        raise HTTPException(409, "This restore has already been used.")
    if utcnow() - datetime.fromisoformat(record["created_at"]) > timedelta(days=1):
        raise HTTPException(409, "Restore validation expired. Import the backup again.")
    active = engine.url.database
    stage = record["database"]
    previous = "fabric_previous_" + uuid.uuid4().hex[:20]
    if not all(re.fullmatch(r"[a-zA-Z0-9_]+", value or "") for value in [active, stage, previous]):
        raise HTTPException(409, "Database names are not supported for restore.")
    with LOCK:
        # Keep both a downloadable pre-restore checkpoint and the whole previous database.
        checkpoint = make_backup()
        engine.dispose()
        admin = admin_engine()
        renamed = False
        promoted = False
        try:
            with admin.connect() as connection:
                for name in [active, stage]:
                    connection.exec_driver_sql('ALTER DATABASE "' + name + '" ALLOW_CONNECTIONS false')
                    connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"), {"name": name})
                connection.exec_driver_sql('ALTER DATABASE "' + active + '" RENAME TO "' + previous + '"')
                renamed = True
                connection.exec_driver_sql('ALTER DATABASE "' + stage + '" RENAME TO "' + active + '"')
                promoted = True
                connection.exec_driver_sql('ALTER DATABASE "' + active + '" ALLOW_CONNECTIONS true')
        except Exception:
            with admin.connect() as connection:
                if promoted:
                    connection.exec_driver_sql('ALTER DATABASE "' + active + '" RENAME TO "' + stage + '"')
                if renamed:
                    connection.exec_driver_sql('ALTER DATABASE "' + previous + '" RENAME TO "' + active + '"')
                connection.exec_driver_sql('ALTER DATABASE "' + active + '" ALLOW_CONNECTIONS true')
            raise
        finally:
            admin.dispose()
            engine.dispose()
        record.update(status="restored", previous_database=previous, pre_restore_backup=checkpoint["id"])
        save_json(path, record)
        return {"id": identifier, "status": "restored", "counts": record["counts"],
                "pre_restore_backup": checkpoint["id"], "previous_database_retained": True}


class BackupSettings(BaseModel):
    destination: str | None = Field(default=None, max_length=2000)
    retention_days: int = Field(default=30, ge=2, le=3650)
    daily_hour: int = Field(default=2, ge=0, le=23)
    enabled: bool = True


class RestoreConfirmation(BaseModel):
    confirmation: str


@router.get("")
def list_backups():
    items = [json.loads(path.read_text()) for path in folder().glob("*.info.json")]
    items.sort(key=lambda row: row["created_at"], reverse=True)
    return {"items": items, "settings": settings(), "timezone": "America/New_York"}


@router.put("/settings")
def update_settings(body: BackupSettings):
    value = body.model_dump()
    if value["destination"]:
        path = Path(value["destination"]).expanduser()
        if not path.is_absolute():
            raise HTTPException(422, "Use an absolute Mac folder path.")
        if path.resolve() == folder().resolve():
            raise HTTPException(422, "Choose a separate destination folder.")
        value["destination"] = str(path)
    with LOCK:
        current = settings()
        current.update(value)
        save_json(folder() / "settings.json", current)
    return current


@router.post("")
def create_backup():
    return make_backup()


@router.get("/{identifier}/download")
def download_backup(identifier: str):
    path, info_path = item_paths(identifier)
    if not path.exists() or not info_path.exists():
        raise HTTPException(404, "Backup not found.")
    info = json.loads(info_path.read_text())
    if digest(path) != info["sha256"]:
        raise HTTPException(409, "Backup checksum failed; use another retained copy.")
    return FileResponse(path, media_type="application/zip", filename=info["filename"])


@router.post("/import")
async def stage_backup(file: UploadFile = File(...)):
    content = await file.read(LIMIT + 1)
    if len(content) > LIMIT:
        raise HTTPException(413, "Backup upload must be smaller than 64 MB.")
    return await asyncio.to_thread(create_staging, content)


@router.post("/imports/{identifier}/restore")
async def restore_backup(identifier: str, body: RestoreConfirmation, request: Request):
    if body.confirmation != "RESTORE":
        raise HTTPException(422, "Type RESTORE to confirm the reviewed database replacement.")
    async with request.app.state.traffic_gate.exclusive():
        return await asyncio.to_thread(apply_staging, identifier)


class TrafficGate:
    """Drain API requests before an explicit restore; reject new ones until it finishes."""
    def __init__(self):
        self.condition = asyncio.Condition()
        self.active = 0
        self.maintenance = False

    async def enter(self):
        async with self.condition:
            if self.maintenance:
                return False
            self.active += 1
            return True

    async def leave(self):
        async with self.condition:
            self.active -= 1
            self.condition.notify_all()

    @asynccontextmanager
    async def exclusive(self):
        async with self.condition:
            if self.maintenance:
                raise HTTPException(409, "Another restore is already in progress.")
            self.maintenance = True
            while self.active:
                await self.condition.wait()
        try:
            yield
        finally:
            async with self.condition:
                self.maintenance = False
                self.condition.notify_all()


async def daily_scheduler():
    while True:
        try:
            value = settings()
            now = utcnow().astimezone(ZoneInfo("America/New_York"))
            if value["enabled"] and now.hour >= value["daily_hour"] and value.get("last_day") != now.date().isoformat():
                await asyncio.to_thread(make_backup)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            value = settings()
            value["last_error"] = type(error).__name__ + ": backup failed; see server log."
            save_json(folder() / "settings.json", value)
        await asyncio.sleep(60)
