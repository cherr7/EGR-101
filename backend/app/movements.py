import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, text

from .models import AuditEvent, ConditioningSession, MovementRequest, Specimen, Stack, Station, utcnow
from .schemas import split_request_id
from .services import check_request_number, iso, resolve_code, snapshot


def process_movement(db, body):
    request_id = body.request_id
    client, number = split_request_id(request_id)
    if client != body.station_id:
        raise HTTPException(422, "A scan's request ID must start with its station id.")
    payload = body.model_dump(mode="json")
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    lock_key = int.from_bytes(hashlib.sha256(request_id.encode()).digest()[:8], "big", signed=True)
    # Serializing each request ID also handles two simultaneous first submissions.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    existing = db.get(MovementRequest, (client, number))
    if existing:
        if existing.fingerprint != fingerprint:
            raise HTTPException(409, "Request ID already used for different movement details.")
        return existing.result
    station = db.get(Station, body.station_id)
    if not station:
        raise HTTPException(422, "Unknown scanning station.")
    check_request_number(db, client, number)
    now = utcnow()

    def finish(status, message, stack=None, affected=None, **extra):
        result = {"status": status, "message": message, "stack_id": stack.id if stack else None,
                  "affected_count": len(affected or []), "recorded_at": iso(now), **extra}
        if stack:
            result["version"] = stack.version
        db.add(MovementRequest(station_id=client, number=number, fingerprint=fingerprint, result=result, recorded_at=now))
        db.flush()
        return result

    try:
        stack, scanned = resolve_code(db, body.code)
    except HTTPException as error:
        return finish("rejected", str(error.detail))
    stack = db.scalar(select(Stack).where(Stack.id == stack.id).with_for_update().execution_options(populate_existing=True))
    specimens = list(db.scalars(select(Specimen).where(Specimen.stack_id == stack.id, Specimen.removed_at.is_(None)).order_by(Specimen.id).with_for_update().execution_options(populate_existing=True)))
    now = utcnow()
    by_id = {p.id: p for p in specimens}
    sessions = list(db.scalars(select(ConditioningSession).where(
        ConditioningSession.specimen_id.in_(by_id), ConditioningSession.voided.is_(False)
    ).execution_options(populate_existing=True))) if by_id else []
    session_map = {s.specimen_id: s for s in sessions}
    active = {p.id: session_map[p.id] if (p.id in session_map and session_map[p.id].exited_at is None) else None for p in specimens}
    periods = {p.id: session_map.get(p.id) for p in specimens}
    if body.expected_version is not None and body.expected_version != stack.version:
        return finish("rejected", "The stack changed. Scan again and review the current selection.", stack)
    if body.scope == "selection":
        ids = list(body.selected_specimen_ids or [])
        if not ids or len(ids) != len(set(ids)) or any(p not in by_id for p in ids):
            return finish("rejected", "Select valid, unique specimens from this stack.", stack)
        if body.expected_version is None:
            return finish("rejected", "Review the stack before confirming a selection.", stack)
        selected = [by_id[p] for p in ids]
    elif body.scope == "specimen":
        if scanned is None:
            return finish("rejected", "Scan a specimen label for individual mode.", stack)
        if scanned.id not in by_id:  # Removed between reading the label and locking the stack.
            return finish("rejected", "This specimen was just removed from the stack. Set it aside.", stack)
        selected = [by_id[scanned.id]]
    else:
        selected = specimens
    changed = [p for p in selected if (periods[p.id] is None if body.direction == "IN" else active[p.id] is not None)]
    if not changed:
        return finish("already_recorded", f"Already recorded {body.direction}; original timestamps preserved. Each specimen conditions once.", stack)
    if body.direction == "OUT" and any(active[p.id].room_id != station.room_id for p in changed):
        return finish("rejected", "A selected specimen is in another room. Check it out at that room first.", stack)
    if body.direction == "OUT" and any(now < active[p.id].entered_at for p in changed):
        return finish("rejected", "The server clock is before the recorded entry. Check the timestamps before checkout.", stack)
    if body.scope != "stack" and len(changed) != len(selected):
        return finish("rejected", "Some selected specimens are already in the requested state. Select only specimens to move.", stack)
    early = [p for p in changed if body.direction == "OUT" and
             now < active[p.id].entered_at + timedelta(hours=station.room.required_hours)]
    if early and not body.acknowledge_early:
        return finish("acknowledgment_required", f"These specimens have not completed {station.room.required_hours:g} hours. Acknowledge their early removal.", stack,
                      specimens=[{"id": p.id, "number": p.number, "entered_at": iso(active[p.id].entered_at),
                                  "due_at": iso(active[p.id].entered_at + timedelta(hours=station.room.required_hours))} for p in sorted(early, key=lambda p: p.number)])
    if body.acknowledge_early and body.expected_version is None:
        return finish("rejected", "Review the current stack before acknowledging early removal.", stack)
    before = snapshot(db, changed)
    for specimen in changed:
        if body.direction == "IN":
            db.add(ConditioningSession(specimen_id=specimen.id, room_id=station.room_id,
                                       entered_at=now, recorded_at=now, source="scan"))
        else:
            session = active[specimen.id]
            session.exited_at = now
            session.incomplete = specimen in early
            session.early_exit_acknowledged = specimen in early
    db.flush()
    event = AuditEvent(stack_id=stack.id, kind="movement", direction=body.direction,
                       actor=station.id, recorded_at=now, specimen_ids=[p.id for p in changed], room_id=station.room_id,
                       payload={"request_id": request_id, "station_id": station.id, "room_id": station.room_id,
                                "occurred_at": iso(now),
                                "scope": body.scope, "early_exit_acknowledged": bool(early),
                                "before": before, "after": snapshot(db, changed)})
    db.add(event)
    db.flush()
    for specimen in changed:
        specimen.revision += 1
        specimen.last_event_id = event.id
    stack.version += 1
    db.flush()
    return finish("committed", f"Recorded {body.direction} for {len(changed)} specimen(s).", stack, changed, event_id=event.id,
                  early_exit_acknowledged=bool(early))
