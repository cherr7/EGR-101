import io
import json
import zipfile
from helpers import prep_sheet, request_id


def test_snapshot_download_and_checked_staged_restore(client, stack_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack = stack_factory(count=3)
    payload = {"request_id": request_id(client), "station_id": "shared-door",
               "code": stack["specimens"][0]["barcode_value"],
               "direction": "IN", "scope": "stack"}
    moved = client.post("/api/movements", json=payload)
    assert moved.status_code == 200, moved.text
    created = client.post("/api/backups")
    assert created.status_code == 200, created.text
    record = created.json()
    assert record["counts"]["specimens"] == 3
    downloaded = client.get(f'/api/backups/{record["id"]}/download')
    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert set(archive.namelist()) == {"manifest.json", "snapshot.json"}
    payload.update(request_id=request_id(client), direction="OUT", acknowledge_early=True)
    assert client.post("/api/movements", json=payload).status_code == 200
    upload = client.post("/api/backups/import", files={"file": ("checkpoint.zip", downloaded.content, "application/zip")})
    assert upload.status_code == 200, upload.text
    stage = upload.json()
    assert stage["status"] == "validated"
    assert client.post(f'/api/backups/imports/{stage["id"]}/restore', json={"confirmation": ""}).status_code == 422
    restored = client.post(f'/api/backups/imports/{stage["id"]}/restore', json={"confirmation": "RESTORE"})
    assert restored.status_code == 200, restored.text
    assert restored.json()["previous_database_retained"]
    current = client.get(f'/api/stacks/{stack["id"]}').json()
    assert [s["id"] for s in current["specimens"]] == [s["id"] for s in stack["specimens"]]
    assert all(s["ENTRY"] and not s["EXIT"] for s in current["specimens"])
    assert client.post(f'/api/backups/imports/{stage["id"]}/restore', json={"confirmation": "RESTORE"}).json() == restored.json()


def test_corrupt_backup_cannot_stage_or_download(client, stack_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack_factory(count=1)
    info = client.post("/api/backups").json()
    from app.backups import item_paths
    path, _ = item_paths(info["id"])
    path.write_bytes(b"damaged checkpoint")
    assert client.get(f'/api/backups/{info["id"]}/download').status_code == 409
    assert client.post("/api/backups/import", files={"file": ("broken.zip", b"bad", "application/zip")}).status_code == 422
    assert client.get("/api/stacks").json()["total"] == 1


def test_snapshot_manifest_and_unknown_entries_rejected(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "fabric-conditioning", "version": 2, "sha256": "incorrect"}))
        archive.writestr("snapshot.json", '{"tables":{}}')
    response = client.post("/api/backups/import", files={"file": ("fake.zip", data.getvalue())})
    assert response.status_code == 422
    assert "checksum" in response.json()["detail"]


def test_destination_export_settings_and_daily_state(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path / "state"))
    destination = tmp_path / "SharePoint folder"
    result = client.put("/api/backups/settings", json={"destination": str(destination), "retention_days": 30, "daily_hour": 2, "enabled": True})
    assert result.status_code == 200
    backup = client.post("/api/backups").json()
    assert backup["cloud_copy_state"] == "copied_to_destination"
    assert (destination / backup["filename"]).is_file()
    overview = client.get("/api/backups").json()
    assert overview["settings"]["last_day"]
    assert overview["items"][0]["id"] == backup["id"]


def test_restoring_an_older_backup_never_reissues_label_numbers(client, stack_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack_factory(count=2)
    saved = client.post("/api/backups").json()
    newer = stack_factory(count=3, material="Printed after the backup")
    archive = client.get(f'/api/backups/{saved["id"]}/download').content
    staged = client.post("/api/backups/import", files={"file": ("older.zip", archive)}).json()
    assert client.post(f'/api/backups/imports/{staged["id"]}/restore', json={"confirmation": "RESTORE"}).is_success
    after = stack_factory(count=2, material="Registered after the restore")
    issued = [p["id"] for p in newer["specimens"]]
    assert min(p["id"] for p in after["specimens"]) > max(issued)
    assert after["id"] > newer["id"]
