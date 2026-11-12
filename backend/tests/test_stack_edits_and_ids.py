"""Editing stacks after printing, and sequential retry IDs in place of UUIDs."""
from pathlib import Path
import re

from sqlalchemy import BigInteger, String, inspect

from helpers import request_id
from test_tracker import detail, move


def test_add_and_remove_specimens_keep_existing_labels_and_numbers(client, stack_factory):
    stack = stack_factory(count=3)
    printed = {p["number"]: p["barcode_value"] for p in stack["specimens"]}
    added = client.post(f'/api/stacks/{stack["id"]}/specimens', json={"count": 2, "expected_version": stack["version"]})
    assert added.status_code == 200, added.text
    after = added.json()
    assert [p["number"] for p in after["specimens"]] == [1, 2, 3, 4, 5]
    assert {p["number"]: p["barcode_value"] for p in after["specimens"][:3]} == printed   # old labels unchanged
    new_ids = after["added_specimen_ids"]
    assert [p["id"] for p in after["specimens"][3:]] == new_ids
    # Only the new specimens need labels: the label job accepts exactly them.
    preview = client.post("/api/labels/preview", json={"stack_ids": [stack["id"]], "selected_specimen_ids": new_ids})
    assert preview.status_code == 200

    two = after["specimens"][1]
    stale = client.post(f'/api/stacks/{stack["id"]}/specimens/remove', json={"specimen_ids": [two["id"]], "expected_version": stack["version"]})
    assert stale.status_code == 409
    removed = client.post(f'/api/stacks/{stack["id"]}/specimens/remove',
                          json={"specimen_ids": [two["id"]], "note": "Torn during prep", "expected_version": after["version"]}).json()
    assert [p["number"] for p in removed["specimens"]] == [1, 3, 4, 5]
    assert removed["removed_specimens"][0]["number"] == 2
    scanned = client.post("/api/lookup", json={"code": two["barcode_value"]})
    assert scanned.status_code == 409 and "removed" in scanned.json()["detail"]
    # The next addition never reuses a removed number.
    again = client.post(f'/api/stacks/{stack["id"]}/specimens', json={"count": 1}).json()
    assert again["specimens"][-1]["number"] == 6
    # A whole-stack IN moves the live specimens only.
    entered, _ = move(client, {"specimens": [again["specimens"][0]]})
    assert entered["affected_count"] == 5
    assert client.post(f'/api/stacks/{stack["id"]}/specimens/remove', json={"specimen_ids": [again["specimens"][0]["id"]]}).status_code == 409
    kinds = [e["kind"] for e in detail(client, stack)["events"]]
    assert {"specimens_added", "specimens_removed"} <= set(kinds)


def test_fix_project_material_test_treatment_after_printing(client, stack_factory):
    stack = stack_factory(count=2, project="WRONG-1", material="Wrong material", treatment="AR", test="Tear")
    labels = [p["barcode_value"] for p in stack["specimens"]]
    entered, _ = move(client, stack)
    edited = client.patch(f'/api/stacks/{stack["id"]}', json={
        "project_number": "00101-FAKE", "material_ref": "S-001", "test_code": "Flame", "preconditioning_code": "5L",
        "note": "Typed the wrong row", "expected_version": entered["version"]})
    assert edited.status_code == 200, edited.text
    value = edited.json()
    assert (value["project_number"], value["material_ref"], value["test_code"], value["preconditioning_code"]) == \
        ("00101-FAKE", "S-001", "Flame", "5L")
    assert [p["barcode_value"] for p in value["specimens"]] == labels
    assert client.post("/api/lookup", json={"code": labels[0]}).json()["material_ref"] == "S-001"
    edit_event = next(e for e in value["events"] if e["kind"] == "stack_edit")
    assert edit_event["payload"]["changes"]["material_ref"] == {"before": "Wrong material", "after": "S-001"}
    # A stack edit is not later movement activity: the IN can still be undone.
    row = next(e for e in client.get("/api/events", params={"stack_id": stack["id"], "kind": "movement"}).json()["items"])
    assert row["can_undo"] is True
    assert client.patch(f'/api/stacks/{stack["id"]}', json={"material_ref": "x", "expected_version": 0}).status_code == 409
    assert client.patch(f'/api/stacks/{stack["id"]}', json={"material_ref": "  "}).status_code == 422


def test_request_ids_are_station_counters_and_still_block_duplicates(client, stack_factory):
    stack = stack_factory(count=2)
    block = client.post("/api/request-ids", json={"client": "shared-door", "count": 5}).json()
    following = client.post("/api/request-ids", json={"client": "shared-door", "count": 5}).json()
    assert following["first"] == block["last"] + 1
    assert client.post("/api/request-ids", json={"client": "someone-else"}).status_code == 422
    body = {"request_id": f'shared-door-{block["first"]}', "station_id": "shared-door",
            "code": stack["specimens"][0]["barcode_value"], "direction": "IN"}
    first = client.post("/api/movements", json=body).json()
    assert first["status"] == "committed"
    assert client.post("/api/movements", json=body).json() == first             # a retry returns the same result
    assert client.post("/api/movements", json={**body, "direction": "OUT"}).status_code == 409   # reuse is refused
    assert client.get(f'/api/movement-requests/{body["request_id"]}').json() == first
    never = {**body, "request_id": f'shared-door-{following["last"] + 1}'}
    assert client.post("/api/movements", json=never).status_code == 422          # numbers must be issued first
    wrong_station = {**body, "request_id": request_id(client, "ppe-door")}
    assert client.post("/api/movements", json=wrong_station).status_code == 422
    for bad in ["7f9c1b0e-1d2a-4c7e-9a51-2b6f0d3e4a10", "shared-door", "shared-door-0", "shared-door--1"]:
        assert client.post("/api/movements", json={**body, "request_id": bad}).status_code == 422, bad


def test_schema_has_sequential_keys_and_no_uuids(client, db):
    from app.db import Base
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            assert "uuid" not in str(column.type).lower()
            if column.primary_key and isinstance(column.type, String):
                assert table.name in {"rooms", "stations", "treatments", "request_counters", "movement_requests"}, table.name
    for name in ["projects", "materials", "prep_drafts", "stacks", "specimens", "conditioning_sessions", "audit_events"]:
        key = Base.metadata.tables[name].c.id
        assert isinstance(key.type, BigInteger) and key.identity is not None, name
    assert isinstance(Base.metadata.tables["specimens"].c.last_event_id.type, BigInteger)
    assert "identity" not in client.get("/api/config").json()
    source = "\n".join(path.read_text() for path in (Path(__file__).parents[1] / "app").glob("*.py"))
    assert not re.search(r"uuid4|uuid_str|gen_random_uuid|import uuid", source)
    keys = {fk["referred_table"] for fk in inspect(db.get_bind()).get_foreign_keys("specimens")}
    assert keys == {"stacks", "audit_events"}


def test_restore_never_lets_request_counters_or_numbers_go_backwards(client, stack_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack_factory(count=1)
    saved = client.post("/api/backups").json()
    reserved = client.post("/api/request-ids", json={"client": "shared-door", "count": 40}).json()
    newer = stack_factory(count=1)
    archive = client.get(f'/api/backups/{saved["id"]}/download').content
    staged = client.post("/api/backups/import", files={"file": ("b.zip", archive)}).json()
    assert client.post(f'/api/backups/imports/{staged["id"]}/restore', json={"confirmation": "RESTORE"}).is_success
    after = client.post("/api/request-ids", json={"client": "shared-door", "count": 1}).json()
    assert after["first"] == reserved["last"] + 1
    again = stack_factory(count=1)
    assert again["id"] > newer["id"] and again["draft_id"] > newer["draft_id"]
