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


def test_backup_list_reports_total_storage_and_folder(client, stack_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack_factory(count=1)
    first = client.post("/api/backups").json()
    second = client.post("/api/backups").json()
    overview = client.get("/api/backups").json()
    storage = overview["storage"]
    from app.backups import folder
    assert storage["folder"] == str(folder())
    assert storage["archive_count"] == 2
    assert storage["total_bytes"] == first["size"] + second["size"]


def test_open_backup_folder_is_limited_to_the_host_mac(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    import app.backups as backups
    calls = []
    monkeypatch.setattr(backups.sys, "platform", "darwin")
    monkeypatch.setattr(backups.subprocess, "run", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(backups, "is_local_client", lambda request: False)
    assert client.post("/api/backups/open-folder").status_code == 403
    assert client.get("/api/backups").json()["storage"]["can_open"] is False
    assert calls == []
    monkeypatch.setattr(backups, "is_local_client", lambda request: True)
    opened = client.post("/api/backups/open-folder")
    assert opened.status_code == 200, opened.text
    assert calls == [["/usr/bin/open", str(backups.folder())]]
    assert client.get("/api/backups").json()["storage"]["can_open"] is True


def test_archives_use_dedicated_folder_and_migrate_legacy_ones(client, stack_factory, monkeypatch, tmp_path):
    import app.backups as backups
    state, visible = tmp_path / "state", tmp_path / "Fabric Conditioning Backups"
    monkeypatch.setenv("FABRIC_STATE_DIR", str(state))
    stack_factory(count=1)
    legacy = client.post("/api/backups").json()  # written before the dedicated folder is configured
    assert (backups.folder() / (legacy["id"] + ".zip")).is_file()
    monkeypatch.setenv("FABRIC_BACKUP_DIR", str(visible))
    backups._MIGRATED.clear()
    overview = client.get("/api/backups").json()
    assert [item["id"] for item in overview["items"]] == [legacy["id"]]
    assert overview["storage"]["folder"] == str(visible)
    assert (visible / (legacy["id"] + ".zip")).is_file() and (visible / (legacy["id"] + ".info.json")).is_file()
    assert not list(backups.folder().glob("*.zip"))
    assert (backups.folder() / "settings.json").is_file()  # settings stay with the app state
    fresh = client.post("/api/backups").json()
    assert (visible / (fresh["id"] + ".zip")).is_file()
    assert client.get(f'/api/backups/{legacy["id"]}/download').status_code == 200
    assert client.put("/api/backups/settings", json={"destination": str(visible), "retention_days": 30,
                                                      "daily_hour": 2, "enabled": True}).status_code == 422


def test_host_mac_is_recognised_by_its_own_lan_address(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    import app.backups as backups
    import app.stations as stations
    monkeypatch.setattr(backups.sys, "platform", "darwin")
    monkeypatch.setattr(stations, "local_addresses", lambda: frozenset({"127.0.0.1", "::1", "10.0.0.5"}))
    assert client.get("/api/backups").json()["storage"]["can_open"] is False  # "testclient" is another machine
    monkeypatch.setattr(stations, "local_addresses", lambda: frozenset({"127.0.0.1", "testclient"}))
    assert client.get("/api/backups").json()["storage"]["can_open"] is True


def test_local_addresses_include_loopback_and_an_interface():
    from app.stations import local_addresses
    found = local_addresses()
    assert {"127.0.0.1", "::1"} <= found


def _backdate(record_id, when):
    from app.backups import item_paths
    _, info_path = item_paths(record_id)
    info = json.loads(info_path.read_text())
    info["created_at"] = when.isoformat()
    info_path.write_text(json.dumps(info))


def test_monday_backup_is_kept_forever_and_others_expire(client, stack_factory, monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from app.backups import item_paths
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    stack_factory(count=1)
    eastern = ZoneInfo("America/New_York")
    old = datetime.now(eastern) - timedelta(days=60)
    monday = (old - timedelta(days=old.weekday())).replace(hour=2, minute=0, second=5, microsecond=0)
    ids = {name: client.post("/api/backups").json()["id"] for name in ("monday_2am", "monday_later", "sunday", "tuesday")}
    _backdate(ids["monday_2am"], monday.astimezone(timezone.utc))
    _backdate(ids["monday_later"], (monday + timedelta(hours=9)).astimezone(timezone.utc))  # second Monday backup
    _backdate(ids["sunday"], (monday - timedelta(hours=3)).astimezone(timezone.utc))        # Sunday 11 p.m.
    _backdate(ids["tuesday"], (monday + timedelta(days=1)).astimezone(timezone.utc))
    listed = {item["id"]: item for item in client.get("/api/backups").json()["items"]}
    assert listed[ids["monday_2am"]]["keep_forever"] is True and listed[ids["monday_2am"]]["expires_at"] is None
    assert all(not listed[ids[name]]["keep_forever"] and listed[ids[name]]["expires_at"]
               for name in ("monday_later", "sunday", "tuesday"))
    fresh = client.post("/api/backups").json()  # creating a backup runs retention cleanup
    remaining = {item["id"] for item in client.get("/api/backups").json()["items"]}
    assert remaining == {ids["monday_2am"], fresh["id"]}
    assert item_paths(ids["monday_2am"])[0].is_file()
    assert not item_paths(ids["tuesday"])[0].exists()


def test_weekly_keepers_use_eastern_monday_not_utc():
    from app.backups import weekly_keepers
    # 2026-11-16 is a Monday. 04:30 UTC Monday is still Sunday 11:30 p.m. in New York.
    records = [{"id": "sunday-night-et", "created_at": "2026-11-16T04:30:00+00:00"},
               {"id": "monday-2am-et", "created_at": "2026-11-16T07:00:00+00:00"},
               {"id": "monday-noon-et", "created_at": "2026-11-16T17:00:00+00:00"}]
    assert weekly_keepers(records) == {"monday-2am-et"}


def test_outside_link_requests_are_not_on_the_host_mac():
    from starlette.requests import Request
    from app.stations import is_local_client
    local = {"type": "http", "client": ("127.0.0.1", 5000), "headers": []}
    tunnel = {**local, "headers": [(b"cf-ray", b"8abc-IAD"), (b"cf-connecting-ip", b"203.0.113.9")]}
    assert is_local_client(Request(local))
    assert not is_local_client(Request(tunnel))
