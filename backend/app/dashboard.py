"""Staff dashboard and batch tracking.

A *batch* is one committed prep draft (one prep-sheet upload or manual entry). Each
physical piece moves registered -> labeled -> in room -> ready -> finished. The
dashboard aggregates that pipeline in a handful of set queries instead of calling
stack_detail per stack, so it stays fast as years of finished work accumulate.
"""
from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import exists, select
from sqlalchemy.orm import selectinload

from .db import get_db
from .models import AuditEvent, ConditioningSession, PrepDraft, PrintJob, Room, Specimen, Stack, utcnow
from .printing import PRINT_REQUEST
from .schemas import split_request_id
from .services import check_request_number, conditioning_due, iso

router = APIRouter(prefix="/api", tags=["Dashboard"])
CONFIRMED_PRINTS = ("submitted", "manual")
UNCONFIRMED_PRINTS = ("submitting", "unknown", "failed")
BUCKETS = (("under_1d", timedelta(days=1)), ("1_to_3d", timedelta(days=3)), ("over_3d", None))


def _bucket(waited):
    for name, limit in BUCKETS:
        if limit is None or waited < limit:
            return name


def batch_label(draft):
    name = (draft.source or {}).get("filename") or "Manual entry"
    return name


def _batch_map(db):
    """stack_id -> committed draft, plus the drafts themselves (small rows)."""
    drafts = list(db.scalars(select(PrepDraft).where(PrepDraft.committed.is_(True)).order_by(PrepDraft.created_at.desc())))
    by_stack = {}
    for draft in drafts:
        for stack_id in draft.stack_ids or []:
            by_stack.setdefault(int(stack_id), draft)
    return drafts, by_stack


def open_stack_ids(db):
    """Stacks with at least one piece not yet finished (never entered, or inside a room now)."""
    finished = exists().where(ConditioningSession.specimen_id == Specimen.id,
                              ConditioningSession.voided.is_(False), ConditioningSession.exited_at.is_not(None))
    return set(db.scalars(select(Specimen.stack_id).where(~finished).distinct()))


def stack_states(db, stack_ids):
    """Per-stack piece counts through the pipeline, computed from specimens, sessions and print jobs."""
    if not stack_ids:
        return {}
    now = utcnow()
    hours = {room.id: room.required_hours for room in db.scalars(select(Room))}
    stacks = {stack.id: stack for stack in db.scalars(
        select(Stack).options(selectinload(Stack.project), selectinload(Stack.material)).where(Stack.id.in_(stack_ids)))}
    pieces = list(db.execute(select(Specimen.id, Specimen.stack_id).where(
        Specimen.stack_id.in_(stack_ids), Specimen.removed_at.is_(None))))   # removed pieces are not in the pipeline
    piece_ids = [row.id for row in pieces]
    sessions = defaultdict(list)
    for session in db.scalars(select(ConditioningSession).where(
            ConditioningSession.specimen_id.in_(piece_ids), ConditioningSession.voided.is_(False))
            .order_by(ConditioningSession.entered_at, ConditioningSession.id)):
        sessions[session.specimen_id].append(session)
    # Print jobs can only follow registration, so the oldest stack bounds the scan.
    oldest = min((stack.created_at for stack in stacks.values()), default=now)
    labeled_at, attempt = {}, {}
    for job in db.scalars(select(PrintJob).where(PrintJob.created_at >= oldest - timedelta(minutes=5))
                          .order_by(PrintJob.created_at)):
        selected = (job.options or {}).get("selected_specimen_ids") or []
        for piece_id in selected:
            if job.status in CONFIRMED_PRINTS:
                labeled_at[piece_id] = job.updated_at or job.created_at
            elif job.status in UNCONFIRMED_PRINTS:
                attempt[piece_id] = job.status
    result = {}
    for stack_id, stack in stacks.items():
        result[stack_id] = {
            "id": stack_id, "project_number": stack.project.project_number, "material_ref": stack.material.material_ref,
            "test_code": stack.test_code, "preconditioning_code": stack.preconditioning_code,
            "registered_at": iso(stack.created_at), "is_demo": stack.is_demo,
            "total": 0, "labeled": 0, "unlabeled_waiting": 0, "labeled_waiting": 0, "entered": 0,
            "inside": 0, "ready": 0, "finished": 0, "rooms": defaultdict(lambda: {"inside": 0, "ready": 0, "ready_since": None}),
            "labeled_at": None, "print_attempt": None,
        }
    for piece in pieces:
        row = result[piece.stack_id]
        row["total"] += 1
        history = sessions.get(piece.id, [])
        active = next((s for s in history if s.exited_at is None), None)
        labeled = piece.id in labeled_at or bool(history)  # a scan proves a label exists
        row["labeled"] += labeled
        if piece.id in labeled_at:
            stamp = labeled_at[piece.id]
            row["labeled_at"] = max(row["labeled_at"], stamp) if row["labeled_at"] else stamp
        if not history:
            if labeled:
                row["labeled_waiting"] += 1
            else:
                row["unlabeled_waiting"] += 1
                row["print_attempt"] = row["print_attempt"] or attempt.get(piece.id)
            continue
        row["entered"] += 1
        if active is None:
            row["finished"] += 1
            continue
        room = row["rooms"][active.room_id]
        row["inside"] += 1
        room["inside"] += 1
        due = conditioning_due(active.entered_at, hours.get(active.room_id, 24))
        if due <= now:
            row["ready"] += 1
            room["ready"] += 1
            room["ready_since"] = min(room["ready_since"], due) if room["ready_since"] else due
    for row in result.values():
        row["labeled_at"] = iso(row["labeled_at"])
        row["rooms"] = dict(row["rooms"])
    return result


def stages(rows):
    keys = ("total", "labeled", "entered", "inside", "ready", "finished")
    return {key: sum(row[key] for row in rows) for key in keys}


def _public(row, batch=None):
    keep = {key: value for key, value in row.items() if key != "rooms"}
    if batch is not None:
        keep["batch"] = {"id": batch.id, "label": batch_label(batch)}
    return keep


def _matches(row, batch, project_number, test_code, preconditioning_code, batch_id, q):
    if project_number and row["project_number"] != project_number:
        return False
    if test_code and row["test_code"] != test_code:
        return False
    if preconditioning_code and row["preconditioning_code"] != preconditioning_code:
        return False
    if batch_id and (batch is None or batch.id != batch_id):
        return False
    if q:
        text = " ".join([row["project_number"], row["material_ref"], row["test_code"], row["preconditioning_code"],
                         batch_label(batch) if batch else ""]).lower()
        if q.lower() not in text:
            return False
    return True


@router.get("/dashboard")
def dashboard(room_id: str | None = None, project_number: str | None = None, test_code: str | None = None,
              preconditioning_code: str | None = None, batch_id: int | None = Query(default=None, ge=1),
              q: str | None = Query(default=None, max_length=200), db=Depends(get_db)):
    now = utcnow()
    drafts, by_stack = _batch_map(db)
    open_ids = open_stack_ids(db)
    rows = stack_states(db, open_ids)
    rooms = {room.id: room for room in db.scalars(select(Room).order_by(Room.name))}

    facets = {"projects": sorted({r["project_number"] for r in rows.values()}),
              "tests": sorted({r["test_code"] for r in rows.values()}),
              "treatments": sorted({r["preconditioning_code"] for r in rows.values()})}
    selected = {sid: row for sid, row in rows.items()
                if _matches(row, by_stack.get(sid), project_number, test_code, preconditioning_code, batch_id, q)}
    # Room chips follow every filter except the room itself, so they show where the filtered work is.
    chips = {room.id: {"id": room.id, "name": room.name, "inside": 0, "ready": 0} for room in rooms.values()}
    for row in selected.values():
        for rid, counts in row["rooms"].items():
            if rid in chips:
                chips[rid]["inside"] += counts["inside"]
                chips[rid]["ready"] += counts["ready"]

    # 1. Pipeline gaps. Pieces never entered have no room yet, so the room filter does not hide them.
    needs_labels = sorted((_public(r, by_stack.get(sid)) for sid, r in selected.items() if r["unlabeled_waiting"]),
                          key=lambda r: r["registered_at"])
    not_scanned = sorted((_public(r, by_stack.get(sid)) for sid, r in selected.items() if r["labeled_waiting"]),
                         key=lambda r: r["labeled_at"] or r["registered_at"])

    # 2. Ready to remove, grouped by room, longest-waiting first.
    ready_rooms = []
    for rid, room in rooms.items():
        if room_id and rid != room_id:
            continue
        items, buckets = [], {name: 0 for name, _ in BUCKETS}
        for sid, row in selected.items():
            counts = row["rooms"].get(rid)
            if not counts or not counts["ready"]:
                continue
            waited = now - counts["ready_since"]
            buckets[_bucket(waited)] += counts["ready"]
            items.append({**_public(row, by_stack.get(sid)), "room_ready": counts["ready"], "room_inside": counts["inside"],
                          "ready_since": iso(counts["ready_since"]), "waiting_seconds": int(waited.total_seconds()),
                          "age_bucket": _bucket(waited)})
        items.sort(key=lambda item: item["ready_since"])
        ready_rooms.append({"room_id": rid, "room_name": room.name, "stacks": len(items),
                            "pieces": sum(item["room_ready"] for item in items),
                            "oldest_ready_since": items[0]["ready_since"] if items else None,
                            "buckets": buckets, "items": items})

    # 3. Batches: open ones (any unfinished stack) with stage counts, plus the latest closed ones.
    open_batches, closed = [], []
    for draft in drafts:
        ids = [int(i) for i in draft.stack_ids or []]
        if batch_id and draft.id != batch_id:
            continue
        if any(i in open_ids for i in ids):
            open_batches.append(draft)
        elif len(closed) < 5:
            closed.append(draft)
    extra = {i for draft in open_batches + closed for i in draft.stack_ids or []} - set(rows)
    every = {**rows, **stack_states(db, extra)} if extra else rows
    batches = []
    for draft, is_open in [(d, True) for d in open_batches] + [(d, False) for d in closed]:
        members = [every[int(i)] for i in draft.stack_ids or [] if int(i) in every]
        batches.append({"id": draft.id, "label": batch_label(draft), "created_at": iso(draft.created_at),
                        "open": is_open, "stacks": len(members), "stages": stages(members)})

    return {"server_time": iso(now), "filters": {"room_id": room_id, "project_number": project_number,
                                                  "test_code": test_code, "preconditioning_code": preconditioning_code,
                                                  "batch_id": batch_id, "q": q},
            "rooms": list(chips.values()), "facets": {**facets, "batches": [{"id": d.id, "label": batch_label(d), "created_at": iso(d.created_at)} for d in open_batches]},
            "gaps": {"needs_labels": needs_labels, "not_scanned_in": not_scanned},
            "ready": ready_rooms, "batches": batches}


@router.get("/batches/{draft_id}")
def batch_summary(draft_id: int, db=Depends(get_db)):
    draft = db.get(PrepDraft, draft_id)
    if draft is None or not draft.committed:
        raise HTTPException(404, "Batch not found or not registered yet.")
    ids = [int(i) for i in draft.stack_ids or []]
    rows = stack_states(db, ids)
    members = [rows[i] for i in ids if i in rows]
    summary = stages(members)
    return {"id": draft.id, "label": batch_label(draft), "created_at": iso(draft.created_at),
            "open": summary["finished"] < summary["total"], "stages": summary,
            "stacks": [{**_public(row), "rooms": row["rooms"] and {k: {**v, "ready_since": iso(v["ready_since"])} for k, v in row["rooms"].items()}}
                       for row in members],
            "server_time": iso(utcnow())}


class LabelConfirmBody(BaseModel):
    stack_ids: list[int] = Field(min_length=1, max_length=500)
    specimen_ids: list[int] | None = Field(default=None, max_length=10000)  # default: every piece of the stacks
    request_id: str = Field(pattern=PRINT_REQUEST)   # 'print-<n>', reserved from /api/request-ids like a print
    note: str | None = Field(default=None, max_length=500)


@router.post("/labels/confirm")
def confirm_labels(body: LabelConfirmBody, db=Depends(get_db)):
    """Record labels printed outside the app's print queue (e.g. from the PDF)."""
    request_id = body.request_id
    number = split_request_id(request_id)[1]
    with db.begin():
        check_request_number(db, "print", number)
        existing = db.get(PrintJob, number, with_for_update=True)
        if existing:
            if existing.status != "manual":
                raise HTTPException(409, f"{request_id} is already a print job. Reserve a new request ID.")
            return {"request_id": request_id, "status": existing.status, "stack_ids": existing.stack_ids}
        ids = sorted(set(body.stack_ids))
        found = set(db.scalars(select(Stack.id).where(Stack.id.in_(ids))))
        if found != set(ids):
            raise HTTPException(404, "One or more stacks were not found.")
        pieces = list(db.execute(select(Specimen.id, Specimen.stack_id).where(Specimen.stack_id.in_(ids), Specimen.removed_at.is_(None))
                                 .order_by(Specimen.stack_id, Specimen.number)))
        if body.specimen_ids is not None:
            wanted = set(body.specimen_ids)
            if not wanted <= {p.id for p in pieces}:
                raise HTTPException(422, "Selected labels must belong to the listed stacks.")
            pieces = [p for p in pieces if p.id in wanted]
            ids = sorted({p.stack_id for p in pieces}) or ids
        db.add(PrintJob(number=number, fingerprint="manual", status="manual", printer_queue=None, stack_ids=ids,
                        options={"selected_specimen_ids": [p.id for p in pieces], "source": "manual_confirmation",
                                 "label_count": len(pieces)}))
        for stack_id in ids:
            if not any(p.stack_id == stack_id for p in pieces):
                continue
            db.add(AuditEvent(stack_id=stack_id, kind="label_print", actor="staff", reason=body.note,
                              specimen_ids=[p.id for p in pieces if p.stack_id == stack_id],
                              payload={"print_request_id": request_id, "status": "manual",
                                       "printer_queue": None, "physical_print_confirmed": True}))
        db.flush()
    return {"request_id": request_id, "status": "manual", "stack_ids": ids, "label_count": len(pieces)}
