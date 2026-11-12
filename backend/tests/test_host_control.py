import json

import pytest

CF = {"Cf-Ray": "8abc-IAD", "Cf-Connecting-Ip": "203.0.113.9"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    from app import host_control
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("FABRIC_SOURCE_REPO", raising=False)
    host_control._ATTEMPTS.clear()
    (tmp_path / "update-check.json").write_text(json.dumps({"latest": None}))   # no network check in tests
    return tmp_path


def test_local_requests_never_need_the_passcode(client, state):
    assert client.put("/api/outside", json={"passcode": "lab-secret", "enabled": True}).status_code == 200
    assert client.get("/api/config").status_code == 200


def test_outside_requests_need_the_passcode(client, state):
    assert client.get("/api/config", headers=CF).status_code == 403          # off until a passcode is set
    client.put("/api/outside", json={"passcode": "lab-secret", "enabled": True})
    assert client.get("/api/config", headers=CF).json()["detail"] == "Passcode required."
    assert client.get("/api/health", headers=CF).status_code == 200
    assert client.post("/api/outside/login", json={"passcode": "wrong-one"}, headers=CF).status_code == 401
    login = client.post("/api/outside/login", json={"passcode": "lab-secret"}, headers=CF)
    assert login.status_code == 200
    cookie = login.cookies.get("fabric_outside") or login.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    assert "Secure" in login.headers["set-cookie"] and "HttpOnly" in login.headers["set-cookie"]
    signed = {**CF, "Cookie": f"fabric_outside={cookie}"}
    assert client.get("/api/config", headers=signed).status_code == 200
    # A new passcode signs everyone out.
    client.put("/api/outside", json={"passcode": "another-secret"})
    assert client.get("/api/config", headers=signed).status_code == 401


def test_outside_cannot_change_settings_or_update(client, state):
    client.put("/api/outside", json={"passcode": "lab-secret", "enabled": True})
    cookie = client.post("/api/outside/login", json={"passcode": "lab-secret"}, headers=CF).headers["set-cookie"].split(";")[0]
    signed = {**CF, "Cookie": cookie}
    assert client.put("/api/outside", json={"enabled": False}, headers=signed).status_code == 403
    assert client.post("/api/system/update", headers=signed).status_code == 403
    assert "url" not in client.get("/api/outside/status", headers=CF).json()


def test_passcode_tries_are_limited(client, state):
    client.put("/api/outside", json={"passcode": "lab-secret", "enabled": True})
    codes = [client.post("/api/outside/login", json={"passcode": "nope"}, headers=CF).status_code for _ in range(11)]
    assert codes[-1] == 429


def test_enabling_needs_a_passcode(client, state):
    assert client.put("/api/outside", json={"enabled": True}).status_code == 409
    assert client.put("/api/outside", json={"passcode": "short"}).status_code == 422


def test_update_status_compares_releases(state, monkeypatch):
    from app import host_control
    monkeypatch.setattr(host_control.app_version, "current", lambda: {"version": "v2026.11.12-2"})
    for latest, available in (("v2026.11.12-10", True), ("v2026.11.12-2", False), ("v2026.11.1-5", False), (None, False)):
        (state / "update-check.json").write_text(json.dumps({"latest": latest}))
        assert host_control.update_status()["available"] is available
    monkeypatch.setattr(host_control.app_version, "current", lambda: {"version": "v2026.11.12-2-3-gabc1234"})
    assert host_control.update_status()["available"] is False


def test_update_needs_a_checkout(client, state):
    assert client.post("/api/system/update").status_code == 409
    assert client.get("/api/system/update").json()["can_update"] is False


def test_station_package_is_served_with_its_checksum(client, state, tmp_path, monkeypatch):
    from app import host_control
    package = tmp_path / "station-package.tar.gz"
    package.write_bytes(b"not really a tarball")
    monkeypatch.setattr(host_control, "package_path", lambda: package)
    host_control._PACKAGE.clear()
    info = client.get("/api/station/package/info").json()
    download = client.get("/api/station/package")
    assert download.content == b"not really a tarball"
    assert download.headers["x-package-sha256"] == info["sha256"]


def test_menu_summary(client, state):
    summary = client.get("/api/host/summary").json()
    assert summary["version"]
    assert {s["name"] for s in summary["stations"]} >= {"Shared"}
    assert summary["update"]["in_progress"] is False


def test_new_link_restarts_the_tunnel_from_the_lab_only(client, state):
    assert client.post("/api/outside/new-link").status_code == 409            # off
    client.put("/api/outside", json={"passcode": "lab-secret", "enabled": True})
    (state / "tunnel.json").write_text(json.dumps({"url": "https://old-link.trycloudflare.com"}))
    (state / "tunnel.pid").write_text("999999")                               # not a running cloudflared
    assert client.post("/api/outside/new-link", headers=CF).status_code in {401, 403}
    assert client.post("/api/outside/new-link").json()["url"] is None
    assert not (state / "tunnel.json").exists()
