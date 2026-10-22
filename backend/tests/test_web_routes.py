def test_kiosk_routes_preserve_station_and_open_dedicated_view(client):
    for route in ["/kiosk", "/station.html"]:
        response = client.get(route + "?station=shared-door", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/?station=shared-door#kiosk"
        assert response.headers["cache-control"] == "no-store"
    response = client.get("/kiosk", follow_redirects=False)
    assert response.headers["location"] == "/#kiosk"
