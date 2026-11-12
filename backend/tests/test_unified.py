from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from app.barcodes import label_code

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
import pytest

from test_tracker import detail, move


def test_check_digit_lookup_and_draft_source_metadata(client):
    body = {"headers": {"Garment": {"project_number": "0073-AB", "labware_number": "000012"}},
            "rows": [{"sheet": "Garment", "test": "ASTM D5034", "conditions": "AR → custom 5L laundering",
                      "metadata": {"excel_row": 17}}]}
    draft = client.post("/api/prep-drafts", json=body).json()
    assert {p["column"] for p in draft["problems"]} == {"conditioning", "sample"}
    incomplete = client.post(f'/api/prep-drafts/{draft["id"]}/commit')
    assert incomplete.status_code == 422
    body["rows"][0].update(sample="Sample A", conditioning="25")
    assert client.put(f'/api/prep-drafts/{draft["id"]}', json={**body, "expected_revision": 0}).is_success
    committed = client.post(f'/api/prep-drafts/{draft["id"]}/commit').json()
    stack = client.get(f'/api/stacks/{committed["stack_ids"][0]}').json()
    assert stack["specimen_count"] == 25 and stack["waiting_count"] == 25
    assert stack["metadata"]["labware_number"] == "000012" and stack["metadata"]["excel_row"] == 17
    specimen = stack["specimens"][0]
    assert specimen["barcode_value"] == label_code(specimen["id"])
    for code in [specimen["barcode_value"], "]d1" + specimen["barcode_value"]]:
        response = client.post("/api/lookup", json={"code": code})
        assert response.is_success and response.json()["scanned_specimen_id"] == specimen["id"]
    typo = str(specimen["id"]) + str((int(specimen["barcode_value"][-1]) + 1) % 10)
    for bad in [str(specimen["id"]), typo, str(2 ** 63) + "0", "UL1:P:" + str(specimen["id"])]:
        assert client.post("/api/lookup", json={"code": bad}).status_code == 400, bad
    assert client.post(f'/api/prep-drafts/{draft["id"]}/commit').json() == committed


def test_group_amendment_three_of_ten_preserves_original_and_per_specimen_log(client, stack_factory):
    stack = stack_factory(count=10)
    entered, _ = move(client, stack)
    removed, _ = move(client, stack, "OUT", expected_version=entered["version"], acknowledge_early=True)
    original = next(e for e in detail(client, stack)["events"] if e["id"] == removed["event_id"])
    ids = [p["id"] for p in stack["specimens"][:3]]
    response = client.patch(f'/api/transactions/{removed["event_id"]}', json={"specimen_ids": ids,
        "expected_version": removed["version"]})
    assert response.is_success, response.text
    after = detail(client, stack)
    assert (after["inside_count"], after["finished_count"]) == (7, 3)
    amended = next(e for e in after["events"] if e["id"] == removed["event_id"])
    assert amended["payload"] == original["payload"] and amended["affected_count"] == 10
    assert amended["effective_count"] == 3 and amended["modified"] and amended["can_undo"]
    specimen = client.get(f'/api/specimens/{stack["specimens"][3]["id"]}').json()
    assert specimen["ENTRY"] and specimen["EXIT"] is None
    assert any(event["type"] == "modification" and event["original_transaction_id"] == removed["event_id"] for event in specimen["raw_json_time"])
    undone = client.post(f'/api/transactions/{removed["event_id"]}/undo', json={"expected_version": after["version"]})
    assert undone.is_success and undone.json()["affected_count"] == 3
    after = detail(client, stack)
    assert after["inside_count"] == 10 and after["finished_count"] == 0
    assert all(len(p["sessions"]) == 1 for p in after["specimens"])


def test_transaction_edit_stale_future_and_chronology_are_atomic(client, stack_factory):
    stack = stack_factory(count=3)
    entered, _ = move(client, stack)
    response = client.patch(f'/api/transactions/{entered["event_id"]}', json={"expected_version": 0,
        "specimen_ids": [stack["specimens"][0]["id"]]})
    assert response.status_code == 409
    future = client.patch(f'/api/transactions/{entered["event_id"]}', json={
        "occurred_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
    assert future.status_code == 422
    assert detail(client, stack)["inside_count"] == 3
    changed = client.patch(f'/api/transactions/{entered["event_id"]}', json={
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(), "expected_version": entered["version"]})
    assert changed.is_success
    after = detail(client, stack)
    assert after["ready_count"] == 3
    removed, _ = move(client, stack, "OUT")
    assert removed["status"] == "committed"
    rejected = client.post(f'/api/transactions/{entered["event_id"]}/undo', json={})
    assert rejected.status_code == 409
    assert detail(client, stack)["finished_count"] == 3


def test_transaction_can_correct_its_selection_again_without_losing_original(client, stack_factory):
    stack = stack_factory(count=4)
    entered, _ = move(client, stack)
    removed, _ = move(client, stack, "OUT", expected_version=entered["version"], acknowledge_early=True)
    ids = [p["id"] for p in stack["specimens"]]
    first = client.patch(f'/api/transactions/{removed["event_id"]}', json={"specimen_ids": ids[:2]})
    assert first.is_success
    second = client.patch(f'/api/transactions/{removed["event_id"]}', json={"specimen_ids": ids[:3]})
    assert second.is_success, second.text
    after = detail(client, stack)
    assert (after["finished_count"], after["inside_count"]) == (3, 1)
    assert client.post(f'/api/transactions/{removed["event_id"]}/undo', json={}).is_success
    after = detail(client, stack)
    assert after["inside_count"] == 4
    assert all(len(p["sessions"]) == 1 for p in after["specimens"])


def test_concurrent_transaction_corrections_reject_stale_version_atomically(client, stack_factory):
    from fastapi import HTTPException
    from app.corrections import edit_transaction
    from app.db import SessionLocal
    from app.schemas import TransactionEditBody
    stack = stack_factory(count=4)
    entered, _ = move(client, stack)
    removed, _ = move(client, stack, "OUT", expected_version=entered["version"], acknowledge_early=True)
    ids = [p["id"] for p in stack["specimens"]]
    def correct(count):
        try:
            with SessionLocal.begin() as db:
                return edit_transaction(db, removed["event_id"], TransactionEditBody(
                    specimen_ids=ids[:count], expected_version=removed["version"]))["status"]
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(correct, [1, 2]))
    assert "committed" in results and 409 in results
    after = detail(client, stack)
    assert after["finished_count"] in {1, 2} and after["inside_count"] + after["finished_count"] == 4
    assert sum(event["kind"] == "modification" for event in after["events"]) == 1


def test_missed_transactions_distinguish_claimed_and_recorded_and_allow_no_reason(client, stack_factory):
    stack = stack_factory(count=2)
    ids = [p["id"] for p in stack["specimens"]]
    claimed = datetime.now(timezone.utc) - timedelta(hours=26)
    payload = {"stack_id": stack["id"], "specimen_ids": ids, "direction": "IN", "room_id": "shared", "occurred_at": claimed.isoformat()}
    recorded = client.post("/api/transactions/missed", json=payload)
    assert recorded.is_success, recorded.text
    after = detail(client, stack)
    assert after["ready_count"] == 2
    txn = next(e for e in after["events"] if e["id"] == recorded.json()["event_id"])
    assert txn["occurred_at"] != txn["recorded_at"] and txn["payload"]["source"] == "missed"
    assert client.post("/api/transactions/missed", json=payload).status_code == 409
    out = client.post("/api/transactions/missed", json={**payload, "direction": "OUT", "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
    assert out.is_success
    assert detail(client, stack)["finished_count"] == 2
    assert client.post("/api/transactions/missed", json=payload).status_code == 409


def test_one_period_constraint_covers_closed_sessions(db, stack_factory):
    from app.models import ConditioningSession
    stack = stack_factory(count=1)
    now = datetime.now(timezone.utc)
    db.add(ConditioningSession(specimen_id=stack["specimens"][0]["id"], room_id="shared", entered_at=now - timedelta(days=2), exited_at=now - timedelta(days=1)))
    db.commit()
    db.add(ConditioningSession(specimen_id=stack["specimens"][0]["id"], room_id="shared", entered_at=now))
    with pytest.raises(DBAPIError):
        db.flush()
    db.rollback()


def test_repeat_whole_out_moves_remaining_without_changing_previous_exit(client, stack_factory):
    stack = stack_factory(count=10)
    entered, _ = move(client, stack)
    first, _ = move(client, stack, "OUT", "selection", selected_specimen_ids=[p["id"] for p in stack["specimens"][:3]],
                    expected_version=entered["version"], acknowledge_early=True)
    original_exits = [p["exit_at"] for p in detail(client, stack)["specimens"][:3]]
    second, _ = move(client, stack, "OUT", expected_version=first["version"], acknowledge_early=True)
    assert second["affected_count"] == 7 and second["status"] == "committed"
    assert [p["exit_at"] for p in detail(client, stack)["specimens"][:3]] == original_exits
    assert move(client, stack, "IN")[0]["status"] == "already_recorded"
    assert client.post(f'/api/transactions/{second["event_id"]}/undo', json={}).is_success
    assert detail(client, stack)["inside_count"] == 7
