from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, or_
from sqlalchemy.dialects.postgresql import insert

from .models import (AuditEvent, ConditioningSession, Material, PrepDraft, Project,
                     Room, Specimen, Stack, Station, Treatment, utcnow, uuid_str)


def iso(value):
    return value.isoformat() if value is not None else None


def session_dict(session):
    return {"id": session.id, "room_id": session.room_id,
            "entered_at": iso(session.entered_at), "exited_at": iso(session.exited_at),
            "recorded_at": iso(session.recorded_at), "source": session.source,
            "corrected": session.corrected, "incomplete": session.incomplete,
            "voided": session.voided, "required_seconds": session.room.required_hours * 3600,
            "early_exit_acknowledged": session.early_exit_acknowledged}


def sessions_for(db, specimen_id, include_voided=False):
    query = select(ConditioningSession).where(ConditioningSession.specimen_id == specimen_id)
    if not include_voided:
        query = query.where(ConditioningSession.voided.is_(False))
    return list(db.scalars(query.order_by(ConditioningSession.entered_at, ConditioningSession.id)))


def current_session(db, specimen_id):
    return db.scalar(select(ConditioningSession).where(
        ConditioningSession.specimen_id == specimen_id,
        ConditioningSession.exited_at.is_(None), ConditioningSession.voided.is_(False)))


def snapshot(db, specimens):
    return {p.id: [session_dict(s) for s in sessions_for(db, p.id, include_voided=True)] for p in specimens}


def event_dict(db, event):
    undone = db.scalar(select(AuditEvent.id).where(
        AuditEvent.kind == "undo", AuditEvent.payload["event_id"].astext == event.id)) is not None
    return {"id": event.id, "kind": event.kind, "direction": event.direction,
            "actor": event.actor, "reason": event.reason, "stack_id": event.stack_id,
            "specimen_ids": event.specimen_ids, "affected_count": len(event.specimen_ids),
            "recorded_at": iso(event.recorded_at), "payload": event.payload, "undone": undone}


def stack_detail(db, stack, scanned_specimen_id=None, include_events=True):
    now = utcnow()
    specimens = []
    for specimen in db.scalars(select(Specimen).where(Specimen.stack_id == stack.id).order_by(Specimen.number)):
        sessions = sessions_for(db, specimen.id)
        active = next((s for s in sessions if s.exited_at is None), None)
        due = active.entered_at + timedelta(hours=active.room.required_hours) if active else None
        specimens.append({"id": specimen.id, "number": specimen.number,
                          "state": "IN" if active else "OUT", "room_id": active.room_id if active else None,
                          "entered_at": iso(active.entered_at) if active else None, "due_at": iso(due),
                          "elapsed_seconds": max(0, (now - active.entered_at).total_seconds()) if active else 0,
                          "ready": bool(due and due <= now), "revision": specimen.revision,
                          "sessions": [session_dict(s) for s in sessions]})
    locations = {(p["state"], p["room_id"]) for p in specimens}
    status = "MIXED" if len(locations) > 1 else (specimens[0]["state"] if specimens else "OUT")
    result = {"id": stack.id, "project_number": stack.project.project_number,
              "material_ref": stack.material.material_ref, "test_code": stack.test_code,
              "preconditioning_code": stack.preconditioning_code, "version": stack.version,
              "created_at": iso(stack.created_at), "is_demo": stack.is_demo,
              "specimen_count": len(specimens), "status": status,
              "specimens": specimens, "scanned_specimen_id": scanned_specimen_id}
    if include_events:
        result["events"] = [event_dict(db, e) for e in db.scalars(select(AuditEvent).where(
            AuditEvent.stack_id == stack.id).order_by(AuditEvent.recorded_at.desc(), AuditEvent.id))]
    return result


def query_stacks(db, q=None, project_number=None, material_ref=None, test_code=None,
                 preconditioning_code=None, room_id=None, status=None):
    query = select(Stack).join(Project).join(Material)
    if q:
        pattern = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(Project.project_number.ilike(pattern, escape="\\"),
                               Material.material_ref.ilike(pattern, escape="\\"),
                               Stack.test_code.ilike(pattern, escape="\\")))
    for field, value in [(Project.project_number, project_number), (Material.material_ref, material_ref),
                         (Stack.test_code, test_code), (Stack.preconditioning_code, preconditioning_code)]:
        if value:
            query = query.where(field == value)
    items = []
    for stack in db.scalars(query.order_by(Stack.created_at.desc(), Stack.id)):
        detail = stack_detail(db, stack, include_events=False)
        if room_id and not any(p["room_id"] == room_id for p in detail["specimens"]):
            continue
        if status:
            wanted = status.upper()
            if wanted in {"IN", "OUT", "MIXED"} and detail["status"] != wanted:
                continue
            if wanted == "READY" and not any(p["ready"] for p in detail["specimens"]):
                continue
            if wanted == "CONDITIONING" and not any(p["state"] == "IN" and not p["ready"] for p in detail["specimens"]):
                continue
        items.append(detail)
    return items


def resolve_code(db, code):
    parts = code.strip().split(":")
    if len(parts) != 3 or parts[0] != "UL1" or parts[1] not in {"P", "S"}:
        raise HTTPException(400, "Invalid label. Expected UL1:P:<uuid> or UL1:S:<uuid>.")
    try:
        identifier = str(UUID(parts[2]))
    except ValueError:
        raise HTTPException(400, "Invalid label identifier.")
    specimen = db.get(Specimen, identifier) if parts[1] == "P" else None
    stack = db.get(Stack, specimen.stack_id if specimen else identifier) if parts[1] == "S" or specimen else None
    if stack is None:
        raise HTTPException(404, "This label is not registered.")
    return stack, specimen


def draft_dict(draft):
    return {"id": draft.id, "rows": draft.rows, "committed": draft.committed, "stack_ids": draft.stack_ids}


def commit_draft(db, draft, is_demo=False):
    if draft.committed:
        return draft.stack_ids
    stack_ids = []
    for row in draft.rows:
        db.execute(insert(Project).values(id=uuid_str(), project_number=row["project_number"]).on_conflict_do_nothing())
        db.execute(insert(Material).values(id=uuid_str(), material_ref=row["material_ref"]).on_conflict_do_nothing())
        db.execute(insert(Treatment).values(code=row["preconditioning_code"]).on_conflict_do_nothing())
        project = db.scalar(select(Project).where(Project.project_number == row["project_number"]))
        material = db.scalar(select(Material).where(Material.material_ref == row["material_ref"]))
        stack = Stack(project_id=project.id, material_id=material.id, test_code=row["test_code"],
                      preconditioning_code=row["preconditioning_code"], is_demo=is_demo)
        db.add(stack)
        db.flush()
        specimens = [Specimen(stack_id=stack.id, number=i) for i in range(1, row["specimen_count"] + 1)]
        db.add_all(specimens)
        db.flush()
        db.add(AuditEvent(stack_id=stack.id, kind="registration", actor="prep",
                          specimen_ids=[p.id for p in specimens], payload={"draft_id": draft.id, "row": row}))
        stack_ids.append(stack.id)
    draft.committed = True
    draft.stack_ids = stack_ids
    db.flush()
    return stack_ids


def seed_config(db):
    # Nominal conditioning settings from the project outline (tolerances remain in room procedures).
    rooms = [
        {"id": "shared", "name": "Shared conditioning room", "temperature_c": 23, "temperature_tolerance_c": 1, "relative_humidity": 65, "humidity_tolerance": 2, "required_hours": 24},
        {"id": "ppe", "name": "PPE conditioning room", "temperature_c": 23, "temperature_tolerance_c": 3, "relative_humidity": 65, "humidity_tolerance": 5, "required_hours": 24},
        {"id": "footwear", "name": "Footwear conditioning room", "temperature_c": 21, "temperature_tolerance_c": 3, "relative_humidity": 50, "humidity_tolerance": 5, "required_hours": 24},
    ]
    for room in rooms:
        db.execute(insert(Room).values(**room).on_conflict_do_nothing())
        db.execute(insert(Station).values(id=room["id"] + "-door", room_id=room["id"], name=room["name"] + " door").on_conflict_do_nothing())
    for code in ["AR", "1L", "3L", "5L", "10L", "25L", "50L", "100L"]:
        db.execute(insert(Treatment).values(code=code).on_conflict_do_nothing())
