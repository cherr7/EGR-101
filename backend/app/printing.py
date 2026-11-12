"""Explicit, idempotent label submission to the Mac's existing CUPS queues.

The application never discovers a printer and prints automatically. A person
previews the PDF and selects a queue. 'submitted' means accepted by CUPS, not
proof that a physical label emerged from the printer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import get_db
from .exports import label_height_mm, labels_pdf, labels_png
from .models import AuditEvent, PrintJob, Stack, utcnow
from .schemas import split_request_id
from .services import check_request_number, stack_detail

router = APIRouter(prefix="/api", tags=["Label printing"])
QUEUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
PRINT_REQUEST = r"^print-[1-9][0-9]{0,17}$"


class LabelPreviewBody(BaseModel):
    stack_ids: list[int] = Field(min_length=1, max_length=100)
    selected_specimen_ids: list[int] | None = Field(default=None, min_length=1, max_length=5000)
    width_mm: float = Field(default=62, ge=40, le=150)      # Brother DK-1209 die-cut labels, 62 x 29 mm

    @field_validator("stack_ids")
    @classmethod
    def unique_stacks(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Each stack can appear only once in a label job.")
        return values

    @field_validator("selected_specimen_ids")
    @classmethod
    def unique_specimens(cls, values):
        if values is not None and len(set(values)) != len(values):
            raise ValueError("Each specimen can appear only once in a label job.")
        return values


class PrintBody(LabelPreviewBody):
    request_id: str = Field(pattern=PRINT_REQUEST)   # 'print-<n>', n reserved from /api/request-ids
    printer_queue: str = Field(min_length=1, max_length=200)
    copies: int = Field(default=1, ge=1, le=10)

    @field_validator("printer_queue")
    @classmethod
    def safe_queue(cls, value):
        if not QUEUE_PATTERN.fullmatch(value):
            raise ValueError("Choose an installed printer queue.")
        return value


ALERT_MESSAGES = {   # CUPS printer-state-reasons a label printer reports, in staff words
    "offline": "The printer is offline. Jobs sent now wait on the Mac and print when it reconnects.",
    "media-empty": "The printer is out of labels.", "media-needed": "The printer needs labels loaded.",
    "media-jam": "Labels are jammed.", "cover-open": "The printer cover is open.",
    "door-open": "The printer cover is open.", "paused": "The Mac's queue for this printer is paused.",
}


def _lpstat(lpstat: str, *args: str) -> str:
    try:
        return subprocess.run([lpstat, *args], check=False, capture_output=True, text=True, timeout=5,
                              env={**os.environ, "LC_ALL": "C"}).stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def waiting_jobs(lpstat: str, queue: str) -> list[str]:
    """CUPS job ids still queued (not yet printed) for this printer."""
    return [line.split()[0] for line in _lpstat(lpstat, "-o", queue).splitlines()
            if line.split() and line.split()[0].startswith(queue + "-")]


def queue_health(lpstat: str, queue: str) -> dict:
    """What the Mac knows about one printer: state, alerts, and jobs still waiting to print.
    CUPS keeps accepting jobs for an offline printer and prints them when it returns, so a
    backlog here means a reprint now would come out twice."""
    detail = _lpstat(lpstat, "-l", "-p", queue)
    state = ("stopped" if re.search(r"\bdisabled\b", detail) else "printing" if "now printing" in detail
             else "idle" if " is idle" in detail else "unknown")
    alerts_line = re.search(r"Alerts:[ \t]*(.*)", detail)
    alerts = [item.removesuffix("-report").removesuffix("-error").removesuffix("-warning")
              for item in re.split(r"[\s,]+", alerts_line.group(1)) if item and item != "none"] if alerts_line else []
    waiting = waiting_jobs(lpstat, queue)
    warnings = list(dict.fromkeys(ALERT_MESSAGES[a] for a in alerts if a in ALERT_MESSAGES))
    if state == "stopped":
        warnings.insert(0, "The Mac has stopped this printer's queue. Resume it in System Settings > Printers.")
    if waiting:
        warnings.append(f"{len(waiting)} job(s) are already waiting for this printer. They print when it is ready; "
                        "cancel them in the Mac's print queue before printing the same labels again.")
    return {"state": state, "alerts": alerts, "waiting_jobs": len(waiting), "warnings": warnings}


def printer_inventory() -> dict:
    configured = os.getenv("TRACKER_PRINTER_QUEUE", "").strip() or None
    lp, lpstat = shutil.which("lp"), shutil.which("lpstat")
    result = {"available": False, "enabled": False, "configured_queue": configured,
              "printers": [], "message": "CUPS is unavailable. PDF preview and download still work."}
    if not lp or not lpstat:
        return result
    try:
        queues = subprocess.run([lpstat, "-a"], check=False, capture_output=True,
                                text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
        default = subprocess.run([lpstat, "-d"], check=False, capture_output=True,
                                 text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        result["message"] = "Printer service is not responding. PDF download still works."
        return result
    default_match = re.search(r"default destination:\s*(\S+)", default.stdout or "")
    default_name = default_match.group(1) if default_match else None
    names = []
    for line in (queues.stdout or "").splitlines():
        parts = line.split(None, 1)
        if parts and QUEUE_PATTERN.fullmatch(parts[0]) and " not accepting " not in line:
            names.append(parts[0])
    result["printers"] = [{"name": name, "is_default": name == default_name,
                           "label_printer": bool(re.search(r"\bQL|Brother", name, re.I)), **queue_health(lpstat, name)}
                          for name in sorted(set(names))]
    result["available"] = bool(names)
    result["enabled"] = configured in names
    result["message"] = ("Choose a printer after reviewing the labels."
                         if names else "No accepting printer queues found. Add the Brother printer in macOS Settings.")
    if configured and configured not in names:
        result["message"] = "The configured printer queue is unavailable. PDF download still works."
    return result


def _details(db: Session, stack_ids: list[int], selected_specimen_ids: list[int] | None = None) -> list[dict]:
    details = []
    for identifier in stack_ids:
        stack = db.get(Stack, identifier)
        if stack is None:
            raise HTTPException(404, "A selected stack is no longer registered.")
        details.append(stack_detail(db, stack, include_events=False))
    if selected_specimen_ids is not None:
        selected = set(selected_specimen_ids)
        available = {specimen["id"] for item in details for specimen in item["specimens"]}
        if not selected or not selected <= available:
            raise HTTPException(422, "Select registered specimens belonging to the selected stacks.")
        details = [{**item, "specimens": [specimen for specimen in item["specimens"] if specimen["id"] in selected]}
                   for item in details]
        details = [item for item in details if item["specimens"]]
    if sum(len(item["specimens"]) for item in details) > 5000:
        raise HTTPException(422, "Preview at most 5,000 labels per job.")
    return details


def _job_dict(job: PrintJob) -> dict:
    return {"id": job.number, "request_id": f"print-{job.number}", "status": job.status,
            "printer_queue": job.printer_queue, "cups_job_id": job.cups_job_id,
            "stack_ids": job.stack_ids, "options": job.options, "error": job.error,
            "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat(),
            "physical_print_confirmed": False,
            "needs_review": job.status in {"submitting", "unknown"},
            "message": "Accepted by the Mac print queue; verify the physical labels."
                       if job.status == "submitted" else
                       "Submission may have reached the printer. Check the queue before creating a new job."
                       if job.status in {"submitting", "unknown"} else job.error}


@router.get("/printers")
def list_printers():
    return printer_inventory()


@router.post("/labels/preview.png")
def preview_labels_image(body: LabelPreviewBody, db: Session = Depends(get_db)):
    image, total = labels_png(_details(db, body.stack_ids, body.selected_specimen_ids), body.width_mm)
    return Response(image, media_type="image/png", headers={"Cache-Control": "no-store", "X-Label-Count": str(total)})


@router.post("/labels/preview")
def preview_labels(body: LabelPreviewBody, db: Session = Depends(get_db)):
    document = labels_pdf(_details(db, body.stack_ids, body.selected_specimen_ids),
                          body.width_mm)
    return Response(document, media_type="application/pdf", headers={
        "Content-Disposition": 'inline; filename="specimen-labels.pdf"', "Cache-Control": "no-store"})


class PrintRejected(Exception):
    pass


class PrintAmbiguous(PrintRejected):
    pass


def _submit_label(lp: str, body: PrintBody, request_id: str, label: dict) -> str:
    """Send one label as its own CUPS job on the fixed die-cut size; return the CUPS job ID."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix="conditioning-label-", suffix=".pdf", delete=False) as output:
            output.write(labels_pdf([label], body.width_mm))
            temporary = FilePath(output.name)
        completed = subprocess.run([
            lp, "-d", body.printer_queue, "-t", "Fabric Labels " + request_id,
            "-o", f"media=Custom.{body.width_mm:g}x{label_height_mm():g}mm",
            "-o", "print-scaling=none", "-o", "fit-to-page=false",
            "-o", "CutMedia=EndOfPage", str(temporary),   # cut after every label
        ], capture_output=True, text=True, timeout=20, check=False, env={**os.environ, "LC_ALL": "C"})
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    match = re.search(r"request id is\s+(\S+)", completed.stdout or "")
    if completed.returncode == 0 and match:
        return match.group(1)
    if completed.returncode == 0:
        raise PrintAmbiguous("CUPS returned without a recognizable job ID. Check the print queue before reprinting.")
    raise PrintRejected((completed.stderr or completed.stdout or "Printer rejected the job.").strip())


def submit_print_job(db: Session, body: PrintBody) -> dict:
    request_id = body.request_id
    number = split_request_id(request_id)[1]
    values = body.model_dump(mode="json")
    fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    # The lock serializes the claim, then the persisted 'submitting' state keeps
    # another request from sending lp again after a lost response or restart.
    key = int.from_bytes(hashlib.sha256(request_id.encode()).digest()[:8], "big", signed=True)
    with db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        existing = db.get(PrintJob, number)
        if existing:
            if existing.fingerprint != fingerprint:
                raise HTTPException(409, "This print request ID was already used for different labels or settings.")
            return _job_dict(existing)
        check_request_number(db, "print", number)
        inventory = printer_inventory()
        names = {item["name"] for item in inventory["printers"]}
        if body.printer_queue not in names:
            raise HTTPException(409, "Choose an available printer queue before printing.")
        if inventory["configured_queue"] and body.printer_queue != inventory["configured_queue"]:
            raise HTTPException(409, "This host is configured for a different printer queue.")
        stack_ids = body.stack_ids
        details = _details(db, stack_ids, body.selected_specimen_ids)
        height_mm = label_height_mm(details, body.width_mm)
        job = PrintJob(number=number, fingerprint=fingerprint, status="submitting",
                       printer_queue=body.printer_queue, stack_ids=stack_ids,
                       options={"copies": body.copies, "width_mm": body.width_mm,
                                "height_mm": height_mm, "barcode_format": "DataMatrix",
                                "selected_specimen_ids": [specimen["id"] for detail in details for specimen in detail["specimens"]],
                                "label_count": sum(len(item["specimens"]) for item in details) * body.copies})
        db.add(job)
        for detail in details:
            db.add(AuditEvent(stack_id=detail["id"], kind="label_print_requested", actor="staff",
                              specimen_ids=[item["id"] for item in detail["specimens"]],
                              payload={"print_request_id": request_id, "status": "submitting",
                                       "printer_queue": body.printer_queue, "copies": body.copies,
                                       "physical_print_confirmed": False}))
        db.flush()
    # One CUPS job per label: within a multi-page job the QL feeds extra blank tape before every label
    # after the first, so only a job's first label comes out with even margins.
    labels = [{**detail, "specimens": [specimen]} for detail in details for specimen in detail["specimens"]]
    status, error, cups_ids = "unknown", None, []
    try:
        lp = shutil.which("lp")
        if not lp:
            raise OSError("CUPS submission command is unavailable.")
        for label in labels:
            for _ in range(body.copies):
                cups_ids.append(_submit_label(lp, body, request_id, label))
        status = "submitted"
    except subprocess.TimeoutExpired:
        error = "Print submission timed out. Check the queue before creating another print job."
    except (OSError, PrintRejected) as failure:
        status = "unknown" if cups_ids or isinstance(failure, PrintAmbiguous) else "failed"
        error = str(failure)[:1000] + (f" {len(cups_ids)} label(s) were already sent; check the queue." if cups_ids else "")
    cups_job_id = ",".join(cups_ids) or None
    with db.begin():
        job = db.scalar(select(PrintJob).where(PrintJob.number == number).with_for_update())
        job.status, job.cups_job_id, job.error, job.updated_at = status, cups_job_id, error, utcnow()
        for detail in details:
            db.add(AuditEvent(stack_id=detail["id"], kind="label_print", actor="staff",
                              specimen_ids=[item["id"] for item in detail["specimens"]],
                              payload={"print_request_id": request_id, "status": status,
                                       "printer_queue": body.printer_queue, "cups_job_id": cups_job_id,
                                       "copies": body.copies, "physical_print_confirmed": False}))
        db.flush()
        return _job_dict(job)


@router.post("/print-jobs")
def create_print_job(body: PrintBody, db: Session = Depends(get_db)):
    return submit_print_job(db, body)


def _with_queue(job: PrintJob) -> dict:
    value = _job_dict(job)
    lpstat = shutil.which("lpstat")
    if job.status == "submitted" and job.cups_job_id and job.printer_queue and lpstat:
        mine = set(job.cups_job_id.split(","))
        value["waiting_cups_jobs"] = sum(1 for item in waiting_jobs(lpstat, job.printer_queue) if item in mine)
        value["message"] = ("All labels have left the Mac's queue. Check them at the printer."
                            if not value["waiting_cups_jobs"] else
                            f"{value['waiting_cups_jobs']} label(s) still waiting in the Mac's queue: the printer may be "
                            "offline, out of labels, or busy. They print when it is ready; do not reprint them.")
    return value


@router.get("/print-jobs/{request_id}")
def print_job_status(request_id: Annotated[str, Path(pattern=PRINT_REQUEST)], db: Session = Depends(get_db)):
    with db.begin():
        job = db.scalar(select(PrintJob).where(PrintJob.number == split_request_id(request_id)[1]).with_for_update())
        if job is None:
            raise HTTPException(404, "Print request not found. Retry with the same request ID.")
        if job.status == "submitting" and utcnow() - job.updated_at > timedelta(seconds=60):
            job.status = "unknown"
            job.error = "The host did not retain a final print response. Check the Mac queue and physical output before creating another job."
            job.updated_at = utcnow()
            selected = set(job.options.get("selected_specimen_ids", []))
            for detail in _details(db, job.stack_ids, list(selected) if selected else None):
                db.add(AuditEvent(stack_id=detail["id"], kind="label_print_status", actor="host",
                                  specimen_ids=[item["id"] for item in detail["specimens"]],
                                  payload={"print_request_id": request_id, "status": "unknown", "physical_print_confirmed": False}))
            db.flush()
        return _with_queue(job)
