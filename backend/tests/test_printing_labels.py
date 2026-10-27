import io
import subprocess
from uuid import UUID, uuid4

from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader
import pytest
import zxingcpp

from app.barcodes import datamatrix_image, id_payload, payload_id
from app import printing


def install_fake_cups(monkeypatch, *, timeout=False):
    calls = []
    monkeypatch.delenv("TRACKER_PRINTER_QUEUE", raising=False)
    monkeypatch.setattr(printing.shutil, "which", lambda command: "/usr/bin/" + command)

    def run(argv, **kwargs):
        calls.append(argv)
        assert not kwargs.get("shell")
        if argv[0].endswith("lpstat"):
            value = "Brother_QL accepting requests since today\n" if "-a" in argv else "system default destination: Brother_QL\n"
            return subprocess.CompletedProcess(argv, 0, value, "")
        if timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        assert argv[1:3] == ["-d", "Brother_QL"]
        assert "media=Custom.62x31mm" in argv
        assert "print-scaling=none" in argv
        with open(argv[-1], "rb") as file:
            assert file.read(4) == b"%PDF"
        return subprocess.CompletedProcess(argv, 0, "request id is Brother_QL-42 (1 file(s))\n", "")

    monkeypatch.setattr(printing.subprocess, "run", run)
    return calls


def test_full_128_bit_datamatrix_is_lossless_and_id_only():
    identifiers = [UUID(int=0), UUID(int=(1 << 128) - 1), uuid4()]
    for identifier in identifiers:
        payload = id_payload(str(identifier))
        assert len(payload) == 39 and payload.isdecimal()
        image = datamatrix_image(payload, module_pixels=8)
        assert image.size == (176, 176)  # 20 symbol modules + one quiet module each edge.
        decoded = zxingcpp.read_barcode(image)
        assert decoded.format == zxingcpp.BarcodeFormat.DataMatrix
        assert decoded.text == payload
        assert payload_id(decoded.text) == str(identifier)
        assert decoded.bytes == payload.encode("ascii")
    with pytest.raises(ValueError):
        payload_id(str(1 << 128))
    with pytest.raises(ValueError):
        payload_id("UL1:P:" + str(identifiers[2]))


def test_printer_inventory_never_enables_default_without_configuration(monkeypatch):
    calls = install_fake_cups(monkeypatch)
    result = printing.printer_inventory()
    assert result["available"] and not result["enabled"]
    assert result["configured_queue"] is None
    assert result["printers"] == [{"name": "Brother_QL", "is_default": True}]
    assert not any(call[0].endswith("/lp") for call in calls)
    monkeypatch.setenv("TRACKER_PRINTER_QUEUE", "Brother_QL")
    assert printing.printer_inventory()["enabled"]
    monkeypatch.setattr(printing.shutil, "which", lambda _: None)
    assert printing.printer_inventory()["available"] is False


def test_label_preview_and_pdf_codes_are_registered_ids(client, stack_factory):
    stack = stack_factory(count=3, project="00073-LAB")
    response = client.post("/api/labels/preview", json={"stack_ids": [stack["id"]]})
    assert response.is_success, response.text
    pdf = PdfReader(io.BytesIO(response.content))
    assert len(pdf.pages) == 3
    decoded_ids = []
    for page in pdf.pages:
        assert abs(float(page.mediabox.width) - 62 / 25.4 * 72) < .01
        assert abs(float(page.mediabox.height) - 31 / 25.4 * 72) < .01
        assert "00073-LAB" in page.extract_text()
        image = Image.open(io.BytesIO(list(page.images)[0].data))
        decoded_ids.append(payload_id(zxingcpp.read_barcode(image).text))
    assert decoded_ids == [specimen["id"] for specimen in stack["specimens"]]
    after = client.get(f'/api/stacks/{stack["id"]}').json()
    assert [event["kind"] for event in after["events"]] == ["registration"]


def test_print_submission_idempotence_and_audit(client, stack_factory, monkeypatch):
    stack = stack_factory(count=2)
    calls = install_fake_cups(monkeypatch)
    body = {"request_id": str(uuid4()), "stack_ids": [stack["id"]], "printer_queue": "Brother_QL"}
    response = client.post("/api/print-jobs", json=body)
    assert response.is_success, response.text
    assert response.json()["status"] == "submitted"
    assert response.json()["cups_job_id"] == "Brother_QL-42"
    assert response.json()["physical_print_confirmed"] is False
    retry = client.post("/api/print-jobs", json=body)
    assert retry.is_success and retry.json()["id"] == body["request_id"]
    assert len([call for call in calls if call[0].endswith("/lp")]) == 1
    status = client.get(f'/api/print-jobs/{body["request_id"]}')
    assert status.json()["status"] == "submitted"
    assert client.post("/api/print-jobs", json={**body, "copies": 2}).status_code == 409
    after = client.get(f'/api/stacks/{stack["id"]}').json()
    events = [event for event in after["events"] if event["kind"] == "label_print"]
    assert len(events) == 1 and len(events[0]["specimen_ids"]) == 2
    assert events[0]["payload"]["status"] == "submitted"
    assert all(specimen["ENTRY"] is None for specimen in after["specimens"])


def test_print_timeout_is_unknown_and_same_uuid_never_resubmits(client, stack_factory, monkeypatch):
    stack = stack_factory(count=1)
    calls = install_fake_cups(monkeypatch, timeout=True)
    body = {"request_id": str(uuid4()), "stack_ids": [stack["id"]], "printer_queue": "Brother_QL"}
    first = client.post("/api/print-jobs", json=body)
    assert first.is_success, first.text
    assert first.json()["status"] == "unknown"
    second = client.post("/api/print-jobs", json=body)
    assert second.json()["status"] == "unknown"
    assert len([call for call in calls if call[0].endswith("/lp")]) == 1


def test_print_requires_available_explicit_queue_and_rejects_command_options(client, stack_factory, monkeypatch):
    stack = stack_factory(count=1)
    calls = install_fake_cups(monkeypatch)
    base = {"request_id": str(uuid4()), "stack_ids": [stack["id"]]}
    assert client.post("/api/print-jobs", json=base).status_code == 422
    assert client.post("/api/print-jobs", json={**base, "printer_queue": "-h attacker"}).status_code == 422
    assert client.post("/api/print-jobs", json={**base, "printer_queue": "Unknown"}).status_code == 409
    assert not any(call[0].endswith("/lp") for call in calls)
