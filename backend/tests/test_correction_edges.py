from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException


def test_even_small_future_claim_is_rejected(client, stack_factory, supervisor):
    stack = stack_factory(count=1)
    response = client.post("/api/corrections", json={
        "kind": "missed_session", "reason": "Accidental future time",
        "specimen_ids": [stack["specimens"][0]["id"]], "room_id": "shared",
        "entered_at": (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(),
    })
    assert response.status_code == 422
    assert client.get(f'/api/stacks/{stack["id"]}').json()["specimens"][0]["sessions"] == []


def test_session_undone_after_loading_cannot_be_corrected(client, db, stack_factory, supervisor):
    from app.corrections import process_correction
    from app.models import ConditioningSession
    from app.schemas import CorrectionBody

    stack = stack_factory(count=1)
    movement = client.post("/api/movements", json={"request_id": str(uuid4()), "station_id": "shared-door",
        "code": stack["specimens"][0]["barcode_value"], "direction": "IN", "scope": "stack"}).json()
    detail = client.get(f'/api/stacks/{stack["id"]}').json()
    session_id = detail["specimens"][0]["sessions"][0]["id"]
    # Keep an earlier ORM observation alive, as can happen while waiting for a lock.
    observed = db.get(ConditioningSession, session_id)
    assert observed.voided is False
    undo = client.post("/api/corrections", json={"kind": "undo", "event_id": movement["event_id"], "reason": "Wrong stack"})
    assert undo.is_success
    body = CorrectionBody(kind="edit_session", session_id=session_id, reason="Stale editor",
                          entered_at=datetime.now(timezone.utc) - timedelta(hours=25))
    with pytest.raises(HTTPException) as error:
        process_correction(db, body, "supervisor")
    assert error.value.status_code == 404
    db.rollback()
    assert len(client.get(f'/api/stacks/{stack["id"]}').json()["events"]) == 3
