from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from .models import AuditEvent, ConditioningSession, Room, Specimen, Stack, utcnow, uuid_str
from .services import conditioning_period, current_session, iso, sessions_for, snapshot, transaction_effective


def validate_timeline(db, specimen, now):
    sessions = sessions_for(db, specimen.id)
    if len(sessions) > 1:
        raise HTTPException(409, "Each specimen has exactly one conditioning period. Correct the existing record instead.")
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
    if body.kind == "undo":
        from .schemas import TransactionUndoBody
        return undo_transaction(db, str(body.event_id), TransactionUndoBody(note=body.reason), username)
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
        ids = list(body.specimen_ids or [])
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
            original = {s["id"]: s for s in target.payload["before"][str(specimen.id)]}
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
        exit_at = (body.exited_at if "exited_at" in body.model_fields_set else session.exited_at) if body.kind == "edit_session" else body.exited_at
        if exit_at is not None and exit_at < body.entered_at:
            raise HTTPException(422, "Exit must be at or after entry.")
        room_id = body.room_id or session.room_id
        room = db.get(Room, room_id)
        if room is None:
            raise HTTPException(422, "Unknown room.")
        if body.kind == "edit_session":
            session.entered_at = body.entered_at.astimezone(timezone.utc)
            session.exited_at = exit_at.astimezone(timezone.utc) if exit_at else None
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
                                "room_id": room_id, "occurred_at": iso(body.entered_at),
                                "before": before, "after": snapshot(db, specimens)})
    db.add(event)
    for specimen in specimens:
        specimen.revision += 1
        specimen.last_event_id = event.id
    stack.version += 1
    db.flush()
    return {"status": "committed", "event_id": event.id, "stack_id": stack_id,
            "affected_count": len(specimens), "recorded_at": iso(now), "version": stack.version}


def _lock_transaction(db, event_id):
    target = db.get(AuditEvent, event_id)
    if target is None or target.kind != "movement":
        raise HTTPException(404, "Select a recorded movement transaction.")
    stack = db.scalar(select(Stack).where(Stack.id == target.stack_id).with_for_update().execution_options(populate_existing=True))
    specimens = list(db.scalars(select(Specimen).where(Specimen.stack_id == stack.id).order_by(Specimen.id)
                               .with_for_update().execution_options(populate_existing=True)))
    if db.scalar(select(AuditEvent.id).where(AuditEvent.kind == "undo", AuditEvent.payload["event_id"].astext == target.id)):
        raise HTTPException(409, "This transaction was already undone.")
    return target, stack, {p.id: p for p in specimens}


def _check_version(stack, expected_version):
    if expected_version is not None and stack.version != expected_version:
        raise HTTPException(409, "The stack changed. Refresh before saving this correction.")


def _restore(db, specimen, values):
    original = {value["id"]: value for value in values}
    sessions = sessions_for(db, specimen.id, include_voided=True)
    for session in sessions:
        if session.id not in original:
            session.voided = True
    db.flush()
    for session in sessions:
        if session.id in original:
            value = original[session.id]
            session.entered_at = datetime.fromisoformat(value["entered_at"])
            session.exited_at = datetime.fromisoformat(value["exited_at"]) if value["exited_at"] else None
            session.room_id = value["room_id"]
            session.room = db.get(Room, value["room_id"])
            session.corrected = value["corrected"]
            session.incomplete = value["incomplete"]
            session.early_exit_acknowledged = value.get("early_exit_acknowledged", False)
            session.voided = value["voided"]
    db.flush()


def _finish_transaction_change(db, stack, specimens, event):
    db.add(event)
    for specimen in specimens:
        specimen.revision += 1
        specimen.last_event_id = event.id
    stack.version += 1
    db.flush()
    return {"status": "committed", "event_id": event.id, "stack_id": stack.id,
            "affected_count": len(specimens), "recorded_at": iso(event.recorded_at), "version": stack.version}


def undo_transaction(db, event_id, body, username="project-staff"):
    target, stack, by_id = _lock_transaction(db, event_id)
    _check_version(stack, body.expected_version)
    effective = transaction_effective(db, target)
    specimens = [by_id[identifier] for identifier in effective["specimen_ids"]]
    if any(p.last_event_id != effective["latest_event_id"] for p in specimens):
        raise HTTPException(409, "Cannot undo: an affected specimen has an intervening movement or correction.")
    before = snapshot(db, specimens)
    for specimen in specimens:
        _restore(db, specimen, effective["base_before"][str(specimen.id)])
    now = utcnow()
    event = AuditEvent(id=uuid_str(), stack_id=stack.id, kind="undo", direction=target.direction,
                       actor=username, reason=body.note or None, recorded_at=now,
                       specimen_ids=[p.id for p in specimens],
                       payload={"event_id": target.id, "correction_kind": "undo", "before": before,
                                "room_id": effective["room_id"], "station_id": target.payload.get("station_id"),
                                "after": snapshot(db, specimens), "occurred_at": iso(now)})
    return _finish_transaction_change(db, stack, specimens, event)


def edit_transaction(db, event_id, body, username="project-staff"):
    target, stack, by_id = _lock_transaction(db, event_id)
    _check_version(stack, body.expected_version)
    effective = transaction_effective(db, target)
    ids = list(body.specimen_ids) if body.specimen_ids is not None else list(effective["specimen_ids"])
    if not ids or len(ids) != len(set(ids)) or any(identifier not in by_id for identifier in ids):
        raise HTTPException(422, "Choose unique numbered specimens from this stack. Use Undo to cancel a transaction.")
    current_ids = set(effective["specimen_ids"])
    affected_ids = current_ids | set(ids)
    for identifier in affected_ids:
        specimen = by_id[identifier]
        if identifier in current_ids:
            if specimen.last_event_id != effective["latest_event_id"]:
                raise HTTPException(409, "A specimen has an intervening movement or correction. Correct that later record first.")
        elif specimen.last_event_id:
            last = db.get(AuditEvent, specimen.last_event_id)
            if last and last.id != effective["latest_event_id"] and last.recorded_at > target.recorded_at:
                raise HTTPException(409, "A newly selected specimen has a later movement or correction.")
    occurred_at = body.occurred_at.astimezone(timezone.utc) if body.occurred_at else datetime.fromisoformat(effective["occurred_at"])
    now = utcnow()
    if occurred_at > now:
        raise HTTPException(422, "Recorded movement times cannot be in the future.")
    room_id = body.room_id or effective["room_id"]
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(422, "Unknown room.")
    specimens = [by_id[identifier] for identifier in sorted(affected_ids)]
    before = snapshot(db, specimens)
    base_before = dict(effective["base_before"])
    for identifier in set(ids) - current_ids:
        base_before[str(identifier)] = before[str(identifier)]
    for identifier in current_ids:
        _restore(db, by_id[identifier], base_before[str(identifier)])
    for identifier in ids:
        if target.direction == "IN":
            if conditioning_period(db, identifier) is not None:
                raise HTTPException(409, "A selected specimen already has its conditioning entry.")
            db.add(ConditioningSession(specimen_id=identifier, room_id=room_id, entered_at=occurred_at,
                                       recorded_at=now, source="correction", corrected=True))
        else:
            session = current_session(db, identifier)
            if session is None:
                raise HTTPException(409, "A selected specimen has no open entry to check out.")
            if occurred_at < session.entered_at:
                raise HTTPException(422, "Exit must be at or after entry.")
            if session.room_id != room_id:
                raise HTTPException(409, "Checkout room must match the specimen's entry room. Correct the entry first.")
            session.exited_at = occurred_at
            session.corrected = True
            session.incomplete = occurred_at - session.entered_at < timedelta(hours=room.required_hours)
    db.flush()
    for specimen in specimens:
        validate_timeline(db, specimen, now)
    updated = {"specimen_ids": ids, "occurred_at": iso(occurred_at), "room_id": room_id, "note": body.note}
    note = body.note if "note" in body.model_fields_set else effective["note"]
    updated["note"] = note
    event = AuditEvent(id=uuid_str(), stack_id=stack.id, kind="modification", direction=target.direction,
                       actor=username, reason=note or None, recorded_at=now,
                       specimen_ids=[p.id for p in specimens],
                       payload={"event_id": target.id, "effective_movement": updated, "base_before": base_before,
                                "room_id": room_id, "station_id": target.payload.get("station_id"),
                                "before": before, "after": snapshot(db, specimens), "occurred_at": iso(occurred_at)})
    return _finish_transaction_change(db, stack, specimens, event)


def record_missed_movement(db, body, username="project-staff"):
    stack = db.scalar(select(Stack).where(Stack.id == body.stack_id).with_for_update().execution_options(populate_existing=True))
    if stack is None:
        raise HTTPException(404, "Stack not found.")
    _check_version(stack, body.expected_version)
    ids = list(body.specimen_ids)
    specimens = list(db.scalars(select(Specimen).where(Specimen.id.in_(ids)).order_by(Specimen.id)
                               .with_for_update().execution_options(populate_existing=True)))
    if len(ids) != len(set(ids)) or len(specimens) != len(ids) or any(p.stack_id != stack.id for p in specimens):
        raise HTTPException(422, "Choose unique numbered specimens from this stack.")
    now = utcnow()
    occurred_at = body.occurred_at.astimezone(timezone.utc)
    room = db.get(Room, body.room_id)
    if room is None:
        raise HTTPException(422, "Unknown room.")
    if occurred_at > now:
        raise HTTPException(422, "Recorded movement times cannot be in the future.")
    before = snapshot(db, specimens)
    for specimen in specimens:
        period = conditioning_period(db, specimen.id)
        if body.direction == "IN":
            if period is not None:
                raise HTTPException(409, "This specimen already has its one conditioning entry.")
            db.add(ConditioningSession(specimen_id=specimen.id, room_id=room.id, entered_at=occurred_at,
                                       recorded_at=now, source="missed", corrected=True))
        else:
            if period is None or period.exited_at is not None:
                raise HTTPException(409, "A selected specimen has no open entry to check out.")
            if period.room_id != room.id:
                raise HTTPException(409, "Checkout room must match entry room.")
            if occurred_at < period.entered_at:
                raise HTTPException(422, "Exit must be at or after entry.")
            period.exited_at = occurred_at
            period.corrected = True
            period.incomplete = occurred_at - period.entered_at < timedelta(hours=room.required_hours)
    db.flush()
    event = AuditEvent(id=uuid_str(), stack_id=stack.id, kind="movement", direction=body.direction,
                       actor=username, reason=body.note or None, recorded_at=now, specimen_ids=ids,
                       payload={"source": "missed", "scope": "selection", "room_id": room.id,
                                "occurred_at": iso(occurred_at), "before": before, "after": snapshot(db, specimens)})
    return _finish_transaction_change(db, stack, specimens, event)
