import csv
import io
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select, text
from starlette.middleware.sessions import SessionMiddleware

from .auth import current_user, require_supervisor, valid_credentials
from .corrections import process_correction
from .db import SessionLocal, get_db
from .exports import router as exports_router
from .models import AuditEvent, MovementRequest, PrepDraft, Room, Stack, Station, Treatment
from .movements import process_movement
from .schemas import CorrectionBody, LoginBody, LookupBody, MovementBody, PrepBody
from .services import commit_draft, draft_dict, event_dict, query_stacks, resolve_code, seed_config, stack_detail


@asynccontextmanager
async def lifespan(app):
    with SessionLocal.begin() as db:
        seed_config(db)
    yield


app = FastAPI(title="Fabric Conditioning Tracker", version="1.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware,
                   secret_key=os.getenv("SESSION_SECRET", "classroom-demo-change-this-session-secret"),
                   same_site="strict", https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
                   max_age=8 * 60 * 60)
app.include_router(exports_router)


@app.get("/api/health")
def health(db=Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
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
            "display_timezone": "America/New_York"}


@app.post("/api/prep-drafts")
def create_draft(body: PrepBody, db=Depends(get_db)):
    with db.begin():
        draft = PrepDraft(rows=[r.model_dump() for r in body.rows])
        db.add(draft)
        db.flush()
        return draft_dict(draft)


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
        body = PrepBody(rows=rows)
    except UnicodeDecodeError:
        raise HTTPException(422, "CSV must use UTF-8 encoding.")
    except (ValidationError, csv.Error) as error:
        raise HTTPException(422, str(error))
    return create_draft(body, db)


@app.get("/api/prep-drafts/{draft_id}")
def get_draft(draft_id: str, db=Depends(get_db)):
    draft = db.get(PrepDraft, draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found.")
    return draft_dict(draft)


@app.put("/api/prep-drafts/{draft_id}")
def update_draft(draft_id: str, body: PrepBody, db=Depends(get_db)):
    with db.begin():
        draft = db.scalar(select(PrepDraft).where(PrepDraft.id == draft_id).with_for_update())
        if not draft:
            raise HTTPException(404, "Draft not found.")
        if draft.committed:
            raise HTTPException(409, "A committed registration cannot be edited. Start a new draft.")
        draft.rows = [r.model_dump() for r in body.rows]
        db.flush()
        return draft_dict(draft)


@app.post("/api/prep-drafts/{draft_id}/commit")
def register_draft(draft_id: str, db=Depends(get_db)):
    with db.begin():
        draft = db.scalar(select(PrepDraft).where(PrepDraft.id == draft_id).with_for_update())
        if not draft:
            raise HTTPException(404, "Draft not found.")
        return {"stack_ids": commit_draft(db, draft)}


@app.get("/api/stacks")
def list_stacks(q: str | None = None, project_number: str | None = None, material_ref: str | None = None,
                test_code: str | None = None, preconditioning_code: str | None = None,
                room_id: str | None = None, status: str | None = None, db=Depends(get_db)):
    items = query_stacks(db, q=q, project_number=project_number, material_ref=material_ref,
                         test_code=test_code, preconditioning_code=preconditioning_code, room_id=room_id, status=status)
    return {"items": items, "total": len(items)}


@app.get("/api/stacks/{stack_id}")
def get_stack(stack_id: str, db=Depends(get_db)):
    stack = db.get(Stack, stack_id)
    if not stack:
        raise HTTPException(404, "Stack not found.")
    return stack_detail(db, stack)


@app.post("/api/lookup")
def lookup(body: LookupBody, db=Depends(get_db)):
    stack, scanned = resolve_code(db, body.code)
    return stack_detail(db, stack, scanned.id if scanned else None)


@app.post("/api/movements")
def movement(body: MovementBody, db=Depends(get_db)):
    with db.begin():
        return process_movement(db, body)


@app.get("/api/movement-requests/{request_id}")
def movement_status(request_id: str, db=Depends(get_db)):
    request = db.get(MovementRequest, request_id)
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


@app.get("/api/events")
def events(stack_id: str | None = None, db=Depends(get_db)):
    query = select(AuditEvent).order_by(AuditEvent.recorded_at.desc(), AuditEvent.id)
    if stack_id:
        query = query.where(AuditEvent.stack_id == stack_id)
    return {"items": [event_dict(db, e) for e in db.scalars(query)]}


@app.post("/api/corrections")
def correct(body: CorrectionBody, username=Depends(require_supervisor), db=Depends(get_db)):
    with db.begin():
        return process_correction(db, body, username)
