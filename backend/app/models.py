from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow():
    return datetime.now(timezone.utc)


def uuid_str():
    return str(uuid4())


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    project_number: Mapped[str] = mapped_column(String(120), unique=True)


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
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
    __tablename__ = "prep_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    rows: Mapped[list] = mapped_column(JSONB)
    committed: Mapped[bool] = mapped_column(Boolean, default=False)
    stack_ids: Mapped[list] = mapped_column(JSONB, default=list)
    source: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    warnings: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    sheets: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    review_data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Stack(Base):
    __tablename__ = "stacks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), index=True)
    test_code: Mapped[str] = mapped_column(String(120), index=True)
    preconditioning_code: Mapped[str] = mapped_column(ForeignKey("treatments.code"), index=True)
    source_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    project: Mapped[Project] = relationship()
    material: Mapped[Material] = relationship()
    specimens: Mapped[list["Specimen"]] = relationship(back_populates="stack", order_by="Specimen.number")


class Specimen(Base):
    __tablename__ = "specimens"
    __table_args__ = (UniqueConstraint("stack_id", "number"), CheckConstraint("number > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    stack_id: Mapped[str] = mapped_column(ForeignKey("stacks.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    last_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stack: Mapped[Stack] = relationship(back_populates="specimens")
    sessions: Mapped[list["ConditioningSession"]] = relationship(back_populates="specimen", order_by="ConditioningSession.entered_at")


class ConditioningSession(Base):
    __tablename__ = "conditioning_sessions"
    __table_args__ = (
        CheckConstraint("exited_at IS NULL OR exited_at >= entered_at", name="valid_session_chronology"),
        Index("one_open_session_per_specimen", "specimen_id", unique=True, postgresql_where=text("exited_at IS NULL AND voided = false")),
        Index("one_conditioning_period_per_specimen", "specimen_id", unique=True, postgresql_where=text("voided = false")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    specimen_id: Mapped[str] = mapped_column(ForeignKey("specimens.id"), index=True)
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


class MovementRequest(Base):
    __tablename__ = "movement_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    stack_id: Mapped[str] = mapped_column(ForeignKey("stacks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    actor: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    specimen_ids: Mapped[list] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PrintJob(Base):
    __tablename__ = "print_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="prepared")
    printer_queue: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cups_job_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    stack_ids: Mapped[list] = mapped_column(JSONB, default=list)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
