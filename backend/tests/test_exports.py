import csv
import io
from uuid import uuid4

from pypdf import PdfReader


def register(client, **overrides):
    row = {"project_number": "000012ABC123", "material_ref": "Thermal barrier",
           "test_code": "FLAME", "preconditioning_code": "50L", "specimen_count": 2, **overrides}
    response = client.post("/api/prep-drafts", json={"rows": [row]})
    assert response.is_success, response.text
    result = client.post(f'/api/prep-drafts/{response.json()["id"]}/commit')
    assert result.is_success, result.text
    stack = client.get(f'/api/stacks/{result.json()["stack_ids"][0]}')
    assert stack.is_success, stack.text
    return stack.json()


def move(client, stack, direction, **extra):
    body = {"request_id": str(uuid4()), "station_id": "shared-door", "code": f'UL1:S:{stack["id"]}',
            "direction": direction, "scope": "stack", **extra}
    result = client.post("/api/movements", json=body)
    assert result.is_success, result.text
    return result.json()


def rows(response):
    assert response.is_success, response.text
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_labels_registered_identifiers_and_reprints(client):
    stack = register(client)
    url = f'/api/stacks/{stack["id"]}/labels'
    exported = rows(client.get(url + ".csv"))
    assert exported[0]["qr_payload"] == f'UL1:S:{stack["id"]}'
    assert [row["qr_payload"] for row in exported[1:]] == [f'UL1:P:{p["id"]}' for p in stack["specimens"]]
    assert all(row["project_number"] == "000012ABC123" for row in exported)
    assert rows(client.get(url + ".csv")) == exported
    assert len(rows(client.get(url + ".csv?include_header=false"))) == 2
    pdf_response = client.get(url + ".pdf")
    assert pdf_response.is_success
    pdf = PdfReader(io.BytesIO(pdf_response.content))
    assert len(pdf.pages) == 3
    for page in pdf.pages:
        assert abs(float(page.mediabox.width) - 54 * 72 / 25.4) < .01
        assert abs(float(page.mediabox.height) - 25 * 72 / 25.4) < .01
        assert "000012ABC123" in page.extract_text()
        assert len(list(page.images)) == 1
    assert client.get(url + ".pdf?width_mm=1").status_code == 422
    assert client.get(f"/api/stacks/{uuid4()}/labels.pdf").status_code == 404


def test_session_report_and_audit_agree_on_early_exit(client):
    stack = register(client)
    checked_in = move(client, stack, "IN")
    assert checked_in["status"] == "committed"
    warning = move(client, stack, "OUT")
    assert warning["status"] == "acknowledgment_required"
    result = move(client, stack, "OUT", acknowledge_early=True, expected_version=warning["version"])
    assert result["status"] == "committed"
    sessions = rows(client.get("/api/exports/sessions.csv?project_number=000012ABC123"))
    assert len(sessions) == 2
    assert {row["specimen_id"] for row in sessions} == {p["id"] for p in stack["specimens"]}
    assert all(row["conditioning_status"] == "INCOMPLETE" and row["early_exit_acknowledged"] == "True" for row in sessions)
    history = rows(client.get("/api/exports/history.csv?project_number=000012ABC123"))
    assert {row["id"] for row in history} >= {checked_in["event_id"], result["event_id"]}
    report = client.get("/api/exports/report?project_number=000012ABC123")
    assert report.is_success
    assert "INCOMPLETE" in report.text and "early exit acknowledged" in report.text
    assert "America/New_York" in report.text
    assert all(p["id"] in report.text for p in stack["specimens"])
    assert result["event_id"] in report.text
    assert rows(client.get("/api/exports/sessions.csv?project_number=other")) == []


def test_export_escapes_untrusted_text(client):
    stack = register(client, material_ref='=HYPERLINK("https://example.invalid")', test_code="<script>alert(1)</script>")
    exported = rows(client.get(f'/api/stacks/{stack["id"]}/labels.csv'))
    assert exported[0]["material_ref"].startswith("'=HYPERLINK")
    report = client.get("/api/exports/report").text
    assert "<script>alert(1)</script>" not in report
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report


def test_undo_retains_observation_in_export(client):
    stack = register(client, specimen_count=1)
    observation = move(client, stack, "IN")
    response = client.post("/api/auth/login", json={"username": "supervisor", "password": "classroom-demo"})
    assert response.is_success
    correction = client.post("/api/corrections", json={"kind": "undo", "event_id": observation["event_id"], "reason": "Scanned the wrong stack"})
    assert correction.is_success, correction.text
    assert rows(client.get("/api/exports/sessions.csv")) == []
    history = rows(client.get("/api/exports/history.csv"))
    assert {row["kind"] for row in history} >= {"movement", "undo"}
    assert any(row["id"] == observation["event_id"] for row in history)
