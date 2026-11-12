"""Database schema. Keep docs/DATABASE.md in step."""
from datetime import datetime, timezone

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Identity, Index, Integer, String,
                        Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


def serial():
    return mapped_column(BigInteger, Identity(), primary_key=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = serial()
    project_number: Mapped[str] = mapped_column(String(120), unique=True)


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[int] = serial()
    material_ref: Mapped[str] = mapped_column(String(200), unique=True)


class Treatment(Base):
    __tablename__ = "treatments"
    code: Mapped[str] = mapped_column(String(200), primary_key=True)


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    temperature_c: Mapped[float] = mapped_column(Float)
    relative_humidity: Mapped[float] = mapped_column(Float)
    temperature_tolerance_c: Mapped[float] = mapped_column(Float)
    humidity_tolerance: Mapped[float] = mapped_column(Float)
    required_hours: Mapped[float] = mapped_column(Float, default=24)


class Station(Base):
    __tablename__ = "stations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"))
    room: Mapped[Room] = relationship()


class PrepDraft(Base):
    """A prep sheet under review, laid out like the sheet: one header per worksheet, one row per sheet row."""
    __tablename__ = "prep_drafts"
    id: Mapped[int] = serial()
    headers: Mapped[dict] = mapped_column(JSONB, default=dict)   # worksheet name -> project/LabWare/standard/regime
    rows: Mapped[list] = mapped_column(JSONB)                     # one entry per prep sheet row
    revision: Mapped[int] = mapped_column(Integer, default=0)     # bumps on every save; stale saves are refused
    committed: Mapped[bool] = mapped_column(Boolean, default=False)
    stack_ids: Mapped[list] = mapped_column(JSONB, default=list)
    source: Mapped[dict] = mapped_column(JSONB, default=dict)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    sheets: Mapped[list] = mapped_column(JSONB, default=list)     # worksheet inventory of the uploaded file
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Stack(Base):
    __tablename__ = "stacks"
    id: Mapped[int] = serial()
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    test_code: Mapped[str] = mapped_column(String(120), index=True)
    preconditioning_code: Mapped[str] = mapped_column(ForeignKey("treatments.code"), index=True)
    source_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    project: Mapped[Project] = relationship()
    material: Mapped[Material] = relationship()
    specimens: Mapped[list["Specimen"]] = relationship(back_populates="stack", order_by="Specimen.number")


class Specimen(Base):
    __tablename__ = "specimens"
    __table_args__ = (UniqueConstraint("stack_id", "number"), CheckConstraint("number > 0"))
    # The id is the label: printed and scanned with a check digit (see barcodes.label_code).
    # number is the piece's position in its stack (01, 02, ...). Neither is ever reused.
    id: Mapped[int] = serial()
    stack_id: Mapped[int] = mapped_column(ForeignKey("stacks.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    last_event_id: Mapped[int | None] = mapped_column(ForeignKey("audit_events.id"), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # taken off the stack
    stack: Mapped[Stack] = relationship(back_populates="specimens")
    sessions: Mapped[list["ConditioningSession"]] = relationship(back_populates="specimen", order_by="ConditioningSession.entered_at")


class ConditioningSession(Base):
    __tablename__ = "conditioning_sessions"
    __table_args__ = (
        CheckConstraint("exited_at IS NULL OR exited_at >= entered_at", name="valid_session_chronology"),
        Index("one_open_session_per_specimen", "specimen_id", unique=True, postgresql_where=text("exited_at IS NULL AND voided = false")),
        Index("one_conditioning_period_per_specimen", "specimen_id", unique=True, postgresql_where=text("voided = false")),
        Index("open_sessions_by_room", "room_id", postgresql_where=text("exited_at IS NULL AND voided = false")),  # inventory
    )
    id: Mapped[int] = serial()
    specimen_id: Mapped[int] = mapped_column(ForeignKey("specimens.id"), index=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"))
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(24), default="scan")
    corrected: Mapped[bool] = mapped_column(Boolean, default=False)
    incomplete: Mapped[bool] = mapped_column(Boolean, default=False)
    early_exit_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    voided: Mapped[bool] = mapped_column(Boolean, default=False)
    specimen: Mapped[Specimen] = relationship(back_populates="sessions")
    room: Mapped[Room] = relationship()


class RequestCounter(Base):
    """Retry IDs are '<client>-<n>'. The server hands out n in blocks so a client never reuses one,
    even after its browser storage is cleared. Clients are station ids and 'print'."""
    __tablename__ = "request_counters"
    client: Mapped[str] = mapped_column(String(36), primary_key=True)
    last_issued: Mapped[int] = mapped_column(BigInteger, default=0)


class MovementRequest(Base):
    """The stored result of each scan request, so a retry of the same request returns it instead of moving twice."""
    __tablename__ = "movement_requests"
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"), primary_key=True)
    number: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("events_by_room", "room_id", "kind", "recorded_at"),                    # kiosk activity
        Index("events_by_target", text("((payload ->> 'event_id')::bigint)")),       # undo / modification of an event
        Index("events_by_specimen", "specimen_ids", postgresql_using="gin"),         # specimen history
    )
    id: Mapped[int] = serial()
    stack_id: Mapped[int] = mapped_column(ForeignKey("stacks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    actor: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    specimen_ids: Mapped[list] = mapped_column(JSONB)
    room_id: Mapped[str | None] = mapped_column(String(36), nullable=True)   # room the movement or correction is in
    payload: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PrintJob(Base):
    __tablename__ = "print_jobs"
    number: Mapped[int] = mapped_column(BigInteger, primary_key=True)   # request 'print-<number>'
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="prepared")
    printer_queue: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cups_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)   # one CUPS id per label, comma-separated
    stack_ids: Mapped[list] = mapped_column(JSONB, default=list)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
