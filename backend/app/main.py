import csv
import asyncio
import io
import os
from typing import Annotated
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Path as PathParam, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from urllib.parse import urlencode
from pydantic import ValidationError
from sqlalchemy import or_, select, text
from starlette.middleware.sessions import SessionMiddleware

from .auth import current_user, require_supervisor, valid_credentials
from .corrections import edit_transaction, process_correction, record_missed_movement, undo_transaction
from .db import SessionLocal, get_db, engine
from .backups import TrafficGate, daily_scheduler, router as backups_router
from .exports import router as exports_router
from .workbooks import router as workbooks_router
from .printing import router as printing_router
from .stacks import router as stacks_router
from .models import AuditEvent, Material, MovementRequest, PrepDraft, Project, Room, Specimen, Stack, Station, Treatment, utcnow
from .movements import process_movement
from .schemas import (REQUEST_ID, CorrectionBody, LoginBody, LookupBody, MissedMovementBody, MovementBody, PrepBody, PrepCommitBody,
                      RequestIdsBody, TransactionEditBody, TransactionUndoBody, split_request_id)
from .services import (TARGET_EVENT, commit_draft, draft_dict, event_dict, iso, query_stacks, reserve_request_numbers, resolve_code,
                       seed_config, specimen_time_log, stack_detail)


@asynccontextmanager
async def lifespan(app):
    app.state.traffic_gate = TrafficGate()
    with SessionLocal.begin() as db:
        seed_config(db)
    scheduler = None
    if not (engine.url.database or "").endswith("_test"):
        scheduler = asyncio.create_task(daily_scheduler())
    try:
        yield
    finally:
        if scheduler:
            scheduler.cancel()
            try:
                await scheduler
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Fabric Conditioning Tracker", version="2.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware,
                   secret_key=os.getenv("SESSION_SECRET", "classroom-demo-change-this-session-secret"),
                   same_site="strict", https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
                   max_age=8 * 60 * 60)
app.include_router(exports_router)
app.include_router(workbooks_router)
app.include_router(printing_router)
app.include_router(stacks_router)
app.include_router(backups_router)


class RestoreMaintenanceGate:
    """Count API requests through to the end of their response so a restore can drain them.

    Plain ASGI rather than @app.middleware: Starlette's BaseHTTPMiddleware drops file response bodies
    (the staff app, its assets and backup downloads) when uvicorn offers zero-copy file sends.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        gate = getattr(scope["app"].state, "traffic_gate", None) if scope["type"] == "http" else None
        restore = scope.get("method") == "POST" and path.startswith("/api/backups/imports/") and path.endswith("/restore")
        if not gate or not path.startswith("/api/") or restore:
            return await self.app(scope, receive, send)
        if not await gate.enter():
            response = JSONResponse(status_code=503, content={"detail": "Database restore in progress; retry after maintenance."},
                                    headers={"Retry-After": "5"})
            return await response(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            await gate.leave()


app.add_middleware(RestoreMaintenanceGate)


@app.get("/api/health")
def health(db=Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected", "server_time": iso(utcnow())}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unreachable"})


@app.get("/api/config")
def config(db=Depends(get_db)):
    rooms = [{"id": r.id, "name": r.name, "temperature_c": r.temperature_c,
              "relative_humidity": r.relative_humidity, "required_hours": r.required_hours,
              "temperature_tolerance_c": r.temperature_tolerance_c, "humidity_tolerance": r.humidity_tolerance}
             for r in db.scalars(select(Room).order_by(Room.name))]
    stations = [{"id": s.id, "name": s.name, "room_id": s.room_id}
                for s in db.scalars(select(Station).order_by(Station.name))]
    return {"rooms": rooms, "stations": stations, "treatments": list(db.scalars(select(Treatment.code).order_by(Treatment.code))),
            "display_timezone": "America/New_York", "server_time": iso(utcnow()),
            "conditioning_periods_per_specimen": 1}


@app.post("/api/request-ids")
def request_ids(body: RequestIdsBody, db=Depends(get_db)):
    """Reserve a block of retry IDs '<client>-<n>' for a kiosk station or for printing."""
    with db.begin():
        return reserve_request_numbers(db, body.client, body.count)


@app.post("/api/prep-drafts")
def create_draft(body: PrepBody, db=Depends(get_db)):
    with db.begin():
        draft = PrepDraft(headers={name: header.model_dump() for name, header in body.headers.items()},
                          rows=[r.model_dump() for r in body.rows])
        db.add(draft)
        db.flush()
        return draft_dict(draft)


def csv_draft(rows):
    """Lay a five-column CSV out as prep sheet rows: one worksheet per project."""
    sheets = {}
    for row in rows:
        project = row["project_number"].strip()
        sheets.setdefault(project, []).append(row)
    headers, sheet_rows = {}, []
    for project, items in sheets.items():
        name = "CSV" if len(sheets) == 1 else "CSV " + project
        headers[name] = {"project_number": project}
        previous = None
        for row in items:
            test = row["test_code"].strip()
            sheet_rows.append({"sheet": name, "test": "" if test == previous else test, "conditions": row["preconditioning_code"].strip(),
                               "conditioning": row["specimen_count"].strip(), "sample": row["material_ref"].strip()})
            previous = test
    return PrepBody(headers=headers, rows=sheet_rows)


@app.post("/api/prep-drafts/csv")
async def import_csv(file: UploadFile = File(...), db=Depends(get_db)):
    content = await file.read(2_000_001)
    if len(content) > 2_000_000:
        raise HTTPException(413, "CSV must be smaller than 2 MB.")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        expected = {"project_number", "material_ref", "test_code", "preconditioning_code", "specimen_count"}
        if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != expected:
            raise HTTPException(422, "CSV headers must be exactly: project_number,material_ref,test_code,preconditioning_code,specimen_count")
        rows = []
        for row in reader:
            if len(rows) >= 100:
                raise HTTPException(422, "CSV can contain at most 100 stacks.")
            if None in row or any(value is None for value in row.values()):
                raise HTTPException(422, "Every CSV row must have all five values.")
            rows.append(row)
        if not rows:
            raise HTTPException(422, "The CSV has no rows.")
        body = csv_draft(rows)
    except UnicodeDecodeError:
        raise HTTPException(422, "CSV must use UTF-8 encoding.")
    except (ValidationError, csv.Error) as error:
        raise HTTPException(422, str(error))
    return create_draft(body, db)


@app.get("/api/prep-drafts/{draft_id}")
def get_draft(draft_id: int, db=Depends(get_db)):
    draft = db.get(PrepDraft, draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found.")
    return draft_dict(draft)


@app.put("/api/prep-drafts/{draft_id}")
def update_draft(draft_id: int, body: PrepBody, db=Depends(get_db)):
    with db.begin():
        draft = db.scalar(select(PrepDraft).where(PrepDraft.id == draft_id).with_for_update())
        if not draft:
            raise HTTPException(404, "Draft not found.")
        if draft.committed:
            raise HTTPException(409, "A registered prep sheet cannot be edited. Edit the registered stacks instead.")
        if body.expected_revision is not None and draft.revision != body.expected_revision:
            raise HTTPException(409, "This prep sheet changed in another browser. Reload its latest values before saving.")
        draft.headers = {name: header.model_dump() for name, header in body.headers.items()}
        draft.rows = [r.model_dump() for r in body.rows]
        draft.revision += 1
        db.flush()
        return draft_dict(draft)


@app.post("/api/prep-drafts/{draft_id}/commit")
def register_draft(draft_id: int, body: PrepCommitBody | None = None, db=Depends(get_db)):
    with db.begin():
        draft = db.scalar(select(PrepDraft).where(PrepDraft.id == draft_id).with_for_update())
        if not draft:
            raise HTTPException(404, "Draft not found.")
        if not draft.committed and body and body.expected_revision is not None and draft.revision != body.expected_revision:
            raise HTTPException(409, "This prep sheet changed before registration. Reload its latest values.")
        return {"stack_ids": commit_draft(db, draft)}


@app.get("/api/stacks")
def list_stacks(q: str | None = None, project_number: str | None = None, material_ref: str | None = None,
                test_code: str | None = None, preconditioning_code: str | None = None,
                room_id: str | None = None, status: str | None = None, db=Depends(get_db)):
    items = query_stacks(db, q=q, project_number=project_number, material_ref=material_ref,
                         test_code=test_code, preconditioning_code=preconditioning_code, room_id=room_id, status=status)
    return {"items": items, "total": len(items), "server_time": iso(utcnow())}


@app.get("/api/stacks/{stack_id}")
def get_stack(stack_id: int, db=Depends(get_db)):
    stack = db.get(Stack, stack_id)
    if not stack:
        raise HTTPException(404, "Stack not found.")
    return stack_detail(db, stack)


@app.get("/api/specimens/{specimen_id}")
def get_specimen(specimen_id: int, db=Depends(get_db)):
    specimen = db.get(Specimen, specimen_id)
    if specimen is None:
        raise HTTPException(404, "Specimen not found.")
    stack = db.get(Stack, specimen.stack_id)
    detail = stack_detail(db, stack, specimen.id, include_events=False)
    item = next(p for p in detail["specimens"] if p["id"] == specimen.id)
    return {**item, "stack_id": stack.id, "project_number": detail["project_number"],
            "material_ref": detail["material_ref"], "test_code": detail["test_code"],
            "preconditioning_code": detail["preconditioning_code"], "metadata": detail["metadata"],
            "raw_json_time": specimen_time_log(db, specimen.id), "server_time": iso(utcnow())}


@app.post("/api/lookup")
def lookup(body: LookupBody, db=Depends(get_db)):
    stack, scanned = resolve_code(db, body.code)
    return stack_detail(db, stack, scanned.id if scanned else None)


@app.post("/api/movements")
def movement(body: MovementBody, db=Depends(get_db)):
    with db.begin():
        return process_movement(db, body)


@app.get("/api/movement-requests/{request_id}")
def movement_status(request_id: Annotated[str, PathParam(pattern=REQUEST_ID)], db=Depends(get_db)):
    request = db.get(MovementRequest, split_request_id(request_id))
    if not request:
        raise HTTPException(404, "Request not recorded. Retry the original request ID and unchanged details.")
    return request.result


@app.get("/api/auth/me")
def auth_me(request: Request):
    username = current_user(request)
    return {"authenticated": bool(username), "username": username}


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    if not valid_credentials(body.username, body.password):
        raise HTTPException(401, "Incorrect supervisor username or password.")
    request.session.clear()
    request.session["username"] = body.username
    return {"authenticated": True, "username": body.username}


@app.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"authenticated": False, "username": None}


@app.get("/api/transactions")
@app.get("/api/events")
def events(stack_id: int | None = None, room_id: str | None = None, station_id: str | None = None,
           limit: int = Query(default=100, ge=1, le=500), q: str | None = None,
           kind: str | None = Query(default=None, max_length=32), db=Depends(get_db)):
    query = select(AuditEvent).order_by(AuditEvent.recorded_at.desc(), AuditEvent.id.desc())
    if stack_id:
        query = query.where(AuditEvent.stack_id == stack_id)
    if room_id:
        # Events recorded in this room, plus events a staff modification moved into it (both indexed).
        moved_here = select(TARGET_EVENT).where(
            AuditEvent.kind == "modification", AuditEvent.room_id == room_id)
        query = query.where(or_(AuditEvent.room_id == room_id, AuditEvent.id.in_(moved_here)))
    if station_id:
        query = query.where(AuditEvent.payload["station_id"].astext == station_id)
    if kind:
        query = query.where(AuditEvent.kind == kind)
    if q:
        pattern = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.join(Stack, Stack.id == AuditEvent.stack_id).join(Project).join(Material).where(or_(
            Project.project_number.ilike(pattern, escape="\\"), Material.material_ref.ilike(pattern, escape="\\"),
            Stack.test_code.ilike(pattern, escape="\\"), Stack.preconditioning_code.ilike(pattern, escape="\\"),
            *([AuditEvent.id == int(q)] if q.strip().isdigit() and len(q.strip()) < 18 else [])))
    rows = list(db.scalars(query.limit(limit + 1)))
    items = [event_dict(db, event) for event in rows[:limit]]
    if room_id:  # a later modification may have moved an event recorded here to another room
        items = [item for item in items if item["room_id"] == room_id]
    return {"items": items, "limit": limit, "has_more": len(rows) > limit, "server_time": iso(utcnow())}


@app.get("/api/transactions/{event_id}")
def get_transaction(event_id: int, db=Depends(get_db)):
    event = db.get(AuditEvent, event_id)
    if event is None:
        raise HTTPException(404, "Transaction not found.")
    return event_dict(db, event)


@app.post("/api/corrections")
def correct(body: CorrectionBody, db=Depends(get_db)):
    with db.begin():
        return process_correction(db, body, "project-staff")


@app.post("/api/transactions/missed")
def missed_transaction(body: MissedMovementBody, db=Depends(get_db)):
    with db.begin():
        return record_missed_movement(db, body)


@app.patch("/api/transactions/{event_id}")
def update_transaction(event_id: int, body: TransactionEditBody, db=Depends(get_db)):
    with db.begin():
        return edit_transaction(db, event_id, body)


@app.post("/api/transactions/{event_id}/undo")
def undo_movement(event_id: int, body: TransactionUndoBody, db=Depends(get_db)):
    with db.begin():
        return undo_transaction(db, event_id, body)


frontend_dir = Path(os.getenv("FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend/dist")))
if (frontend_dir / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def web_app():
    index = frontend_dir / "index.html"
    if not index.is_file():
        return JSONResponse(status_code=503, content={"detail": "Build the staff frontend first: cd frontend && npm run build"})
    return FileResponse(index, headers={"Cache-Control": "no-store"})


@app.get("/kiosk", include_in_schema=False)
@app.get("/station.html", include_in_schema=False)
def kiosk_app(station: str | None = None):
    query = "?" + urlencode({"station": station}) if station else ""
    return RedirectResponse("/" + query + "#kiosk", status_code=307,
                            headers={"Cache-Control": "no-store"})
