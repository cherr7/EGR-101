from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import BigInteger, select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.dialects.postgresql import insert

from .barcodes import code_id, label_code
from .models import (AuditEvent, ConditioningSession, Material, Project, RequestCounter,
                     Room, Specimen, Stack, Station, Treatment, utcnow)
from .prepsheet import resolve as resolve_prep

# The event an undo or modification refers to; matches the events_by_target index.
TARGET_EVENT = AuditEvent.payload["event_id"].astext.cast(BigInteger)


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
    return list(db.scalars(query.order_by(ConditioningSession.entered_at, ConditioningSession.id)
                           .execution_options(populate_existing=True)))


def current_session(db, specimen_id):
    return db.scalar(select(ConditioningSession).where(
        ConditioningSession.specimen_id == specimen_id,
        ConditioningSession.exited_at.is_(None), ConditioningSession.voided.is_(False))
        .execution_options(populate_existing=True))


def conditioning_period(db, specimen_id):
    return db.scalar(select(ConditioningSession).where(
        ConditioningSession.specimen_id == specimen_id, ConditioningSession.voided.is_(False))
        .execution_options(populate_existing=True))


def transaction_effective(db, event):
    amendment = db.scalar(select(AuditEvent).where(
        AuditEvent.kind == "modification", TARGET_EVENT == event.id
    ).order_by(AuditEvent.recorded_at.desc(), AuditEvent.id.desc()).limit(1))
    effective = dict(amendment.payload["effective_movement"]) if amendment else {
        "specimen_ids": event.specimen_ids, "occurred_at": event.payload.get("occurred_at", iso(event.recorded_at)),
        "room_id": event.payload.get("room_id"), "note": event.reason or ""}
    effective["latest_event_id"] = amendment.id if amendment else event.id
    effective["base_before"] = amendment.payload.get("base_before", event.payload.get("before", {})) if amendment else event.payload.get("before", {})
    return effective


def _live_periods(periods):
    def when(value):
        return datetime.fromisoformat(value) if value else None
    return sorted((p["id"], p["room_id"], when(p["entered_at"]), when(p["exited_at"]), p["corrected"],
                   p["incomplete"], p.get("early_exit_acknowledged", False)) for p in periods if not p["voided"])


def transaction_is_latest_for(db, specimen, effective):
    """True when undoing/editing this transaction cannot overwrite anything recorded after it.

    Normally that means the specimen's last event is this transaction. A later movement that was
    itself undone also qualifies: undo restores the exact sessions this transaction left, so the
    earlier transaction becomes the newest live change again."""
    latest_id = effective["latest_event_id"]
    if specimen.last_event_id == latest_id:
        return True
    last = db.get(AuditEvent, specimen.last_event_id) if specimen.last_event_id else None
    if last is None or last.kind != "undo":
        return False
    latest = db.get(AuditEvent, latest_id)
    after = (latest.payload.get("after") or {}).get(str(specimen.id)) if latest else None
    if after is None:
        return False
    current = [session_dict(s) for s in sessions_for(db, specimen.id, include_voided=True)]
    return _live_periods(current) == _live_periods(after)


def snapshot(db, specimens):
    # JSON object keys are strings, so audit snapshots are keyed by str(specimen id).
    return {str(p.id): [session_dict(s) for s in sessions_for(db, p.id, include_voided=True)] for p in specimens}


def event_dict(db, event):
    undone = db.scalar(select(AuditEvent.id).where(
        AuditEvent.kind == "undo", TARGET_EVENT == event.id)) is not None
    effective = transaction_effective(db, event) if event.kind == "movement" else None
    affected_ids = list(effective["specimen_ids"]) if effective else event.specimen_ids
    latest_id = effective["latest_event_id"] if effective else event.id
    can_undo = event.kind == "movement" and not undone and all(
        transaction_is_latest_for(db, db.get(Specimen, identifier), effective) for identifier in affected_ids)
    stack = db.get(Stack, event.stack_id)
    room_id = effective["room_id"] if effective else event.payload.get("room_id")
    if room_id is None:
        room_ids = {period.get("room_id") for periods in event.payload.get("after", {}).values()
                    for period in periods if period.get("room_id")}
        room_id = next(iter(room_ids)) if len(room_ids) == 1 else None
    room = db.get(Room, room_id) if room_id else None
    station_id = event.payload.get("station_id")
    station = db.get(Station, station_id) if station_id else None
    return {"id": event.id, "kind": event.kind, "direction": event.direction,
            "actor": event.actor, "reason": event.reason, "stack_id": event.stack_id,
            "specimen_ids": event.specimen_ids, "affected_count": len(event.specimen_ids),
            "recorded_at": iso(event.recorded_at), "payload": event.payload, "undone": undone,
            "occurred_at": effective["occurred_at"] if effective else event.payload.get("occurred_at", iso(event.recorded_at)),
            "room_id": room_id, "room_name": room.name if room else None,
            "station_id": station_id, "station_name": station.name if station else None,
            "project_number": stack.project.project_number, "material_ref": stack.material.material_ref,
            "test_code": stack.test_code, "preconditioning_code": stack.preconditioning_code,
            "specimen_numbers": [number for number in db.scalars(select(Specimen.number).where(
                Specimen.id.in_(affected_ids)).order_by(Specimen.number))],
            "note": effective["note"] if effective else event.reason or "",
            "effective_specimen_ids": affected_ids, "effective_count": len(affected_ids),
            "effective_occurred_at": effective["occurred_at"] if effective else None,
            "effective_room_id": effective["room_id"] if effective else None,
            "modified": bool(effective and latest_id != event.id), "can_undo": can_undo, "version": stack.version}


def specimen_time_log(db, specimen_id):
    events = db.scalars(select(AuditEvent).where(AuditEvent.specimen_ids.contains([specimen_id]))
                       .order_by(AuditEvent.recorded_at, AuditEvent.id))
    return [{"transaction_id": event.id, "type": event.kind, "direction": event.direction,
             "recorded_at": iso(event.recorded_at),
             "occurred_at": event.payload.get("occurred_at", iso(event.recorded_at)),
             "original_transaction_id": event.payload.get("event_id"), "note": event.reason or "",
             "before": event.payload.get("before", {}).get(str(specimen_id)),
             "after": event.payload.get("after", {}).get(str(specimen_id))} for event in events]


def conditioning_due(entered_at, required_hours):
    """When a piece entered at `entered_at` reaches its room's required residence (the one rule for 'ready')."""
    return entered_at + timedelta(hours=required_hours)


def stack_detail(db, stack, scanned_specimen_id=None, include_events=True):
    now = utcnow()
    specimens = []
    members = list(db.scalars(select(Specimen).where(Specimen.stack_id == stack.id).order_by(Specimen.number)))
    rows = [specimen for specimen in members if specimen.removed_at is None]
    removed = [{"id": specimen.id, "number": specimen.number, "barcode_value": label_code(specimen.id),
                "removed_at": iso(specimen.removed_at)} for specimen in members if specimen.removed_at is not None]
    by_specimen = {specimen.id: [] for specimen in rows}
    if by_specimen:
        for session in db.scalars(select(ConditioningSession).options(selectinload(ConditioningSession.room)).where(
                ConditioningSession.specimen_id.in_(by_specimen), ConditioningSession.voided.is_(False))
                .order_by(ConditioningSession.entered_at, ConditioningSession.id).execution_options(populate_existing=True)):
            by_specimen[session.specimen_id].append(session)
    for specimen in rows:
        sessions = by_specimen[specimen.id]
        active = next((s for s in sessions if s.exited_at is None), None)
        period = sessions[0] if sessions else None
        due = conditioning_due(active.entered_at, active.room.required_hours) if active else None
        specimens.append({"id": specimen.id, "number": specimen.number,
                          "state": "IN" if active else "OUT", "room_id": active.room_id if active else None,
                          "entered_at": iso(period.entered_at) if period else None, "due_at": iso(due),
                          "entry_at": iso(period.entered_at) if period else None,
                          "exit_at": iso(period.exited_at) if period else None,
                          "ENTRY": iso(period.entered_at) if period else None, "EXIT": iso(period.exited_at) if period else None,
                          "barcode_value": label_code(specimen.id),
                          "status": "READY" if due and due <= now else "CONDITIONING" if active else "FINISHED" if period else "WAITING",
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
              "specimens": specimens, "removed_specimens": removed, "scanned_specimen_id": scanned_specimen_id,
              "next_number": max((specimen.number for specimen in members), default=0) + 1,
              "metadata": stack.source_metadata, "server_time": iso(now),
              "inside_count": sum(p["state"] == "IN" for p in specimens),
              "ready_count": sum(p["ready"] for p in specimens),
              "waiting_count": sum(p["status"] == "WAITING" for p in specimens),
              "finished_count": sum(p["status"] == "FINISHED" for p in specimens),
              "oldest_entry_at": min((p["entry_at"] for p in specimens if p["state"] == "IN"), default=None),
              "next_ready_at": min((p["due_at"] for p in specimens if p["state"] == "IN" and not p["ready"]), default=None)}
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
    if room_id:  # only stacks with a piece inside this room now (indexed open sessions), not all history
        query = query.where(Stack.id.in_(select(Specimen.stack_id).join(ConditioningSession).where(
            ConditioningSession.room_id == room_id, ConditioningSession.exited_at.is_(None),
            ConditioningSession.voided.is_(False))))
    items = []
    for stack in db.scalars(query.options(selectinload(Stack.project), selectinload(Stack.material)).order_by(Stack.created_at.desc(), Stack.id)):
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
            if wanted in {"WAITING", "FINISHED"} and not any(p["status"] == wanted for p in detail["specimens"]):
                continue
        items.append(detail)
    return items


def resolve_code(db, code):
    """Return (stack, specimen) for a scanned or typed specimen label code."""
    value = code.strip()
    if value.startswith("]d") and len(value) > 3:
        value = value[3:]  # Optional Data Matrix AIM symbology identifier from configured scanners.
    try:
        identifier = code_id(value)
    except ValueError as error:
        raise HTTPException(400, "Invalid label code. " + str(error))
    specimen = db.get(Specimen, identifier)
    if specimen is None:
        raise HTTPException(404, "This label is not registered.")
    if specimen.removed_at is not None:
        raise HTTPException(409, f"Specimen {specimen.number:02d} was removed from stack {specimen.stack_id}. Set it aside.")
    return db.get(Stack, specimen.stack_id), specimen


def request_clients(db):
    return {"print", *db.scalars(select(Station.id))}


def reserve_request_numbers(db, client, count):
    """Hand out the next block of retry numbers for a client; a lost response only skips numbers."""
    if client not in request_clients(db):
        raise HTTPException(422, "Unknown request client. Use a station id or 'print'.")
    db.execute(insert(RequestCounter).values(client=client, last_issued=0).on_conflict_do_nothing())
    counter = db.scalar(select(RequestCounter).where(RequestCounter.client == client).with_for_update())
    first = counter.last_issued + 1
    counter.last_issued += count
    db.flush()
    return {"client": client, "first": first, "last": counter.last_issued}


def check_request_number(db, client, number):
    issued = db.scalar(select(RequestCounter.last_issued).where(RequestCounter.client == client))
    if issued is None or number > issued:
        raise HTTPException(422, f"Request {client}-{number} was never issued.")


def draft_dict(draft):
    stacks, problems = resolve_prep(draft.headers, draft.rows)
    return {"id": draft.id, "headers": draft.headers, "rows": draft.rows, "revision": draft.revision,
            "committed": draft.committed, "stack_ids": draft.stack_ids, "source": draft.source,
            "warnings": draft.warnings, "sheets": draft.sheets, "problems": problems,
            "stack_count": len(stacks), "specimen_count": sum(item["specimen_count"] for item in stacks)}


def find_or_create(db, model, column, value):
    db.execute(insert(model).values({column.key: value}).on_conflict_do_nothing())
    return db.scalar(select(model).where(column == value))


def register_stack(db, row, is_demo=False, actor="prep", payload=None):
    from .schemas import PrepRow
    row = PrepRow.model_validate(row).model_dump()
    project = find_or_create(db, Project, Project.project_number, row["project_number"])
    material = find_or_create(db, Material, Material.material_ref, row["material_ref"])
    db.execute(insert(Treatment).values(code=row["preconditioning_code"]).on_conflict_do_nothing())
    stack = Stack(project_id=project.id, material_id=material.id, test_code=row["test_code"],
                  preconditioning_code=row["preconditioning_code"], is_demo=is_demo,
                  source_metadata=row.get("metadata", {}))
    db.add(stack)
    db.flush()
    specimens = [Specimen(stack_id=stack.id, number=i) for i in range(1, row["specimen_count"] + 1)]
    db.add_all(specimens)
    db.flush()
    db.add(AuditEvent(stack_id=stack.id, kind="registration", actor=actor,
                      specimen_ids=[p.id for p in specimens], payload={**(payload or {}), "row": row}))
    return stack


def commit_draft(db, draft, is_demo=False):
    if draft.committed:
        return draft.stack_ids
    stacks, problems = resolve_prep(draft.headers, draft.rows)
    if problems or not stacks:
        raise HTTPException(422, {"message": "Fill in the highlighted prep sheet cells before registration.",
                                  "problems": problems})
    stack_ids = [register_stack(db, row, is_demo, payload={"draft_id": draft.id}).id for row in stacks]
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
