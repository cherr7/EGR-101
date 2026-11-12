"""Shared builders for the API tests."""


def request_id(client, name="shared-door"):
    """Reserve one retry ID the way a kiosk ('<station>') or the label dialog ('print') does."""
    block = client.post("/api/request-ids", json={"client": name, "count": 1})
    assert block.status_code == 200, block.text
    return f'{name}-{block.json()["first"]}'


def prep_sheet(project="000000001234", material="COTTON-A", test="ASTM-D5034", treatment="AR", count=20, **header):
    """A one-row prep sheet in the draft's sheet layout."""
    return {"headers": {"Preparation": {"project_number": project, **header}},
            "rows": [{"sheet": "Preparation", "test": test, "conditions": treatment,
                      "conditioning": str(count), "sample": material}]}
