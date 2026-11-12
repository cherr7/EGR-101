import io
import subprocess

from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader
import pytest
import zxingcpp

from app.barcodes import MAX_ID, code_id, datamatrix_image, label_code
from app import printing
from helpers import prep_sheet, request_id


def install_fake_cups(monkeypatch, *, timeout=False, alerts=None, waiting=()):
    calls = []
    monkeypatch.delenv("TRACKER_PRINTER_QUEUE", raising=False)
    monkeypatch.setattr(printing.shutil, "which", lambda command: "/usr/bin/" + command)

    def run(argv, **kwargs):
        calls.append(argv)
        assert not kwargs.get("shell")
        if argv[0].endswith("lpstat"):
            value = ("Brother_QL accepting requests since today\n" if "-a" in argv else
                     "printer Brother_QL is idle.  enabled since today\n\tAlerts: " + " ".join(alerts or ["none"]) + "\n" if "-l" in argv else
                     "".join(f"{job} staff 1024 today\n" for job in waiting) if "-o" in argv else
                     "system default destination: Brother_QL\n")
            return subprocess.CompletedProcess(argv, 0, value, "")
        if timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        assert argv[1:3] == ["-d", "Brother_QL"]
        assert "media=Custom.62x29mm" in argv   # Brother DK-1209 die-cut label
        assert "print-scaling=none" in argv and "CutMedia=EndOfPage" in argv
        with open(argv[-1], "rb") as file:
            assert file.read(4) == b"%PDF"
        return subprocess.CompletedProcess(argv, 0, "request id is Brother_QL-42 (1 file(s))\n", "")

    monkeypatch.setattr(printing.subprocess, "run", run)
    return calls


def test_every_label_number_is_a_lossless_fixed_16_by_16_datamatrix():
    for identifier in [1, 9, 10, 572, 123456789, MAX_ID]:
        payload = label_code(identifier)
        assert payload.isdecimal() and code_id(payload) == identifier
        image = datamatrix_image(payload, module_pixels=8)
        assert image.size == (144, 144)  # Always 16 symbol modules + one quiet module each edge.
        decoded = zxingcpp.read_barcode(image)
        assert decoded.format == zxingcpp.BarcodeFormat.DataMatrix
        assert decoded.text == payload
        assert decoded.bytes == payload.encode("ascii")
    assert label_code(572) == "5724"  # Damm check digit reference value


def test_check_digit_rejects_typos_swaps_and_non_labels():
    payload = label_code(4815162342)
    for index in range(len(payload)):
        for digit in "0123456789":
            if digit != payload[index]:
                with pytest.raises(ValueError):
                    code_id(payload[:index] + digit + payload[index + 1:])
    for index in range(len(payload) - 1):
        if payload[index] != payload[index + 1]:
            with pytest.raises(ValueError):
                code_id(payload[:index] + payload[index + 1] + payload[index] + payload[index + 2:])
    for bad in ["", "7", label_code(MAX_ID)[:-1] + "x", str(MAX_ID + 1) + str(0), "UL1:P:12", "1" * 21]:
        with pytest.raises(ValueError):
            code_id(bad)


def test_printer_inventory_never_enables_default_without_configuration(monkeypatch):
    calls = install_fake_cups(monkeypatch)
    result = printing.printer_inventory()
    assert result["available"] and not result["enabled"]
    assert result["configured_queue"] is None
    assert result["printers"] == [{"name": "Brother_QL", "is_default": True, "label_printer": True, "state": "idle",
                                   "alerts": [], "waiting_jobs": 0, "warnings": []}]
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
        assert abs(float(page.mediabox.height) - 29 / 25.4 * 72) < .01
        assert "00073-LAB" in page.extract_text()
        image = Image.open(io.BytesIO(list(page.images)[0].data))
        decoded_ids.append(code_id(zxingcpp.read_barcode(image).text))
    assert decoded_ids == [specimen["id"] for specimen in stack["specimens"]]
    after = client.get(f'/api/stacks/{stack["id"]}').json()
    assert [event["kind"] for event in after["events"]] == ["registration"]


def test_print_submission_idempotence_and_audit(client, stack_factory, monkeypatch):
    stack = stack_factory(count=2)
    calls = install_fake_cups(monkeypatch)
    body = {"request_id": request_id(client, "print"), "stack_ids": [stack["id"]], "printer_queue": "Brother_QL"}
    response = client.post("/api/print-jobs", json=body)
    assert response.is_success, response.text
    assert response.json()["status"] == "submitted"
    assert response.json()["cups_job_id"] == "Brother_QL-42,Brother_QL-42"   # one CUPS job per label
    assert response.json()["physical_print_confirmed"] is False
    retry = client.post("/api/print-jobs", json=body)
    assert retry.is_success and retry.json()["request_id"] == body["request_id"]
    assert len([call for call in calls if call[0].endswith("/lp")]) == 2
    status = client.get(f'/api/print-jobs/{body["request_id"]}')
    assert status.json()["status"] == "submitted"
    assert client.post("/api/print-jobs", json={**body, "copies": 2}).status_code == 409
    after = client.get(f'/api/stacks/{stack["id"]}').json()
    events = [event for event in after["events"] if event["kind"] == "label_print"]
    assert len(events) == 1 and len(events[0]["specimen_ids"]) == 2
    assert events[0]["payload"]["status"] == "submitted"
    assert all(specimen["ENTRY"] is None for specimen in after["specimens"])


def test_print_timeout_is_unknown_and_same_request_id_never_resubmits(client, stack_factory, monkeypatch):
    stack = stack_factory(count=1)
    calls = install_fake_cups(monkeypatch, timeout=True)
    body = {"request_id": request_id(client, "print"), "stack_ids": [stack["id"]], "printer_queue": "Brother_QL"}
    first = client.post("/api/print-jobs", json=body)
    assert first.is_success, first.text
    assert first.json()["status"] == "unknown"
    second = client.post("/api/print-jobs", json=body)
    assert second.json()["status"] == "unknown"
    assert len([call for call in calls if call[0].endswith("/lp")]) == 1


def test_print_requires_available_explicit_queue_and_rejects_command_options(client, stack_factory, monkeypatch):
    stack = stack_factory(count=1)
    calls = install_fake_cups(monkeypatch)
    base = {"request_id": request_id(client, "print"), "stack_ids": [stack["id"]]}
    assert client.post("/api/print-jobs", json=base).status_code == 422
    assert client.post("/api/print-jobs", json={**base, "printer_queue": "-h attacker"}).status_code == 422
    assert client.post("/api/print-jobs", json={**base, "printer_queue": "Unknown"}).status_code == 409
    assert not any(call[0].endswith("/lp") for call in calls)


def test_printer_state_warns_about_offline_printer_and_waiting_jobs(monkeypatch):
    install_fake_cups(monkeypatch, alerts=["offline-report", "media-empty-error"], waiting=["Brother_QL-7", "Brother_QL-8"])
    printer = printing.printer_inventory()["printers"][0]
    assert printer["alerts"] == ["offline", "media-empty"] and printer["waiting_jobs"] == 2
    assert any("offline" in w for w in printer["warnings"]) and any("out of labels" in w for w in printer["warnings"])
    assert any("2 job(s) are already waiting" in w for w in printer["warnings"])


def test_print_status_reports_labels_still_waiting_in_the_mac_queue(client, stack_factory, monkeypatch):
    stack = stack_factory(count=1)
    install_fake_cups(monkeypatch, waiting=["Brother_QL-42"])
    body = {"request_id": request_id(client, "print"), "stack_ids": [stack["id"]], "printer_queue": "Brother_QL"}
    assert client.post("/api/print-jobs", json=body).json()["status"] == "submitted"
    status = client.get(f'/api/print-jobs/{body["request_id"]}').json()
    assert status["waiting_cups_jobs"] == 1 and "do not reprint" in status["message"]
    install_fake_cups(monkeypatch)
    assert client.get(f'/api/print-jobs/{body["request_id"]}').json()["waiting_cups_jobs"] == 0


def test_image_preview_shows_the_same_codes_as_the_printed_pdf(client, stack_factory):
    stack = stack_factory(count=14)
    response = client.post("/api/labels/preview.png", json={"stack_ids": [stack["id"]]})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.headers["x-label-count"] == "14"
    found = {code_id(result.text) for result in zxingcpp.read_barcodes(Image.open(io.BytesIO(response.content)))}
    assert found == {p["id"] for p in stack["specimens"][:12]}   # the first 12, decodable as drawn


def test_label_job_accepts_every_stack_of_a_large_registered_job():
    # A filled 1970 job prep sheet registers 120+ stacks; previewing them is one job.
    from app.printing import LabelPreviewBody
    assert len(LabelPreviewBody(stack_ids=list(range(1, 151))).stack_ids) == 150
