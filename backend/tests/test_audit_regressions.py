import asyncio
from datetime import datetime, timedelta, timezone
import io
import json
import threading
from types import SimpleNamespace

from PIL import Image
from pypdf import PdfReader
import pytest
from sqlalchemy import event, select
import zxingcpp

from app import backups, printing
from app.barcodes import code_id
from test_printing_labels import install_fake_cups
from test_tracker import detail, move
from helpers import prep_sheet, request_id


def decode_pdf(content):
    document = PdfReader(io.BytesIO(content))
    return document, [code_id(zxingcpp.read_barcode(Image.open(io.BytesIO(list(page.images)[0].data))).text)
                      for page in document.pages]


def test_prep_compare_and_swap_prevents_registration_of_another_review(client):
    draft = client.post("/api/prep-drafts", json=prep_sheet("00073-DEMO", "Original", "D5034", "AR", 2)).json()
    first = prep_sheet("00073-DEMO", "First reviewed material", "D5034", "AR", 2)
    second = prep_sheet("00073-DEMO", "Second reviewed material", "D5034", "AR", 2)
    saved = client.put(f'/api/prep-drafts/{draft["id"]}', json={**first, "expected_revision": 0})
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert client.put(f'/api/prep-drafts/{draft["id"]}', json={**second, "expected_revision": 0}).status_code == 409
    assert client.post(f'/api/prep-drafts/{draft["id"]}/commit', json={"expected_revision": 0}).status_code == 409
    committed = client.post(f'/api/prep-drafts/{draft["id"]}/commit', json={"expected_revision": 1})
    assert committed.status_code == 200
    record = client.get("/api/stacks/" + str(committed.json()["stack_ids"][0])).json()
    assert record["material_ref"] == "First reviewed material"
    assert client.post(f'/api/prep-drafts/{draft["id"]}/commit', json={"expected_revision": 1}).json() == committed.json()


def test_selective_preview_print_and_audit_use_identical_registered_ids(client, stack_factory, monkeypatch):
    stack = stack_factory(count=10)
    ids = [stack["specimens"][i]["id"] for i in [2, 7]]
    body = {"stack_ids": [stack["id"]], "selected_specimen_ids": ids}
    preview = client.post("/api/labels/preview", json=body)
    assert preview.is_success
    document, decoded = decode_pdf(preview.content)
    assert decoded == ids
    assert f"Stack {stack['id']} · Piece 03" in document.pages[0].extract_text()
    calls = install_fake_cups(monkeypatch)
    original_run = printing.subprocess.run
    sent = []
    def check_job(argv, **kwargs):
        if argv[0].endswith("/lp"):
            with open(argv[-1], "rb") as stream:
                sent.extend(decode_pdf(stream.read())[1])
        return original_run(argv, **kwargs)
    monkeypatch.setattr(printing.subprocess, "run", check_job)
    request = {**body, "request_id": request_id(client, "print"), "printer_queue": "Brother_QL", "copies": 2}
    printed = client.post("/api/print-jobs", json=request)
    assert printed.is_success, printed.text
    assert printed.json()["options"]["label_count"] == 4
    assert printed.json()["options"]["selected_specimen_ids"] == ids
    assert client.post("/api/print-jobs", json=request).json()["status"] == "submitted"
    assert client.post("/api/print-jobs", json={**request, "selected_specimen_ids": ids[:1]}).status_code == 409
    assert sum(call[0].endswith("/lp") for call in calls) == 4 and sent == [ids[0], ids[0], ids[1], ids[1]]
    logs = client.get(f'/api/transactions?stack_id={stack["id"]}').json()["items"]
    printed_events = [row for row in logs if row["kind"] in {"label_print_requested", "label_print"}]
    assert len(printed_events) == 2
    assert all(row["specimen_ids"] == ids for row in printed_events)
    assert all(p["ENTRY"] is None for p in detail(client, stack)["specimens"])


def test_selective_labels_reject_empty_duplicate_and_foreign_ids(client, stack_factory, monkeypatch):
    stack = stack_factory(count=2)
    other = stack_factory(count=1, material="Other")
    calls = install_fake_cups(monkeypatch)
    for ids in [[], [stack["specimens"][0]["id"]] * 2, [other["specimens"][0]["id"]]]:
        body = {"stack_ids": [stack["id"]], "selected_specimen_ids": ids}
        assert client.post("/api/labels/preview", json=body).status_code == 422
        assert client.post("/api/print-jobs", json={**body, "request_id": request_id(client, "print"), "printer_queue": "Brother_QL"}).status_code == 422
    assert not any(call[0].endswith("/lp") for call in calls)


def test_lost_print_completion_preserves_attempt_and_cannot_resubmit(client, stack_factory, monkeypatch):
    stack = stack_factory(count=1)
    calls = install_fake_cups(monkeypatch)
    original_run = printing.subprocess.run
    def crash(argv, **kwargs):
        if argv[0].endswith("/lp"):
            calls.append(argv)
            raise RuntimeError("Simulated host loss after claiming print request")
        return original_run(argv, **kwargs)
    monkeypatch.setattr(printing.subprocess, "run", crash)
    body = {"stack_ids": [stack["id"]], "request_id": request_id(client, "print"), "printer_queue": "Brother_QL"}
    with pytest.raises(RuntimeError):
        client.post("/api/print-jobs", json=body)
    assert client.get(f'/api/print-jobs/{body["request_id"]}').json()["status"] == "submitting"
    now = datetime.now(timezone.utc) + timedelta(seconds=61)
    monkeypatch.setattr(printing, "utcnow", lambda: now)
    status = client.get(f'/api/print-jobs/{body["request_id"]}').json()
    assert status["status"] == "unknown" and status["needs_review"]
    assert client.post("/api/print-jobs", json=body).json()["status"] == "unknown"
    assert sum(call[0].endswith("/lp") for call in calls) == 1
    events = detail(client, stack)["events"]
    assert {row["kind"] for row in events} >= {"label_print_requested", "label_print_status"}


def test_event_filters_apply_before_limit_and_include_readable_context(client, stack_factory):
    shared = stack_factory(count=1, project="00073-LAB", material="Cotton")
    ppe = stack_factory(count=1, material="Nylon")
    first, _ = move(client, shared)
    move(client, ppe, station_id="ppe-door")
    response = client.get("/api/events?room_id=shared&limit=1")
    assert response.is_success, response.text
    rows = response.json()["items"]
    assert len(rows) == 1 and rows[0]["id"] == first["event_id"]
    assert rows[0]["project_number"] == "00073-LAB" and rows[0]["material_ref"] == "Cotton"
    assert rows[0]["room_name"] == "Shared conditioning room" and rows[0]["specimen_numbers"] == [1]
    assert client.get("/api/events?station_id=shared-door&limit=1").json()["items"][0]["id"] == first["event_id"]
    assert client.get("/api/events?q=Cotton&limit=1").json()["items"][0]["stack_id"] == shared["id"]
    assert client.get("/api/events?limit=0").status_code == 422
    assert client.get("/api/events?limit=501").status_code == 422


def test_old_transaction_search_precedes_limit_and_direct_lookup_is_unbounded(client, db, stack_factory):
    from app.models import AuditEvent
    stack = stack_factory(count=1, project="OLD-UNIQUE-PROJECT")
    moved, _ = move(client, stack)
    other = stack_factory(count=1, material="New unrelated stack", project="OTHER-PROJECT")
    for _ in range(110):
        db.add(AuditEvent(stack_id=other["id"], kind="label_print", actor="test",
                          specimen_ids=[other["specimens"][0]["id"]], payload={}))
    db.commit()
    assert moved["event_id"] not in {row["id"] for row in client.get("/api/transactions").json()["items"]}
    searched = client.get("/api/transactions?q=OLD-UNIQUE-PROJECT&kind=movement&limit=1").json()
    assert len(searched["items"]) == 1 and searched["items"][0]["id"] == moved["event_id"]
    assert client.get(f'/api/transactions?q={moved["event_id"]}&limit=1').json()["items"][0]["id"] == moved["event_id"]
    assert client.get(f'/api/transactions/{moved["event_id"]}').json()["id"] == moved["event_id"]


def test_locked_movement_refreshes_stale_session_after_room_correction(client, db, stack_factory):
    from app.models import ConditioningSession
    from app.movements import process_movement
    from app.schemas import MovementBody
    stack = stack_factory(count=1)
    move(client, stack)
    period = detail(client, stack)["specimens"][0]["sessions"][0]
    observed = db.get(ConditioningSession, period["id"])
    assert observed.room_id == "shared"
    corrected = client.post("/api/corrections", json={"kind": "edit_session", "session_id": period["id"],
        "room_id": "ppe", "entered_at": period["entered_at"]})
    assert corrected.is_success
    result = process_movement(db, MovementBody(request_id=request_id(client), station_id="shared-door",
        code=stack["specimens"][0]["barcode_value"], direction="OUT", acknowledge_early=True,
        expected_version=corrected.json()["version"]))
    assert result["status"] == "rejected" and "another room" in result["message"]
    db.rollback()
    assert detail(client, stack)["inside_count"] == 1


def test_partial_correction_preserves_unspecified_exit_and_note(client, stack_factory):
    stack = stack_factory(count=1)
    entered, _ = move(client, stack)
    removed, _ = move(client, stack, "OUT", acknowledge_early=True, expected_version=entered["version"])
    before = detail(client, stack)["specimens"][0]
    corrected = client.post("/api/corrections", json={"kind": "edit_session", "session_id": before["sessions"][0]["id"],
        "entered_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()})
    assert corrected.is_success
    assert detail(client, stack)["specimens"][0]["EXIT"] == before["EXIT"]
    second = stack_factory(count=1, material="Second")
    recorded, _ = move(client, second)
    assert client.patch(f'/api/transactions/{recorded["event_id"]}', json={"note": "Keep this note"}).is_success
    assert client.patch(f'/api/transactions/{recorded["event_id"]}', json={"occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}).is_success
    transaction = next(row for row in detail(client, second)["events"] if row["id"] == recorded["event_id"])
    assert transaction["note"] == "Keep this note"


def test_restore_gate_cancellation_while_waiting_clears_maintenance():
    async def scenario():
        gate = backups.TrafficGate()
        await gate.enter()
        async def waiting():
            async with gate.exclusive():
                pass
        task = asyncio.create_task(waiting())
        await asyncio.sleep(0)
        assert gate.maintenance
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not gate.maintenance
        await gate.leave()
        assert await gate.enter()
        await gate.leave()
    asyncio.run(scenario())


def test_disconnected_restore_keeps_gate_until_worker_finishes(monkeypatch):
    started, release = threading.Event(), threading.Event()
    def switch(_identifier):
        started.set()
        assert release.wait(5)
        return {"status": "restored"}
    monkeypatch.setattr(backups, "apply_staging", switch)
    async def scenario():
        gate = backups.TrafficGate()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(traffic_gate=gate)))
        task = asyncio.create_task(backups.restore_backup("test", backups.RestoreConfirmation(confirmation="RESTORE"), request))
        while not started.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        await asyncio.sleep(.01)
        assert gate.maintenance and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not gate.maintenance
    asyncio.run(scenario())


def test_malformed_manifest_shape_returns_validation_error(client):
    data = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("manifest.json", "[]")
        archive.writestr("snapshot.json", "{}")
    assert client.post("/api/backups/import", files={"file": ("invalid.zip", data.getvalue())}).status_code == 422


def test_restore_name_switch_rolls_back_if_promotion_fails(client, stack_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack = stack_factory(count=2)
    saved = client.post("/api/backups").json()
    archive = client.get(f'/api/backups/{saved["id"]}/download').content
    stage = client.post("/api/backups/import", files={"file": ("saved.zip", archive)}).json()
    real_admin = backups.admin_engine
    def broken_admin():
        admin = real_admin()
        def before(_conn, _cursor, statement, _parameters, _context, _many):
            if '_restore_' in statement.split(' RENAME TO ')[0] and statement.startswith('ALTER DATABASE'):
                raise RuntimeError("Injected failure after the first database rename")
        event.listen(admin, "before_cursor_execute", before)
        return admin
    monkeypatch.setattr(backups, "admin_engine", broken_admin)
    with pytest.raises(RuntimeError):
        client.post(f'/api/backups/imports/{stage["id"]}/restore', json={"confirmation": "RESTORE"})
    assert client.get("/api/health").json()["database"] == "connected"
    assert detail(client, stack)["specimen_count"] == 2
    assert client.get(f'/api/backups/imports/{stage["id"]}').json()["status"] == "validated"


def test_restore_recovers_when_commit_succeeds_but_its_response_is_lost(client, stack_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack = stack_factory(count=1)
    saved = client.post("/api/backups").json()
    content = client.get(f'/api/backups/{saved["id"]}/download').content
    staged = client.post("/api/backups/import", files={"file": ("saved.zip", content)}).json()
    stack_factory(count=1, material="Added after checkpoint")
    real_admin = backups.admin_engine

    def lost_commit_response_admin():
        admin = real_admin()
        real_commit = admin.dialect.do_commit

        def commit_then_lose_response(connection):
            real_commit(connection)  # Both database renames really commit.
            raise OSError("Injected connection loss after PostgreSQL COMMIT")

        admin.dialect.do_commit = commit_then_lose_response
        return admin

    monkeypatch.setattr(backups, "admin_engine", lost_commit_response_admin)
    with pytest.raises(OSError, match="after PostgreSQL COMMIT"):
        client.post(f'/api/backups/imports/{staged["id"]}/restore', json={"confirmation": "RESTORE"})
    journal = backups.folder() / (staged["id"] + ".staged.json")
    assert json.loads(journal.read_text())["status"] == "applying"
    # The restored data and committed marker, rather than the failed response,
    # establish the result. A retry must not replace subsequent movements.
    assert client.get("/api/stacks").json()["total"] == 1
    status = client.get(f'/api/backups/imports/{staged["id"]}').json()
    assert status["status"] == "restored"
    move(client, stack)
    retried = client.post(f'/api/backups/imports/{staged["id"]}/restore', json={"confirmation": "RESTORE"})
    assert retried.json() == status
    assert detail(client, stack)["inside_count"] == 1


def test_scheduler_metadata_and_corrupt_settings_recover(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    client.post("/api/backups")
    scheduler = client.get("/api/backups").json()["scheduler"]
    assert scheduler["last_success_at"] and scheduler["next_run_at"] and not scheduler["overdue"]
    (backups.folder() / "settings.json").write_text("[]")
    status = client.get("/api/backups").json()
    assert not status["scheduler"]["enabled"] and status["scheduler"]["last_error"]
    assert client.put("/api/backups/settings", json={"enabled": True}).is_success
    assert client.get("/api/backups").json()["scheduler"]["enabled"]


def test_restore_commit_survives_lost_journal_write_and_retry(client, stack_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack = stack_factory(count=1)
    saved = client.post("/api/backups").json()
    content = client.get(f'/api/backups/{saved["id"]}/download').content
    staged = client.post("/api/backups/import", files={"file": ("saved.zip", content)}).json()
    real_save = backups.save_json
    def fail_completion(path, value):
        if path.name.endswith(".staged.json") and value.get("status") == "restored":
            raise OSError("Injected failure after committed database switch")
        return real_save(path, value)
    monkeypatch.setattr(backups, "save_json", fail_completion)
    restored = client.post(f'/api/backups/imports/{staged["id"]}/restore', json={"confirmation": "RESTORE"})
    assert restored.is_success, restored.text
    status = client.get(f'/api/backups/imports/{staged["id"]}').json()
    assert status == restored.json()
    move(client, stack)
    retried = client.post(f'/api/backups/imports/{staged["id"]}/restore', json={"confirmation": "RESTORE"})
    assert retried.json() == restored.json()
    assert detail(client, stack)["inside_count"] == 1  # A retry did not replace the dataset again.


def test_restore_reconciles_retained_marker_after_a_later_restore(client, stack_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack = stack_factory(count=1)

    def stage_current():
        saved = client.post("/api/backups").json()
        content = client.get(f'/api/backups/{saved["id"]}/download').content
        return client.post("/api/backups/import", files={"file": ("saved.zip", content)}).json()

    first = stage_current()
    stack_factory(count=1, material="Present only in the second restore")
    second = stage_current()
    real_save = backups.save_json

    def lose_first_completion(path, value):
        if path.name == first["id"] + ".staged.json" and value.get("status") == "restored":
            raise OSError("First restore completion journal unavailable")
        return real_save(path, value)

    monkeypatch.setattr(backups, "save_json", lose_first_completion)
    first_result = client.post(f'/api/backups/imports/{first["id"]}/restore', json={"confirmation": "RESTORE"})
    assert first_result.is_success, first_result.text
    journal = backups.folder() / (first["id"] + ".staged.json")
    assert json.loads(journal.read_text())["status"] == "applying"
    assert client.get("/api/stacks").json()["total"] == 1
    # Another browser can complete B before A's lost result is reconciled.
    second_result = client.post(f'/api/backups/imports/{second["id"]}/restore', json={"confirmation": "RESTORE"})
    assert second_result.is_success, second_result.text
    assert client.get("/api/stacks").json()["total"] == 2
    assert json.loads(journal.read_text())["status"] == "applying"
    recovered = client.get(f'/api/backups/imports/{first["id"]}')
    assert recovered.is_success, recovered.text
    assert recovered.json() == first_result.json()
    move(client, stack)
    retried = client.post(f'/api/backups/imports/{first["id"]}/restore', json={"confirmation": "RESTORE"})
    assert retried.json() == first_result.json()
    assert client.get("/api/stacks").json()["total"] == 2
    assert detail(client, stack)["inside_count"] == 1


def test_scheduler_does_not_die_while_reporting_an_unwritable_error(monkeypatch):
    def bad_settings():
        raise OSError("State directory unavailable")
    async def stop_after_iteration(_seconds):
        raise asyncio.CancelledError()
    monkeypatch.setattr(backups, "settings", bad_settings)
    monkeypatch.setattr(backups.asyncio, "sleep", stop_after_iteration)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(backups.daily_scheduler())


def test_activity_row_version_rejects_undo_after_an_unseen_amendment(client, stack_factory):
    stack = stack_factory(count=3)
    entered, _ = move(client, stack)
    row = next(e for e in client.get('/api/events', params={'stack_id': stack['id']}).json()['items'] if e['id'] == entered['event_id'])
    assert row['version'] == entered['version']
    changed = client.patch(f"/api/transactions/{row['id']}", json={'note': 'Updated after row loaded', 'expected_version': row['version']})
    assert changed.is_success, changed.text
    stale = client.post(f"/api/transactions/{row['id']}/undo", json={'expected_version': row['version']})
    assert stale.status_code == 409
    assert detail(client, stack)['inside_count'] == 3
