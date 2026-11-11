"""Printer-neutral labels and audit exports from the authoritative database."""
from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .barcodes import datamatrix_image, label_code
from .models import AuditEvent, Stack
from .services import stack_detail

router = APIRouter(prefix="/api", tags=["Labels and reports"])
MM = 72 / 25.4
# Brother DK-1209 die-cut labels: 62 mm wide, 29 mm long. The QL cannot print within 3 mm of a label's leading
# and trailing edge (1.5 mm at the sides), so every label keeps a 3 mm margin on all four sides.
LABEL_WIDTH_MM, LABEL_HEIGHT_MM = 62, 29
SIDE = 3 * MM
CODE = 19.4 * MM             # Data Matrix side, quiet zone included
GAP = 2.5 * MM               # between code and text
FONT_SIZE, MIN_FONT_SIZE = 7, 4   # points; long text shrinks toward the minimum to stay on the fixed label
LEADING_RATIO = 8.5 / 7
LOCAL_TZ = ZoneInfo("America/New_York")


def _stack(db: Session, stack_id: int) -> Stack:
    stack = db.get(Stack, stack_id)
    if stack is None:
        raise HTTPException(404, "Stack not found")
    return stack


def _details(db: Session, project_number: str | None) -> list[dict]:
    stacks = db.scalars(select(Stack).order_by(Stack.created_at, Stack.id)).all()
    details = [stack_detail(db, item) for item in stacks]
    return [item for item in details if project_number is None or item["project_number"] == project_number]


def _safe_csv(value) -> str:
    """Prevent untrusted project/material text from becoming spreadsheet formulas."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r")):
        return "'" + text
    return text


def _csv_response(headers: list[str], rows: list[dict], filename: str) -> Response:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _safe_csv(row.get(key)) for key in headers})
    return Response("\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store",
    })


def _label_rows(detail: dict) -> list[dict]:
    base = {key: detail.get(key) for key in (
        "project_number", "material_ref", "test_code", "preconditioning_code", "is_demo"
    )}
    base.update(stack_id=detail["id"], specimen_count=detail.get("specimen_count", len(detail["specimens"])))
    return [{**base, "barcode_payload": label_code(specimen["id"]), "barcode_format": "DataMatrix",
             "specimen_id": specimen["id"], "specimen_number": specimen["number"]}
            for specimen in detail["specimens"]]


def _font() -> str:
    if "TrackerSans" in pdfmetrics.getRegisteredFontNames():
        return "TrackerSans"
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont("TrackerSans", path))
            return "TrackerSans"
    return "Helvetica"


def _text_lines(row: dict, font: str, width_mm: float, size: float = FONT_SIZE) -> list[str]:
    """Every field in full, wrapped to the text column instead of cut off."""
    fields = [row["project_number"], row["material_ref"], row["test_code"], row["preconditioning_code"],
              f'Piece {row["specimen_number"]} / {row["specimen_count"]}',
              ("DEMO " if row["is_demo"] else "") + "No. " + row["barcode_payload"]]
    width = width_mm * MM - 2 * SIDE - CODE - GAP
    lines = []
    for field in fields:
        for line in simpleSplit(" ".join(str(field).split()), font, size, width) or [""]:
            while pdfmetrics.stringWidth(line, font, size) > width:   # a word longer than the column
                cut = max(n for n in range(1, len(line) + 1) if pdfmetrics.stringWidth(line[:n], font, size) <= width)
                lines.append(line[:cut])
                line = line[cut:]
            lines.append(line)
    return lines


def _text_height(lines: list[str], size: float = FONT_SIZE) -> float:
    return (len(lines) - 1) * size * LEADING_RATIO + 1.05 * size   # first ascender to last descender


def _fitted_text(row: dict, font: str, width_mm: float) -> tuple[list[str], float]:
    """The largest font size at which every field fits the fixed label height; (lines, size)."""
    available = LABEL_HEIGHT_MM * MM - 2 * SIDE
    size = FONT_SIZE
    while True:
        lines = _text_lines(row, font, width_mm, size)
        if _text_height(lines, size) <= available or size <= MIN_FONT_SIZE:
            return lines, size
        size -= .5


def label_height_mm(details: list[dict] | None = None, width_mm: float = LABEL_WIDTH_MM) -> float:
    """Die-cut stock: every label is the same length whatever its text."""
    return LABEL_HEIGHT_MM


def labels_pdf(detail: dict | list[dict], width_mm: float = LABEL_WIDTH_MM) -> bytes:
    details = detail if isinstance(detail, list) else [detail]
    width, height = width_mm * MM, LABEL_HEIGHT_MM * MM
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(width, height))
    pdf.setTitle("Fabric Conditioning Tracker - labels")
    font = _font()
    for row in (row for item in details for row in _label_rows(item)):
        image = datamatrix_image(row["barcode_payload"]).convert("RGB")
        pdf.drawImage(ImageReader(image), SIDE, (height - CODE) / 2, width=CODE, height=CODE,
                      preserveAspectRatio=True, mask="auto")
        lines, size = _fitted_text(row, font, width_mm)
        y = (height + _text_height(lines, size)) / 2 - .85 * size   # first baseline of the vertically centred block
        pdf.setFont(font, size)
        for line in lines:
            pdf.drawString(SIDE + CODE + GAP, y, line)
            y -= size * LEADING_RATIO
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


@router.get("/stacks/{stack_id}/labels.pdf")
def label_pdf(stack_id: int, width_mm: float = Query(62, ge=40, le=150), db: Session = Depends(get_db)):
    detail = stack_detail(db, _stack(db, stack_id))
    return Response(labels_pdf(detail, width_mm), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="labels-{stack_id}.pdf"', "Cache-Control": "no-store"})


@router.get("/stacks/{stack_id}/labels.csv")
def label_csv(stack_id: int, db: Session = Depends(get_db)):
    detail = stack_detail(db, _stack(db, stack_id))
    headers = ["barcode_format", "barcode_payload", "stack_id", "specimen_id", "project_number", "material_ref",
               "test_code", "preconditioning_code", "specimen_number", "specimen_count", "is_demo"]
    return _csv_response(headers, _label_rows(detail), f"labels-{stack_id}.csv")


def session_rows(details: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = []
    for detail in details:
        for specimen in detail["specimens"]:
            for session in specimen["sessions"]:
                enter = _parse_date(session["entered_at"])
                exit_at = _parse_date(session.get("exited_at"))
                duration = max(0, ((exit_at or now) - enter).total_seconds())
                rows.append({
                    "project_number": detail["project_number"], "material_ref": detail["material_ref"],
                    "test_code": detail["test_code"], "preconditioning_code": detail["preconditioning_code"],
                    "stack_id": detail["id"], "specimen_id": specimen["id"], "specimen_number": specimen["number"],
                    "session_id": session["id"], "room_id": session["room_id"], "entered_at_utc": session["entered_at"],
                    "exited_at_utc": session.get("exited_at"), "recorded_at_utc": session.get("recorded_at"),
                    "duration_hours": f"{duration / 3600:.4f}",
                    "conditioning_status": "COMPLETE" if duration >= session.get("required_seconds", 86400) else "INCOMPLETE",
                    "source": session["source"], "corrected": session["corrected"],
                    "early_exit_acknowledged": session.get("early_exit_acknowledged", False), "is_demo": detail["is_demo"],
                })
    return rows


SESSION_HEADERS = ["project_number", "material_ref", "test_code", "preconditioning_code", "stack_id", "specimen_id",
                   "specimen_number", "session_id", "room_id", "entered_at_utc", "exited_at_utc", "recorded_at_utc",
                   "duration_hours", "conditioning_status", "source", "corrected", "early_exit_acknowledged", "is_demo"]


def _parse_date(value) -> datetime | None:
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _event_dict(event: AuditEvent) -> dict:
    # Event payload preserves original observations, before/after values and reasons.
    return {column.name: getattr(event, column.name) for column in event.__table__.columns}


def history_rows(db: Session, details: list[dict]) -> list[dict]:
    lookup = {item["id"]: item for item in details}
    if not lookup:
        return []
    events = db.scalars(select(AuditEvent).where(AuditEvent.stack_id.in_(lookup)).order_by(AuditEvent.recorded_at, AuditEvent.id)).all()
    rows = []
    for event in events:
        data = _event_dict(event)
        detail = lookup[event.stack_id]
        rows.append({"project_number": detail["project_number"], "material_ref": detail["material_ref"],
                     "is_demo": detail["is_demo"], **data})
    return rows


@router.get("/exports/sessions.csv")
def export_sessions(project_number: str | None = None, db: Session = Depends(get_db)):
    return _csv_response(SESSION_HEADERS, session_rows(_details(db, project_number)), "conditioning-sessions.csv")


@router.get("/exports/history.csv")
def export_history(project_number: str | None = None, db: Session = Depends(get_db)):
    rows = history_rows(db, _details(db, project_number))
    headers = ["project_number", "material_ref", "is_demo"] + [column.name for column in AuditEvent.__table__.columns]
    return _csv_response(headers, rows, "audit-history.csv")


def _local(value) -> str:
    parsed = _parse_date(value)
    return parsed.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z") if parsed else "-"


def _audit_article(event: dict, detail: dict) -> str:
    """Make the printable history readable; the CSV retains the complete raw payload."""
    esc = lambda value: html.escape(str(value if value is not None else ""))
    payload = event.get("payload") or {}
    numbers = {p["id"]: p["number"] for p in detail["specimens"]}
    specimen_ids = sorted(event["specimen_ids"], key=lambda identifier: numbers.get(identifier, 0))
    affected = ", ".join(f'#{numbers.get(identifier, identifier)}' for identifier in specimen_ids)
    heading = f'{event["kind"].replace("_", " ").title()} {event.get("direction") or ""}'
    parts = [f'<article><h4>{esc(heading)} · {esc(_local(event["recorded_at"]))}</h4>',
             f'<p>Actor: {esc(event["actor"])} · Affected specimens: {esc(affected)}</p>',
             f'<p class="identity">Event {esc(event["id"])}</p>']
    if event.get("reason"):
        parts.append(f'<p><b>Reason:</b> {esc(event["reason"])}</p>')
    if payload.get("correction_kind"):
        parts.append(f'<p>Correction type: {esc(payload["correction_kind"].replace("_", " "))}</p>')
    if payload.get("event_id"):
        parts.append(f'<p class="identity">Reverses movement event {esc(payload["event_id"])}</p>')
    if payload.get("room_id"):
        parts.append(f'<p>Room: {esc(payload["room_id"])}</p>')
    if payload.get("early_exit_acknowledged"):
        parts.append('<p><b>Incomplete conditioning: early exit acknowledged.</b></p>')
    before = payload.get("before") or {}
    after = payload.get("after") or {}
    changes = []
    for specimen_id in specimen_ids:
        previous = {s["id"]: s for s in before.get(str(specimen_id), [])}
        current = {s["id"]: s for s in after.get(str(specimen_id), [])}
        for session_id in sorted(set(previous) | set(current)):
            old, new = previous.get(session_id), current.get(session_id)
            if old == new:
                continue
            def describe(session):
                if session is None:
                    return "No session"
                state = "Voided by undo" if session.get("voided") else "Closed" if session.get("exited_at") else "Open"
                return f'{esc(_local(session["entered_at"]))}<br>→ {esc(_local(session.get("exited_at")))}<br>{esc(session["room_id"])} · {esc(state)}'
            source = (new or old).get("source", "")
            changes.append(f'<tr><td>#{esc(numbers.get(specimen_id, specimen_id))}</td><td>{describe(old)}</td><td>{describe(new)}</td><td>{esc(source)}<small>Session {esc(session_id)}</small></td></tr>')
    if changes:
        parts.append('<table class="audit-changes"><thead><tr><th>Piece</th><th>Previous record</th><th>Recorded result</th><th>Source / session identity</th></tr></thead><tbody>' + "".join(changes) + '</tbody></table>')
    elif event["kind"] == "registration":
        parts.append('<p>Registered the fixed stack and issued permanent specimen identities.</p>')
    parts.append('</article>')
    return "".join(parts)


@router.get("/exports/report", response_class=HTMLResponse)
def export_report(project_number: str | None = None, db: Session = Depends(get_db)):
    details = _details(db, project_number)
    sessions = session_rows(details)
    events = history_rows(db, details)
    esc = lambda value: html.escape(str(value if value is not None else ""))
    body = []
    for detail in details:
        marker = " <strong class='demo'>DEMO DATA</strong>" if detail["is_demo"] else ""
        body.append(f'<section><h2>Project {esc(detail["project_number"])}{marker}</h2><p>{esc(detail["material_ref"])} · {esc(detail["test_code"])} · {esc(detail["preconditioning_code"])}</p><p class="identity">Stack {esc(detail["id"])} · {len(detail["specimens"])} specimens</p>')
        body.append('<table><thead><tr><th>Piece</th><th>Room</th><th>Entry</th><th>Exit</th><th>Hours</th><th>Conditioning / source</th></tr></thead><tbody>')
        rows = [row for row in sessions if row["stack_id"] == detail["id"]]
        if not rows:
            body.append('<tr><td colspan="6">No recorded conditioning sessions.</td></tr>')
        for row in rows:
            flag = " · corrected" if row["corrected"] else ""
            ack = " · early exit acknowledged" if row["early_exit_acknowledged"] else ""
            body.append(f'<tr><td>#{row["specimen_number"]}<small>{esc(row["specimen_id"])}</small></td><td>{esc(row["room_id"])}</td><td>{esc(_local(row["entered_at_utc"]))}</td><td>{esc(_local(row["exited_at_utc"]))}</td><td>{float(row["duration_hours"]):.2f}</td><td>{esc(row["conditioning_status"])} / {esc(row["source"])}{flag}{ack}</td></tr>')
        body.append('</tbody></table><h3>Audit history</h3>')
        for event in [item for item in events if item["stack_id"] == detail["id"]]:
            body.append(_audit_article(event, detail))
        body.append('</section>')
    if not details:
        body.append('<p>No stacks match this project.</p>')
    document = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Conditioning audit report</title><style>
      body{font:13px Arial,sans-serif;color:#182332;margin:32px;max-width:1100px}h1{font-size:24px}h2{font-size:18px;margin-top:28px}h3{font-size:15px}h4{font-size:13px;margin-bottom:6px}.note{color:#536273}.identity,small{font-size:9px;color:#536273;overflow-wrap:anywhere}small{display:block;max-width:160px}.demo{color:#975f00;font-size:12px}table{width:100%;border-collapse:collapse}th,td{padding:8px;border:1px solid #d5dbe0;text-align:left;vertical-align:top}th{background:#f1f4f6}article{margin:18px 0}button{padding:10px 18px;cursor:pointer}@media print{body{margin:0;font-size:10px}button{display:none}thead{display:table-header-group}tr{break-inside:avoid}h2,h3,h4{break-after:avoid}article>p{break-after:avoid}.audit-changes td{padding:5px}}@page{size:A4 landscape;margin:14mm}
      </style></head><body><button onclick="window.print()">Print / save as PDF</button><h1>Fabric conditioning audit report</h1>'''
    document += f'<p>Generated {esc(_local(datetime.now(timezone.utc)))}. All displayed times use America/New_York; CSV timestamps use UTC.</p>'
    document += '<p class="note">Reports reflect recorded residence. Manual corrections show claimed effective times separately from their recording time in the audit history. Environmental measurements are maintained separately.</p>'
    return HTMLResponse(document + "".join(body) + "</body></html>", headers={"Cache-Control": "no-store"})
