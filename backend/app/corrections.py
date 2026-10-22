from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from .models import AuditEvent, ConditioningSession, Room, Specimen, Stack, utcnow, uuid_str
from .services import iso, sessions_for, snapshot


def validate_timeline(db, specimen, now):
    sessions = sessions_for(db, specimen.id)
    previous = None
    for session in sessions:
        if session.entered_at > now or (session.exited_at and session.exited_at > now):
            raise HTTPException(422, "Recorded movement times cannot be in the future.")
        if session.exited_at and session.exited_at < session.entered_at:
            raise HTTPException(422, "Exit must be at or after entry.")
        if previous and (previous.exited_at is None or previous.exited_at > session.entered_at):
            raise HTTPException(409, "Conditioning sessions must not overlap.")
        previous = session


def process_correction(db, body, username):
    now = utcnow()
    target = None
    session = None
    if body.kind == "undo":
        if not body.event_id:
            raise HTTPException(422, "An event ID is required.")
        target = db.get(AuditEvent, str(body.event_id))
        if target is None or target.kind != "movement":
            raise HTTPException(404, "Select a recorded movement to undo.")
        ids = target.specimen_ids
        stack_id = target.stack_id
    elif body.kind == "edit_session":
        if not body.session_id or body.entered_at is None:
            raise HTTPException(422, "Session ID and entry time are required.")
        session = db.get(ConditioningSession, str(body.session_id))
        if session is None or session.voided:
            raise HTTPException(404, "Session not found.")
        ids = [session.specimen_id]
        stack_id = db.get(Specimen, session.specimen_id).stack_id
    else:
        ids = [str(i) for i in body.specimen_ids or []]
        if not ids or len(set(ids)) != len(ids) or body.entered_at is None or body.room_id is None:
            raise HTTPException(422, "Choose specimens, a room, and the claimed entry time.")
        first = db.get(Specimen, ids[0])
        if not first:
            raise HTTPException(404, "Specimen not found.")
        stack_id = first.stack_id
    stack = db.scalar(select(Stack).where(Stack.id == stack_id).with_for_update().execution_options(populate_existing=True))
    specimens = list(db.scalars(select(Specimen).where(Specimen.id.in_(ids)).order_by(Specimen.id).with_for_update().execution_options(populate_existing=True)))
    now = utcnow()
    if session is not None:
        db.refresh(session)
        if session.voided:
            raise HTTPException(404, "This session was undone while the correction was being prepared.")
    if len(specimens) != len(ids) or any(p.stack_id != stack_id for p in specimens):
        raise HTTPException(422, "All selected specimens must belong to the same stack.")
    before = snapshot(db, specimens)
    if body.kind == "undo":
        if any(p.last_event_id != target.id for p in specimens):
            raise HTTPException(409, "Cannot undo: an affected specimen has an intervening movement or correction.")
        for specimen in specimens:
            original = {s["id"]: s for s in target.payload["before"][specimen.id]}
            existing = sessions_for(db, specimen.id, include_voided=True)
            for s in existing:
                if s.id not in original:
                    s.voided = True
            db.flush()  # Release partial unique index before restoring a formerly open session.
            for s in existing:
                if s.id in original:
                    values = original[s.id]
                    s.entered_at = datetime.fromisoformat(values["entered_at"])
                    s.exited_at = datetime.fromisoformat(values["exited_at"]) if values["exited_at"] else None
                    s.corrected = values["corrected"]
                    s.incomplete = values["incomplete"]
                    s.early_exit_acknowledged = values.get("early_exit_acknowledged", False)
                    s.voided = values["voided"]
        kind = "undo"
    else:
        if body.exited_at is not None and body.exited_at < body.entered_at:
            raise HTTPException(422, "Exit must be at or after entry.")
        room_id = body.room_id or session.room_id
        room = db.get(Room, room_id)
        if room is None:
            raise HTTPException(422, "Unknown room.")
        if body.kind == "edit_session":
            session.entered_at = body.entered_at.astimezone(timezone.utc)
            session.exited_at = body.exited_at.astimezone(timezone.utc) if body.exited_at else None
            session.room_id = room_id
            session.room = room
            session.corrected = True
            session.incomplete = bool(session.exited_at and session.exited_at - session.entered_at < timedelta(hours=room.required_hours))
        else:
            for specimen in specimens:
                db.add(ConditioningSession(specimen_id=specimen.id, room_id=room_id,
                                           entered_at=body.entered_at.astimezone(timezone.utc),
                                           exited_at=body.exited_at.astimezone(timezone.utc) if body.exited_at else None,
                                           recorded_at=now, source="missed", corrected=True,
                                           incomplete=bool(body.exited_at and body.exited_at - body.entered_at < timedelta(hours=room.required_hours))))
        # Validate before flushing: sessions_for needs to include pending ORM changes.
        try:
            db.flush()
        except Exception as error:
            from sqlalchemy.exc import IntegrityError
            if isinstance(error, IntegrityError):
                raise HTTPException(409, "Correction conflicts with an existing open session or session chronology.") from error
            raise
        for specimen in specimens:
            validate_timeline(db, specimen, now)
        kind = "correction"
    db.flush()
    event = AuditEvent(id=uuid_str(), stack_id=stack_id, kind=kind, actor=username, reason=body.reason,
                       recorded_at=now, specimen_ids=ids,
                       payload={"correction_kind": body.kind, "event_id": target.id if target else None,
                                "before": before, "after": snapshot(db, specimens)})
    db.add(event)
    for specimen in specimens:
        specimen.revision += 1
        specimen.last_event_id = event.id
    stack.version += 1
    db.flush()
    return {"status": "committed", "event_id": event.id, "stack_id": stack_id,
            "affected_count": len(specimens), "recorded_at": iso(now), "version": stack.version}
