from app import printer_network as net

USB = "ippusb://Brother%20QL-810W._ipp._tcp.local./?uuid=e3248000-80ce-11db-8000-0123456789ab"
WIFI = "ipp://192.0.2.10/ipp/print"
URIS = {"Brother_QL_810W": USB, "Brother_QL_810W_WiFi": WIFI, "Office_Laser": "ipp://192.0.2.50/ipp/print"}


def queue(name, **facts):
    return {"name": name, "state": "idle", "alerts": [], **facts}


def test_partner_is_the_same_printer_on_the_other_connection(monkeypatch):
    monkeypatch.setattr(net, "preferences", lambda: {})
    assert net.partner("Brother_QL_810W", URIS) == "Brother_QL_810W_WiFi"
    assert net.partner("Brother_QL_810W_WiFi", URIS) == "Brother_QL_810W"
    assert net.partner("Office_Laser", URIS) is None


def test_unplugged_usb_falls_back_to_wifi(monkeypatch):
    monkeypatch.setattr(net, "preferences", lambda: {})
    monkeypatch.setattr(net, "_run", lambda *_a, **_k: "+-o Root\n  +-o Magic Keyboard\n")
    monkeypatch.setattr(net, "reachable", lambda uri, timeout=2.0: uri == WIFI)
    printers = [queue("Brother_QL_810W", alerts=["offline"]), queue("Brother_QL_810W_WiFi")]
    chosen, note = net.choose_queue("Brother_QL_810W", printers, URIS)
    assert chosen == "Brother_QL_810W_WiFi" and "USB printer not connected" in note


def test_plugged_in_usb_is_used_even_with_a_stale_offline_alert(monkeypatch):
    monkeypatch.setattr(net, "preferences", lambda: {})
    monkeypatch.setattr(net, "_run", lambda *_a, **_k: "+-o QL-810W@01100000\n")
    monkeypatch.setattr(net, "reachable", lambda uri, timeout=2.0: True)
    printers = [queue("Brother_QL_810W", alerts=["offline"]), queue("Brother_QL_810W_WiFi")]
    assert net.choose_queue("Brother_QL_810W", printers, URIS) == ("Brother_QL_810W", None)


def test_no_fallback_when_wifi_is_also_down(monkeypatch):
    monkeypatch.setattr(net, "preferences", lambda: {})
    monkeypatch.setattr(net, "_run", lambda *_a, **_k: "+-o Root\n")
    monkeypatch.setattr(net, "reachable", lambda uri, timeout=2.0: False)
    printers = [queue("Brother_QL_810W"), queue("Brother_QL_810W_WiFi")]
    assert net.choose_queue("Brother_QL_810W", printers, URIS) == ("Brother_QL_810W", None)


def test_arp_and_uri_helpers():
    table = "? (192.0.2.33) at 1:23:45:67:89:ab on en0 ifscope [ethernet]\n? (192.0.2.1) at 0:0:5e:0:1:1 on en0\n"
    assert net.arp_lookup("0123456789ab", table) == "192.0.2.33"
    assert net.with_host(WIFI, "192.0.2.77") == "ipp://192.0.2.77/ipp/print"
    assert net.with_host("socket://192.0.2.10:9100", "192.0.2.77") == "socket://192.0.2.77:9100"
    assert net.model(USB) == "QL-810W"


def test_moved_printer_is_found_by_uuid_and_the_queue_updated(monkeypatch, tmp_path):
    monkeypatch.setenv("FABRIC_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(net, "preferences", lambda: {})
    monkeypatch.setattr(net, "devices", lambda lpstat=None: dict(URIS))
    monkeypatch.setattr(net, "reachable", lambda uri, timeout=2.0: False)
    monkeypatch.setattr(net, "browse", lambda seconds=4.0: ["Brother QL-810W", "HP OfficeJet"])
    monkeypatch.setattr(net, "lookup", lambda name, seconds=3.0: ("BRWX.local", "e3248000-80ce-11db-8000-0123456789ab"))
    monkeypatch.setattr(net, "resolve", lambda host, seconds=3.0: "192.0.2.99")
    calls = []

    class Done:
        returncode, stderr = 0, ""
    monkeypatch.setattr(net.subprocess, "run", lambda args, **_k: calls.append(args) or Done())
    result = net.refresh_wifi_queue()
    assert result["result"] == "moved" and result["ip"] == "192.0.2.99"
    assert calls[0][-3:] == ["Brother_QL_810W_WiFi", "-v", "ipp://192.0.2.99/ipp/print"]
    assert net.last_status()["ip"] == "192.0.2.99"
