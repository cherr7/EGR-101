"""Portable, consistent JSON checkpoints; restore never executes uploaded SQL."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock_time
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, create_engine, select, text

from .db import Base, engine
from .models import utcnow
from .stations import is_local_client

router = APIRouter(prefix="/api/backups", tags=["Backups"])
LOCK = threading.RLock()
LIMIT = 64 * 1024 * 1024
EXPANDED_LIMIT = 256 * 1024 * 1024
SCHEMA = 2           # 2: sequential keys everywhere (no UUIDs); 1-format archives are refused
# Tables whose keys PostgreSQL numbers. A restore never lets any of them count backwards.
NUMBERED = ("projects", "materials", "prep_drafts", "stacks", "specimens", "conditioning_sessions", "audit_events")
_last_identifier = ""


def new_identifier():
    """A sortable timestamp name for a backup or staged restore: 20261112T143000123456 (UTC)."""
    global _last_identifier
    with LOCK:
        value = utcnow().strftime("%Y%m%dT%H%M%S%f")
        while value <= _last_identifier:   # two in the same microsecond
            value = (datetime.strptime(_last_identifier, "%Y%m%dT%H%M%S%f") + timedelta(microseconds=1)).strftime("%Y%m%dT%H%M%S%f")
        _last_identifier = value
        return value
LOGGER = logging.getLogger(__name__)


def folder():
    base = Path(os.environ.get("FABRIC_STATE_DIR", str(Path.home() / "Library/Application Support/Fabric Conditioning")))
    result = base / "backups" / (engine.url.database or "conditioning")
    result.mkdir(parents=True, exist_ok=True, mode=0o700)
    return result


_MIGRATED = set()


def archive_folder():
    """Where checkpoint archives live. The Mac host sets FABRIC_BACKUP_DIR to a visible
    folder (~/Fabric Conditioning Backups); otherwise archives stay beside the settings."""
    configured = os.environ.get("FABRIC_BACKUP_DIR")
    if not configured:
        return folder()
    result = Path(configured).expanduser()
    result.mkdir(parents=True, exist_ok=True, mode=0o700)
    legacy = folder()
    key = (str(legacy), str(result))
    if key not in _MIGRATED and legacy.resolve() != result.resolve():
        # One-time move of archives written before the dedicated folder existed.
        with LOCK:
            for info_path in legacy.glob("*.info.json"):
                identifier = info_path.name.split(".", 1)[0]
                archive = legacy / (identifier + ".zip")
                if archive.exists() and not (result / archive.name).exists():
                    shutil.move(str(archive), result / archive.name)
                if not (result / info_path.name).exists():
                    shutil.move(str(info_path), result / info_path.name)
        _MIGRATED.add(key)
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
        try:
            loaded = json.loads(path.read_text())
            if not isinstance(loaded, dict):
                raise ValueError("Expected backup settings object")
            BackupSettings.model_validate(loaded)
            value.update(loaded)
        except (OSError, ValueError, TypeError):
            LOGGER.exception("Backup settings could not be read")
            value.update(enabled=False, last_error="Backup settings could not be read. Save the backup settings again to resume scheduled snapshots.")
    return value


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def item_paths(identifier):
    if not re.fullmatch(r"[0-9]{8}T[0-9]{12}", identifier):
        raise HTTPException(404, "Backup not found.")
    return archive_folder() / (identifier + ".zip"), archive_folder() / (identifier + ".info.json")


def encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError("Unsupported snapshot value.")


EASTERN = ZoneInfo("America/New_York")
KEEP_WEEKDAY = 0  # Monday: the Sunday-night (Monday 2 a.m.) checkpoint


def weekly_keepers(records):
    """IDs kept forever: the first checkpoint made on each Monday, Eastern time.

    That is normally the 2 a.m. scheduled run (or its same-day catch-up). It is derived
    from timestamps, so it is stable: cleanup never deletes the earliest of a day.
    """
    first = {}
    for record in records:
        local = datetime.fromisoformat(record["created_at"]).astimezone(EASTERN)
        if local.weekday() != KEEP_WEEKDAY:
            continue
        day = local.date()
        if day not in first or local < first[day][0]:
            first[day] = (local, record["id"])
    return {identifier for _, identifier in first.values()}


def make_backup():
    with LOCK:
        identifier = new_identifier()
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
                "sha256": digest(path), "filename": "fabric-" + identifier + ".zip",
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
        config["last_success_at"] = created
        config["last_error"] = None
        save_json(folder() / "settings.json", config)
        if info["cloud_copy_state"] != "copy_failed":
            cutoff = utcnow() - timedelta(days=config["retention_days"])
            records = [(old, json.loads(old.read_text())) for old in archive_folder().glob("*.info.json")]
            permanent = weekly_keepers(record for _, record in records)
            for old, record in records:
                if record["id"] in permanent:
                    continue
                if record["id"] != identifier and datetime.fromisoformat(record["created_at"]) < cutoff:
                    item_paths(record["id"])[0].unlink(missing_ok=True)
                    old.unlink()
        return info


def read_archive(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if set(archive.namelist()) != {"manifest.json", "snapshot.json"} or len(archive.infolist()) != 2:
                raise ValueError("Expected only manifest.json and snapshot.json.")
            if sum(item.file_size for item in archive.infolist()) > EXPANDED_LIMIT or any(item.flag_bits & 1 for item in archive.infolist()):
                raise ValueError("Archive is too large or encrypted.")
            manifest = json.loads(archive.read("manifest.json"))
            raw = archive.read("snapshot.json")
        if not isinstance(manifest, dict) or manifest.get("format") != "fabric-conditioning" or manifest.get("version") != SCHEMA:
            raise ValueError("Unsupported backup format.")
        if hashlib.sha256(raw).hexdigest() != manifest.get("sha256"):
            raise ValueError("Snapshot checksum failed.")
        snapshot = json.loads(raw)
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("tables"), dict) or not isinstance(manifest.get("counts"), dict):
            raise ValueError("Expected a snapshot table map and count manifest.")
        known = {table.name for table in Base.metadata.sorted_tables}
        if set(snapshot["tables"]) != known or set(manifest["counts"]) != known:
            raise ValueError("Snapshot table set differs from this app version.")
        for name, records in snapshot["tables"].items():
            count = manifest["counts"][name]
            if not isinstance(records, list) or type(count) is not int or count < 0 or len(records) != count:
                raise ValueError("Snapshot row counts do not match its manifest.")
            if any(not isinstance(record, dict) for record in records):
                raise ValueError("Expected database row objects.")
        return manifest, snapshot
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError, RuntimeError, RecursionError, NotImplementedError) as error:
        raise HTTPException(422, "Invalid backup: " + str(error))


def admin_engine():
    return create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")


def drop_databases(admin, pattern, keep):
    """Remove this database's leftover restore copies; other databases sharing the cluster are untouched."""
    with admin.connect() as connection:
        names = connection.execute(text("SELECT datname FROM pg_database WHERE datname LIKE :pattern"),
                                   {"pattern": pattern.replace("_", r"\_") + "%"}).scalars().all()
        for name in set(names) - set(keep):
            try:
                connection.exec_driver_sql('DROP DATABASE IF EXISTS "' + name + '" WITH (FORCE)')
            except Exception:
                LOGGER.exception("Could not remove leftover restore database %s", name)


def create_staging(content):
    manifest, snapshot = read_archive(content)
    identifier = new_identifier()
    name = engine.url.database + "_restore_" + identifier.lower()
    admin = admin_engine()
    # Staged copies are only usable for a day; keep the ones a pending or in-flight restore still needs.
    pending = [json.loads(path.read_text()) for path in folder().glob("*.staged.json")]
    drop_databases(admin, engine.url.database + "_restore_", [
        record["database"] for record in pending if record["status"] == "applying" or (
            record["status"] == "validated" and utcnow() - datetime.fromisoformat(record["created_at"]) <= timedelta(days=1))])
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
            # Restored rows keep their numbers; new registrations continue after the highest one.
            for counted in NUMBERED:
                connection.exec_driver_sql(f"SELECT setval(pg_get_serial_sequence('{counted}', 'id'), "
                                           f"COALESCE((SELECT max(id) FROM {counted}), 0) + 1, false)")
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


def restore_result(record):
    return {"id": record["id"], "status": "restored", "counts": record["counts"],
            "pre_restore_backup": record["pre_restore_backup"], "previous_database_retained": True}


def staged_record(identifier):
    item_paths(identifier)  # Validate the opaque identifier before constructing a path.
    path = folder() / (identifier + ".staged.json")
    if not path.exists():
        raise HTTPException(404, "Validated restore not found.")
    record = json.loads(path.read_text())
    if record["status"] == "applying":
        admin = admin_engine()
        try:
            with admin.connect() as connection:
                committed = connection.execute(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_database "
                    "WHERE shobj_description(oid, 'pg_database') = :marker)"
                ), {"marker": "fabric-conditioning:restore:" + identifier}).scalar()
            # This marker commits in the same transaction as both database
            # renames. A later restore may retain that database under another
            # name, so its marker remains evidence even after it stops being
            # active. It resolves a lost response or failed journal write.
            record["status"] = "restored" if committed else "validated"
            try:
                save_json(path, record)
            except OSError:
                LOGGER.exception("Could not persist recovered restore status")
        finally:
            admin.dispose()
    return path, record


def carry_counters(stage):
    """Never issue a number twice: the restored database continues after the highest number the current one
    has issued. Labels may already be printed with specimen numbers newer than the backup, and kiosks and
    browsers may hold retry IDs reserved after it."""
    target = create_engine(engine.url.set(database=stage))
    try:
        with engine.connect() as active, target.begin() as restored:
            for row in active.execute(text("SELECT client, last_issued FROM request_counters")).mappings():
                restored.execute(text("INSERT INTO request_counters (client, last_issued) VALUES (:client, :last_issued) "
                                      "ON CONFLICT (client) DO UPDATE SET last_issued = GREATEST(request_counters.last_issued, EXCLUDED.last_issued)"),
                                 dict(row))
            for table in NUMBERED:
                sequence = active.execute(text(f"SELECT pg_get_serial_sequence('{table}', 'id')")).scalar()
                issued = active.execute(text(f"SELECT CASE WHEN is_called THEN last_value ELSE last_value - 1 END FROM {sequence}")).scalar()
                restored.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                                      f"GREATEST((SELECT max(id) FROM {table}), :issued, 0) + 1, false)"), {"issued": issued})
    finally:
        target.dispose()


def apply_staging(identifier):
    path, record = staged_record(identifier)
    if record["status"] == "restored":
        return restore_result(record)
    if record["status"] != "validated":
        raise HTTPException(409, "This restore has already been used.")
    if utcnow() - datetime.fromisoformat(record["created_at"]) > timedelta(days=1):
        raise HTTPException(409, "Restore validation expired. Import the backup again.")
    active = engine.url.database
    stage = record["database"]
    previous = active + "_previous_" + new_identifier().lower()
    if not all(re.fullmatch(r"[a-zA-Z0-9_]+", value or "") for value in [active, stage, previous]):
        raise HTTPException(409, "Database names are not supported for restore.")
    with LOCK:
        # Keep both a downloadable pre-restore checkpoint and the whole previous database.
        checkpoint = make_backup()
        carry_counters(stage)   # API traffic is drained here, so no number can be issued after this read
        record.update(status="applying", previous_database=previous, pre_restore_backup=checkpoint["id"])
        save_json(path, record)  # Durable intent must succeed before changing database names.
        engine.dispose()
        admin = admin_engine()
        try:
            # Database renames are transactional (unlike CREATE DATABASE). A
            # failure or process loss cannot leave the active name missing
            # between two separately committed renames.
            with admin.connect().execution_options(isolation_level="READ COMMITTED") as connection:
                with connection.begin():
                    for name in [active, stage]:
                        connection.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"), {"name": name})
                    connection.exec_driver_sql('ALTER DATABASE "' + active + '" RENAME TO "' + previous + '"')
                    connection.exec_driver_sql('ALTER DATABASE "' + stage + '" RENAME TO "' + active + '"')
                    connection.exec_driver_sql('COMMENT ON DATABASE "' + active + '" IS \'fabric-conditioning:restore:' + identifier + "'")
        except Exception:
            # A transaction-exit error can mean PostgreSQL committed but its
            # COMMIT response was lost. Keep the durable intent ambiguous;
            # staged_record() checks the transactional database marker before
            # classifying this as restored or safe to retry.
            LOGGER.exception("Restore outcome requires database marker reconciliation")
            raise
        finally:
            admin.dispose()
            engine.dispose()
        record.update(status="restored", previous_database=previous, pre_restore_backup=checkpoint["id"])
        try:
            save_json(path, record)
        except OSError:
            LOGGER.exception("Restore committed; durable database marker will recover its status")
        # Only the newest previous database is kept for recovery; older ones would otherwise pile up forever.
        admin = admin_engine()
        try:
            drop_databases(admin, active + "_previous_", [previous])
        finally:
            admin.dispose()
        return restore_result(record)


class BackupSettings(BaseModel):
    destination: str | None = Field(default=None, max_length=2000)
    retention_days: int = Field(default=30, ge=2, le=3650)
    daily_hour: int = Field(default=2, ge=0, le=23)
    enabled: bool = True


class RestoreConfirmation(BaseModel):
    confirmation: str


def storage_summary(items):
    """Bytes actually on disk for the listed archives, plus where they live."""
    total = 0
    for item in items:
        archive = archive_folder() / (item["id"] + ".zip")
        try:
            total += archive.stat().st_size
        except OSError:
            pass
    return {"folder": str(archive_folder()), "total_bytes": total, "archive_count": len(items)}


def is_host_request(request: Request):
    return is_local_client(request)


@router.get("")
def list_backups(request: Request):
    items = [json.loads(path.read_text()) for path in archive_folder().glob("*.info.json")]
    items.sort(key=lambda row: row["created_at"], reverse=True)
    config = settings()
    permanent = weekly_keepers(items)
    for item in items:
        item["keep_forever"] = item["id"] in permanent
        item["expires_at"] = None if item["keep_forever"] else (
            datetime.fromisoformat(item["created_at"]) + timedelta(days=config["retention_days"])).isoformat()
    now = utcnow().astimezone(ZoneInfo("America/New_York"))
    planned = datetime.combine(now.date(), clock_time(config["daily_hour"]), tzinfo=now.tzinfo)
    overdue = config["enabled"] and now >= planned and config.get("last_day") != now.date().isoformat()
    if not overdue and (now >= planned or config.get("last_day") == now.date().isoformat()):
        planned = datetime.combine(now.date() + timedelta(days=1), clock_time(config["daily_hour"]), tzinfo=now.tzinfo)
    storage = storage_summary(items)
    storage["can_open"] = sys.platform == "darwin" and is_host_request(request)
    return {"items": items, "settings": config, "timezone": "America/New_York", "storage": storage,
            "scheduler": {"enabled": config["enabled"], "last_success_at": config.get("last_success_at"),
                          "last_error": config.get("last_error"), "last_day": config.get("last_day"),
                          "next_run_at": planned.isoformat() if config["enabled"] else None,
                          "timezone": "America/New_York", "overdue": bool(overdue)}}


@router.put("/settings")
def update_settings(body: BackupSettings):
    value = body.model_dump()
    if value["destination"]:
        path = Path(value["destination"]).expanduser()
        if not path.is_absolute():
            raise HTTPException(422, "Use an absolute Mac folder path.")
        if path.resolve() in {folder().resolve(), archive_folder().resolve()}:
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


@router.post("/open-folder")
def open_backup_folder(request: Request):
    # Finder opens on the host Mac's own screen, so only a browser running on
    # that Mac may ask for it; staff computers and Pi stations get the path instead.
    if not is_host_request(request):
        raise HTTPException(403, "The backup folder can only be opened from a browser on the host Mac.")
    if sys.platform != "darwin":
        raise HTTPException(501, "Opening the backup folder is only supported on the macOS host.")
    try:
        subprocess.run(["/usr/bin/open", str(archive_folder())], check=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as error:
        LOGGER.warning("Could not open backup folder: %s", error)
        raise HTTPException(500, "Finder could not open the backup folder. Is someone logged in to the host Mac?")
    return {"opened": str(archive_folder())}


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
        # A disconnected browser can cancel its coroutine, but cannot cancel
        # work already running in the thread. Keep maintenance exclusive until
        # that database switch has actually finished.
        operation = asyncio.create_task(asyncio.to_thread(apply_staging, identifier))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            try:
                await operation
            finally:
                raise


@router.get("/imports/{identifier}")
def imported_backup_status(identifier: str):
    with LOCK:
        _, record = staged_record(identifier)
        if record["status"] == "restored":
            return restore_result(record)
        return {key: value for key, value in record.items()
                if key not in {"database", "previous_database", "pre_restore_backup"}}


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
        claimed = False
        try:
            async with self.condition:
                if self.maintenance:
                    raise HTTPException(409, "Another restore is already in progress.")
                self.maintenance = True
                claimed = True
                while self.active:
                    await self.condition.wait()
            yield
        finally:
            if claimed:
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
            LOGGER.exception("Scheduled checkpoint failed")
            try:
                value = settings()
                value["last_error"] = type(error).__name__ + ": backup failed; see server log."
                save_json(folder() / "settings.json", value)
            except Exception:
                # A permissions/disk problem must not kill the scheduler while
                # it is trying to report the original backup failure.
                LOGGER.exception("Could not persist the scheduled checkpoint error")
        await asyncio.sleep(60)
