import pytest


@pytest.fixture(autouse=True)
def fresh_report():
    from app import stations, system_health
    system_health._STATE["report_at"] = 0.0
    stations.PRESENCE.clear()
    yield
    system_health._STATE["report_at"] = 0.0
    stations.PRESENCE.clear()


def levels(issues, area):
    return {issue["title"]: issue["level"] for issue in issues if issue["area"] == area}


def test_health_report_covers_host_devices_and_backups(client):
    response = client.get("/api/system/health")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["overall"] in {"ok", "warning", "critical"}
    assert report["database"]["connected"] is True
    assert report["database"]["size_bytes"] > 0
    disk = report["storage"][0]
    assert disk["total_bytes"] > disk["free_bytes"] > 0
    assert report["memory"] is None or 0 < report["memory"]["percent"] <= 100
    assert report["cpu"]["percent"] is None or 0 <= report["cpu"]["percent"] <= 100
    assert {row["id"] for row in report["stations"]} == {"shared-door", "ppe-door", "footwear-door"}
    assert isinstance(report["printer"]["queues"], list)
    assert report["backups"]["available"] is True
    # Stations that never sent a heartbeat are informational, not warnings.
    assert set(levels(report["issues"], "Scan stations").values()) == {"info"}


def test_station_problems_become_warnings(client):
    from datetime import timedelta
    from app import stations
    client.post("/api/stations/ppe-door/heartbeat", json={"scanner_ready": False})
    client.post("/api/stations/shared-door/heartbeat", json={"scanner_ready": True})
    stations.PRESENCE["shared-door"]["testclient"]["last_seen"] -= stations.ONLINE_FOR + timedelta(seconds=1)
    found = levels(client.get("/api/system/health").json()["issues"], "Scan stations")
    assert any("no scanner" in title and level == "warning" for title, level in found.items())
    assert any("offline" in title and level == "warning" for title, level in found.items())


def report_with(**changes):
    base = {"storage": [{"label": "Mac storage", "free_bytes": 100 * 2**30, "percent": 40.0}],
            "memory": {"pressure": "normal"}, "power": None, "database": {"connected": True},
            "application": {"logs_bytes": 0}, "stations": [],
            "printer": {"queues": [{"name": "QL", "state": "idle", "alerts": None, "jobs_waiting": 0}], "jobs_last_24h": {}},
            "backups": {"available": False}}
    base.update(changes)
    return base


def test_thresholds_flag_full_disk_battery_sleep_and_printer():
    from app.system_health import find_issues
    assert find_issues(report_with()) == []
    issues = find_issues(report_with(
        storage=[{"label": "Mac storage", "free_bytes": 2 * 2**30, "percent": 99.0}],
        memory={"pressure": "warning"},
        power={"on_battery": True, "battery_percent": 15, "sleep_minutes": 10, "thermal_warning": None},
        printer={"queues": [{"name": "QL", "state": "disabled", "alerts": "offline-report", "jobs_waiting": 4}],
                 "jobs_last_24h": {"failed": 1, "submitted": 3}},
        database={"connected": False}))
    titles = {issue["title"]: issue["level"] for issue in issues}
    assert titles["Mac storage is almost full"] == "critical"
    assert titles["The host Mac is running on battery"] == "critical"
    assert titles["The database is not reachable"] == "critical"
    assert titles["Memory pressure is warning"] == "warning"
    assert titles["The Mac is set to sleep after 10 minutes"] == "warning"
    assert titles["QL is paused"] == "warning"
    assert any(title.startswith("1 label job") for title in titles)
    assert [issue["level"] for issue in issues] == sorted((issue["level"] for issue in issues),
                                                          key=["critical", "warning", "info"].index)


def test_cpu_percent_ignores_first_sample_and_counter_wrap():
    from app.system_health import cpu_percent
    assert cpu_percent(None, (10, 100)) is None
    assert cpu_percent((10, 100), (5, 50)) is None
    assert cpu_percent((10, 100), (35, 200)) == 25.0


PI_REPORT = {"agent_version": 1, "sent_at": None, "model": "Raspberry Pi 5", "uptime_seconds": 86400,
             "cpu_percent": 12.5, "memory": {"total_bytes": 4 * 2**30, "used_bytes": 2**30, "percent": 25.0},
             "disk": {"total_bytes": 32 * 2**30, "free_bytes": 20 * 2**30, "percent": 37.5, "read_only": False},
             "temperature_c": 48.0, "throttled": {"value": 0, "flags": []},
             "network": {"interface": "wlan0", "wifi_signal_dbm": -55, "wired": False},
             "services": {"browser": {"state": "active", "restarts": 0}}, "unknown_future_field": 1}


def test_device_report_is_stored_and_shown_with_the_station(client):
    from datetime import datetime, timezone
    body = {**PI_REPORT, "sent_at": datetime.now(timezone.utc).isoformat()}
    assert client.post("/api/stations/ppe-door/device", json=body).status_code == 200
    client.post("/api/stations/ppe-door/heartbeat", json={"scanner_ready": True})
    report = client.get("/api/system/health").json()
    ppe = next(row for row in report["stations"] if row["id"] == "ppe-door")
    device = ppe["device"]
    assert device["temperature_c"] == 48.0 and device["model"] == "Raspberry Pi 5"
    assert device["stale"] is False and abs(device["clock_skew_seconds"]) < 5
    assert device["history"][-1]["cpu"] == 12.5
    assert "unknown_future_field" not in device
    assert next(row for row in report["stations"] if row["id"] == "shared-door")["device"] is None
    assert not [i for i in report["issues"] if i["title"].startswith("PPE") and i["level"] != "info"]


def test_device_report_validation(client):
    assert client.post("/api/stations/not-a-door/device", json=PI_REPORT).status_code == 404
    assert client.post("/api/stations/ppe-door/device", json={**PI_REPORT, "cpu_percent": 250}).status_code == 422
    assert client.post("/api/stations/ppe-door/device", json={**PI_REPORT, "model": "x" * 500}).status_code == 422


def pi_titles(device, status="ready"):
    from app.system_health import find_issues
    station = {"id": "ppe-door", "name": "PPE", "status": status, "last_seen": None,
               "device": {"stale": False, "age_seconds": 5, "clock_skew_seconds": 0, **device}}
    return {issue["title"]: issue["level"] for issue in find_issues(report_with(stations=[station]))}


def test_pi_rules_flag_heat_power_sd_card_services_clock_and_wifi():
    assert pi_titles(PI_REPORT) == {}
    titles = pi_titles({**PI_REPORT, "temperature_c": 83.0,
                        "throttled": {"value": 0x50005, "flags": ["under_voltage", "throttled", "under_voltage_occurred"]},
                        "disk": {"free_bytes": 10 * 2**30, "percent": 60.0, "read_only": True},
                        "memory": {"percent": 95.0}, "services": {"input": {"state": "failed", "restarts": 12}},
                        "clock_skew_seconds": -600, "network": {"wifi_signal_dbm": -82}, "uptime_seconds": 120})
    assert titles["PPE is overheating (83 °C)"] == "critical"
    assert titles["PPE has low power (under-voltage)"] == "critical"
    assert titles["PPE's SD card is read-only"] == "critical"
    assert titles["PPE is low on memory (95% used)"] == "warning"
    assert titles["PPE: the input service is failed"] == "warning"
    assert titles["PPE's clock is off by 10 min"] == "warning"
    assert titles["PPE has weak Wi-Fi (-82 dBm)"] == "warning"
    assert titles["PPE restarted 2 min ago"] == "info"
    assert "PPE is throttled" not in titles  # explained by the under-voltage issue
    assert pi_titles({**PI_REPORT, "temperature_c": 72.0})["PPE is running hot (72 °C)"] == "warning"
    assert pi_titles({**PI_REPORT, "disk": {"free_bytes": 300 * 2**20, "percent": 99.0}})["PPE's SD card is almost full"] == "critical"


def test_pi_reporting_states():
    from app.system_health import find_issues
    def titles(status, device):
        station = {"id": "ppe-door", "name": "PPE", "status": status, "last_seen": "earlier", "device": device}
        return {issue["title"]: issue["level"] for issue in find_issues(report_with(stations=[station]))}
    assert titles("ready", None) == {"PPE: no Pi stats": "info"}
    assert titles("ready", {"stale": True, "age_seconds": 300}) == {"PPE: Pi stats stopped": "warning"}
    fresh = {**PI_REPORT, "stale": False, "age_seconds": 5, "clock_skew_seconds": 0}
    assert titles("offline", fresh) == {"PPE: the Pi is on but the kiosk page is not running": "warning"}
    assert titles("offline", None) == {"PPE is offline": "warning"}


def test_versions_are_reported_and_an_older_pi_is_flagged(client, monkeypatch):
    from datetime import datetime, timezone
    from app import version
    monkeypatch.setattr(version, "current", lambda: {"version": "v2026.11.12-2", "commit": "abc1234", "built_at": None})
    assert client.get("/api/version").json()["version"] == "v2026.11.12-2"
    assert client.get("/api/health").json()["version"] == "v2026.11.12-2"
    for station, release in (("ppe-door", "v2026.11.12-1"), ("footwear-door", "v2026.11.12-2")):
        body = {**PI_REPORT, "sent_at": datetime.now(timezone.utc).isoformat(), "package_version": release}
        assert client.post(f"/api/stations/{station}/device", json=body).status_code == 200
    report = client.get("/api/system/health").json()
    assert report["application"]["version"] == "v2026.11.12-2"
    behind = [i["title"] for i in report["issues"] if "older version" in i["title"]]
    assert len(behind) == 1 and "v2026.11.12-1" in behind[0] and "PPE" in behind[0]
