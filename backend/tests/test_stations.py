from datetime import timedelta

import pytest


@pytest.fixture(autouse=True)
def clear_presence():
    from app import stations
    stations.PRESENCE.clear()
    stations.DEVICES.clear()
    yield
    stations.PRESENCE.clear()
    stations.DEVICES.clear()


def by_id(payload):
    return {row["id"]: row for row in payload["stations"]}


def test_network_status_lists_all_stations_and_host_address(client):
    payload = client.get("/api/network/status").json()
    assert set(by_id(payload)) == {"shared-door", "ppe-door", "footwear-door"}
    assert all(row["status"] == "never_seen" and row["ip"] is None for row in payload["stations"])
    host = payload["host"]
    assert host["hostname"].endswith(".local")
    assert host["hostname_url"].startswith("http://")
    assert host["url"] is None or host["url"] == f'http://{host["ip"]}:{host["port"]}'


def test_heartbeat_records_station_ip_and_scanner_readiness(client):
    assert client.post("/api/stations/ppe-door/heartbeat", json={"scanner_ready": True, "input": "usb-bridge"}).status_code == 200
    assert client.post("/api/stations/shared-door/heartbeat", json={"scanner_ready": False}).status_code == 200
    rows = by_id(client.get("/api/network/status").json())
    assert rows["ppe-door"]["status"] == "ready"
    assert rows["ppe-door"]["ip"] == "testclient"
    assert rows["ppe-door"]["input"] == "usb-bridge"
    assert rows["shared-door"]["status"] == "no_scanner"
    assert rows["footwear-door"]["status"] == "never_seen"


def test_stale_heartbeat_reports_offline_with_last_known_ip(client):
    from app import stations
    client.post("/api/stations/footwear-door/heartbeat", json={"scanner_ready": True})
    record = stations.PRESENCE["footwear-door"]["testclient"]
    record["last_seen"] -= stations.ONLINE_FOR + timedelta(seconds=1)
    row = by_id(client.get("/api/network/status").json())["footwear-door"]
    assert row["status"] == "offline"
    assert row["ip"] == "testclient"
    assert row["live_clients"] == []


def test_unknown_station_heartbeat_is_rejected(client):
    assert client.post("/api/stations/not-a-door/heartbeat", json={"scanner_ready": True}).status_code == 404
    assert "not-a-door" not in by_id(client.get("/api/network/status").json())
