from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from time import perf_counter
from uuid import uuid4

from app.barcodes import label_code

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError


def move(client, stack, direction="IN", scope="stack", **extra):
    data = {"request_id": str(uuid4()), "station_id": "shared-door", "code": stack["specimens"][2 if len(stack["specimens"]) > 2 else 0]["barcode_value"],
            "direction": direction, "scope": scope, **extra}
    response = client.post("/api/movements", json=data)
    assert response.status_code == 200, response.text
    return response.json(), data


def detail(client, stack):
    return client.get("/api/stacks/" + str(stack["id"])).json()


def test_registration_identity_and_csv_preserve_project(client, stack_factory):
    first = stack_factory()
    second = stack_factory(material="POLY-B")
    third = stack_factory()
    assert len({first["id"], second["id"], third["id"]}) == 3
    assert first["project_number"] == "000000001234"
    repeated = client.post(f'/api/prep-drafts/{first["draft_id"]}/commit').json()
    assert repeated["stack_ids"] == [first["id"]]
    assert client.put(f'/api/prep-drafts/{first["draft_id"]}', json={"rows": []}).status_code == 422
    csv = b"project_number,material_ref,test_code,preconditioning_code,specimen_count\nUL-2026-001,Other,T1,3L,2\n"
    draft = client.post("/api/prep-drafts/csv", files={"file": ("prep.csv", csv, "text/csv")}).json()
    assert draft["rows"][0]["project_number"] == "UL-2026-001"
    committed = client.post(f'/api/prep-drafts/{draft["id"]}/commit').json()
    assert detail(client, {"id": committed["stack_ids"][0]})["specimen_count"] == 2
    assert set(client.get("/api/config").json()["treatments"]) >= {"AR", "100L"}
    rooms = {r["id"]: r for r in client.get("/api/config").json()["rooms"]}
    assert {key: (r["temperature_c"], r["temperature_tolerance_c"], r["relative_humidity"], r["humidity_tolerance"], r["required_hours"])
            for key, r in rooms.items()} == {"shared": (23, 1, 65, 2, 24), "ppe": (23, 3, 65, 5, 24), "footwear": (21, 3, 50, 5, 24)}


def test_scan_any_member_whole_stack_repeat_and_retry(client, stack_factory):
    stack = stack_factory()
    start = perf_counter()
    result, request = move(client, stack)
    elapsed = perf_counter() - start
    assert result["status"] == "committed" and result["affected_count"] == 20
    assert elapsed < 1.0, f"Local confirmed scan exceeded 1-second target: {elapsed:.3f}s"
    first = detail(client, stack)
    repeat, _ = move(client, stack)
    assert repeat["status"] == "already_recorded"
    assert [p["entered_at"] for p in detail(client, stack)["specimens"]] == [p["entered_at"] for p in first["specimens"]]
    assert client.post("/api/movements", json=request).json() == result
    assert client.get("/api/movement-requests/" + request["request_id"]).json() == result
    assert all(len(p["sessions"]) == 1 for p in detail(client, stack)["specimens"])
    assert client.post("/api/movements", json={**request, "direction": "OUT"}).status_code == 409
    assert client.get("/api/movement-requests/" + str(uuid4())).status_code == 404


def test_partial_early_exit_remaining_stack_and_no_reentry(client, stack_factory):
    stack = stack_factory()
    initial, _ = move(client, stack)
    selected = [p["id"] for p in stack["specimens"][:3]]
    warning, _ = move(client, stack, "OUT", "selection", selected_specimen_ids=selected, expected_version=initial["version"])
    assert warning["status"] == "acknowledgment_required"
    assert all(p["state"] == "IN" for p in detail(client, stack)["specimens"])
    removed, _ = move(client, stack, "OUT", "selection", selected_specimen_ids=selected,
                      expected_version=warning["version"], acknowledge_early=True)
    assert removed["status"] == "committed" and removed["affected_count"] == 3
    after = detail(client, stack)
    assert [p["number"] for p in after["specimens"] if p["state"] == "OUT"] == [1, 2, 3]
    assert all(p["sessions"][0]["incomplete"] and p["sessions"][0]["early_exit_acknowledged"] for p in after["specimens"][:3])
    mixed, _ = move(client, stack, "OUT")
    assert mixed["status"] == "acknowledgment_required" and len(mixed["specimens"]) == 17
    remaining, _ = move(client, stack, "OUT", expected_version=mixed["version"], acknowledge_early=True)
    assert remaining["status"] == "committed" and remaining["affected_count"] == 17
    after = detail(client, stack)
    reentry, _ = move(client, stack, "IN", "selection", selected_specimen_ids=selected, expected_version=after["version"])
    assert reentry["status"] == "already_recorded"
    assert all(len(p["sessions"]) == 1 for p in detail(client, stack)["specimens"])


def test_concurrent_and_stale_selection_are_atomic(client, stack_factory):
    from app.db import SessionLocal
    from app.movements import process_movement
    from app.schemas import MovementBody
    stack = stack_factory()
    request = {"request_id": str(uuid4()), "station_id": "shared-door", "code": stack["specimens"][0]["barcode_value"],
               "direction": "IN", "scope": "stack", "expected_version": 0}
    def invoke(payload):
        with SessionLocal.begin() as db:
            return process_movement(db, MovementBody(**payload))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, [request, {**request, "request_id": str(uuid4())}]))
    assert sorted(r["status"] for r in results) == ["committed", "rejected"]
    assert all(len(p["sessions"]) == 1 for p in detail(client, stack)["specimens"])
    stale, _ = move(client, stack, "OUT", "selection", selected_specimen_ids=[p["id"] for p in stack["specimens"]], expected_version=0, acknowledge_early=True)
    assert stale["status"] == "rejected"
    assert all(p["state"] == "IN" for p in detail(client, stack)["specimens"])


def test_concurrent_same_uuid_creates_exactly_one_movement(client, stack_factory):
    from app.db import SessionLocal
    from app.movements import process_movement
    from app.schemas import MovementBody
    stack = stack_factory(count=2)
    request = MovementBody(request_id=uuid4(), station_id="shared-door", code=stack["specimens"][0]["barcode_value"], direction="IN")
    def invoke(_):
        with SessionLocal.begin() as db:
            return process_movement(db, request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, [1, 2]))
    assert results[0] == results[1]
    assert len([e for e in detail(client, stack)["events"] if e["kind"] == "movement"]) == 1


def test_room_mismatch_and_unknown_labels_do_not_change_state(client, stack_factory):
    stack = stack_factory(count=2)
    entered, _ = move(client, stack)
    rejected, _ = move(client, stack, "OUT", station_id="ppe-door", expected_version=entered["version"], acknowledge_early=True)
    assert rejected["status"] == "rejected"
    assert all(p["state"] == "IN" for p in detail(client, stack)["specimens"])
    bad, _ = move(client, stack, code="not-a-label")
    assert bad["status"] == "rejected"
    assert client.post("/api/lookup", json={"code": label_code(10 ** 12)}).status_code == 404   # well-formed, unregistered


def test_corrections_auth_history_and_undo_guard(client, stack_factory, supervisor):
    stack = stack_factory(count=2)
    entered, _ = move(client, stack)
    session_id = detail(client, stack)["specimens"][0]["sessions"][0]["id"]
    changed = client.post("/api/corrections", json={"kind": "edit_session", "reason": "Correct clock transcription",
        "session_id": session_id, "entered_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(), "exited_at": None})
    assert changed.status_code == 200, changed.text
    after = detail(client, stack)
    assert after["specimens"][0]["ready"]
    original_event = next(e for e in after["events"] if e["id"] == entered["event_id"])
    assert original_event["payload"]["after"][str(stack["specimens"][0]["id"])][0]["entered_at"] != after["specimens"][0]["entered_at"]
    denied = client.post("/api/corrections", json={"kind": "undo", "event_id": entered["event_id"], "reason": "Try stale undo"})
    assert denied.status_code == 409
    # A later movement affecting only the second specimen can safely be undone.
    out, _ = move(client, stack, "OUT", "selection", selected_specimen_ids=[stack["specimens"][1]["id"]],
                  expected_version=after["version"], acknowledge_early=True)
    undone = client.post("/api/corrections", json={"kind": "undo", "event_id": out["event_id"], "reason": "Scanned wrong specimen"})
    assert undone.status_code == 200, undone.text
    assert detail(client, stack)["specimens"][1]["state"] == "IN"
    assert next(e for e in detail(client, stack)["events"] if e["id"] == out["event_id"])["undone"]
    client.post("/api/auth/logout")
    assert client.post("/api/corrections", json={"kind": "undo", "event_id": out["event_id"]}).status_code == 409


def test_missed_session_recorded_time_overlap_and_future_rejection(client, stack_factory, supervisor):
    stack = stack_factory(count=2)
    now = datetime.now(timezone.utc)
    claimed = now - timedelta(hours=30)
    data = {"kind": "missed_session", "reason": "Forgot entry yesterday", "specimen_ids": [stack["specimens"][0]["id"]],
            "room_id": "shared", "entered_at": claimed.isoformat(), "exited_at": (now - timedelta(hours=2)).isoformat()}
    recorded = client.post("/api/corrections", json=data)
    assert recorded.status_code == 200, recorded.text
    session = detail(client, stack)["specimens"][0]["sessions"][0]
    assert session["source"] == "missed" and session["entered_at"] != session["recorded_at"]
    overlap = client.post("/api/corrections", json={**data, "entered_at": (now - timedelta(hours=5)).isoformat(), "exited_at": None})
    assert overlap.status_code == 409
    assert len(detail(client, stack)["specimens"][0]["sessions"]) == 1
    future = client.post("/api/corrections", json={**data, "specimen_ids": [stack["specimens"][1]["id"]],
        "entered_at": (now + timedelta(hours=1)).isoformat(), "exited_at": None})
    assert future.status_code == 422
    assert detail(client, stack)["specimens"][1]["sessions"] == []


def test_undo_in_preserves_original_observation_and_no_open_session(client, stack_factory, supervisor):
    stack = stack_factory(count=1)
    entered, _ = move(client, stack)
    undone = client.post("/api/corrections", json={"kind": "undo", "event_id": entered["event_id"], "reason": "Wrong label"})
    assert undone.status_code == 200
    after = detail(client, stack)
    assert after["specimens"][0]["state"] == "OUT"
    assert len(after["events"]) == 3
    assert next(e for e in after["events"] if e["id"] == entered["event_id"])["payload"]["after"]
    assert move(client, stack)[0]["status"] == "committed"


def test_completed_continuous_session_exits_without_early_ack(client, stack_factory, supervisor):
    stack = stack_factory(count=1)
    move(client, stack)
    session_id = detail(client, stack)["specimens"][0]["sessions"][0]["id"]
    corrected = client.post("/api/corrections", json={"kind": "edit_session", "session_id": session_id,
        "reason": "Validate completed conditioning example", "entered_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        "exited_at": None})
    assert corrected.status_code == 200
    exit_result, _ = move(client, stack, "OUT")
    assert exit_result["status"] == "committed" and not exit_result["early_exit_acknowledged"]
    session = detail(client, stack)["specimens"][0]["sessions"][0]
    assert not session["incomplete"] and not session["early_exit_acknowledged"]


def test_database_enforces_open_session_and_append_only_audit(db, stack_factory):
    stack = stack_factory(count=1)
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE audit_events SET reason = 'erased'"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM audit_events"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("TRUNCATE audit_events"))
    db.rollback()
    assert db.scalar(text("SELECT count(*) FROM audit_events")) == 1
    from app.models import ConditioningSession
    now = datetime.now(timezone.utc)
    db.add(ConditioningSession(specimen_id=stack["specimens"][0]["id"], room_id="shared", entered_at=now))
    db.commit()
    db.add(ConditioningSession(specimen_id=stack["specimens"][0]["id"], room_id="ppe", entered_at=now))
    with pytest.raises(DBAPIError):
        db.flush()
    db.rollback()


def test_health_checks_database(client, monkeypatch):
    from app.db import get_db
    from app.main import app
    assert client.get("/api/health").json()["database"] == "connected"
    class Broken:
        def execute(self, _):
            raise RuntimeError("database unavailable")
    def broken_db():
        yield Broken()
    app.dependency_overrides[get_db] = broken_db
    try:
        assert client.get("/api/health").status_code == 503
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_undoing_a_later_scan_reopens_undo_on_the_earlier_scan(client, stack_factory):
    # Kiosk sequence from the Pi: IN, OUT, undo OUT, then undo the IN.
    stack = stack_factory(count=3)
    entered, _ = move(client, stack)
    left, _ = move(client, stack, "OUT", acknowledge_early=True, expected_version=entered["version"])
    assert left["status"] == "committed", left
    rows = {e["id"]: e for e in client.get("/api/events", params={"stack_id": stack["id"]}).json()["items"]}
    assert rows[entered["event_id"]]["can_undo"] is False      # the OUT really is later activity
    undone = client.post("/api/transactions/" + left["event_id"] + "/undo", json={"expected_version": rows[left["event_id"]]["version"]})
    assert undone.is_success, undone.text
    row = next(e for e in client.get("/api/events", params={"stack_id": stack["id"]}).json()["items"] if e["id"] == entered["event_id"])
    assert row["can_undo"] is True and not row["undone"]
    again = client.post("/api/transactions/" + entered["event_id"] + "/undo", json={"expected_version": row["version"]})
    assert again.is_success, again.text
    assert detail(client, stack)["inside_count"] == 0
    assert all(s["state"] == "OUT" and not [p for p in s["sessions"] if not p.get("voided")] for s in detail(client, stack)["specimens"])


def test_correction_after_an_undone_scan_still_blocks_undo(client, stack_factory, supervisor):
    stack = stack_factory(count=1)
    entered, _ = move(client, stack)
    left, _ = move(client, stack, "OUT", acknowledge_early=True, expected_version=entered["version"])
    version = lambda: detail(client, stack)["version"]
    assert client.post("/api/transactions/" + left["event_id"] + "/undo", json={"expected_version": version()}).is_success
    session_id = detail(client, stack)["specimens"][0]["sessions"][0]["id"]
    corrected = client.post("/api/corrections", json={"kind": "edit_session", "session_id": session_id, "reason": "Fix entry",
        "entered_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), "exited_at": None})
    assert corrected.status_code == 200, corrected.text
    row = next(e for e in client.get("/api/events", params={"stack_id": stack["id"]}).json()["items"] if e["id"] == entered["event_id"])
    assert row["can_undo"] is False
    assert client.post("/api/transactions/" + entered["event_id"] + "/undo", json={"expected_version": version()}).status_code == 409
