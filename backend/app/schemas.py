from typing import Literal
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class PrepRow(BaseModel):
    project_number: str = Field(min_length=1, max_length=120)
    material_ref: str = Field(min_length=1, max_length=200)
    test_code: str = Field(min_length=1, max_length=120)
    preconditioning_code: str = Field(min_length=1, max_length=200)
    specimen_count: int = Field(ge=1, le=500)
    metadata: dict = Field(default_factory=dict)

    @field_validator("project_number", "material_ref", "test_code", "preconditioning_code")
    @classmethod
    def clean(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank")
        return value


class PrepDraftRow(BaseModel):
    project_number: str | None = Field(default=None, max_length=120)
    material_ref: str | None = Field(default=None, max_length=200)
    test_code: str | None = Field(default=None, max_length=120)
    preconditioning_code: str | None = Field(default=None, max_length=200)
    specimen_count: int | None = Field(default=None, ge=1, le=500)
    metadata: dict = Field(default_factory=dict)


class PrepBody(BaseModel):
    rows: list[PrepDraftRow] = Field(min_length=1, max_length=1000)
    expected_rows: list[PrepDraftRow] | None = Field(default=None, min_length=1, max_length=1000)


class PrepCommitBody(BaseModel):
    expected_rows: list[PrepDraftRow] | None = Field(default=None, min_length=1, max_length=1000)


class LookupBody(BaseModel):
    code: str


class MovementBody(BaseModel):
    request_id: UUID
    station_id: str
    code: str
    direction: Literal["IN", "OUT"]
    scope: Literal["stack", "specimen", "selection"] = "stack"
    selected_specimen_ids: list[int] | None = None
    expected_version: int | None = Field(default=None, ge=0)
    acknowledge_early: bool = False


class LoginBody(BaseModel):
    username: str
    password: str


class CorrectionBody(BaseModel):
    kind: Literal["edit_session", "missed_session", "undo"]
    reason: str = Field(default="", max_length=2000)
    session_id: UUID | None = None
    specimen_ids: list[int] | None = None
    room_id: str | None = None
    entered_at: datetime | None = None
    exited_at: datetime | None = None
    event_id: UUID | None = None

    @field_validator("reason")
    @classmethod
    def reason_nonblank(cls, value):
        return value.strip()

    @field_validator("entered_at", "exited_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Timestamps must include a timezone")
        return value


class TransactionEditBody(BaseModel):
    specimen_ids: list[int] | None = None
    occurred_at: datetime | None = None
    room_id: str | None = None
    note: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=0)

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Timestamps must include a timezone")
        return value


class TransactionUndoBody(BaseModel):
    note: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=0)


class MissedMovementBody(BaseModel):
    stack_id: int
    specimen_ids: list[int] = Field(min_length=1, max_length=500)
    direction: Literal["IN", "OUT"]
    occurred_at: datetime
    room_id: str
    note: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=0)

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timestamps must include a timezone")
        return value
