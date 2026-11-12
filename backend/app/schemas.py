from typing import Literal
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class PrepRow(BaseModel):
    """One stack ready to register: what a prep sheet row resolves to for one sample."""
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


class PrepHeader(BaseModel):
    """The values a prep sheet states once, above its table. Entered once, used for every row."""
    project_number: str = Field(default="", max_length=120)   # Oracle project; LabWare # when blank
    labware_number: str = Field(default="", max_length=120)
    standard: str = Field(default="", max_length=200)
    regime: str = Field(default="", max_length=200)


class PrepSheetRow(BaseModel):
    """One prep sheet row, column for column. A blank test means 'same as the row above', as on the sheet."""
    sheet: str = Field(default="Preparation", min_length=1, max_length=120)
    test: str = Field(default="", max_length=120)
    tested_count: str = Field(default="", max_length=60)        # Total Number of Specimens to be Tested
    conditions: str = Field(default="", max_length=200)         # the treatment, e.g. AR, 5L, 5L->CH
    conditioning: str = Field(default="", max_length=120)       # Specimens per sample for Conditioning, e.g. '5 Warp, 5 Fill'
    sample: str = Field(default="", max_length=2000)            # Sample Number; several, comma-separated, make one stack each
    sample_type: str = Field(default="", max_length=200)
    metadata: dict = Field(default_factory=dict)


class PrepBody(BaseModel):
    headers: dict[str, PrepHeader] = Field(default_factory=dict)
    rows: list[PrepSheetRow] = Field(min_length=1, max_length=1000)
    expected_revision: int | None = Field(default=None, ge=0)


class PrepCommitBody(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0)


class StackEditBody(BaseModel):
    project_number: str | None = Field(default=None, min_length=1, max_length=120)
    material_ref: str | None = Field(default=None, min_length=1, max_length=200)
    test_code: str | None = Field(default=None, min_length=1, max_length=120)
    preconditioning_code: str | None = Field(default=None, min_length=1, max_length=200)
    note: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=0)

    @field_validator("project_number", "material_ref", "test_code", "preconditioning_code")
    @classmethod
    def clean(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Must not be blank")
        return value


class AddSpecimensBody(BaseModel):
    count: int = Field(ge=1, le=500)
    note: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=0)


class RemoveSpecimensBody(BaseModel):
    specimen_ids: list[int] = Field(min_length=1, max_length=500)
    note: str = Field(default="", max_length=2000)
    expected_version: int | None = Field(default=None, ge=0)


class RequestIdsBody(BaseModel):
    client: str = Field(min_length=1, max_length=36)
    count: int = Field(default=50, ge=1, le=500)


REQUEST_ID = r"^[a-z0-9][a-z0-9-]{0,35}-[1-9][0-9]{0,17}$"


def split_request_id(value: str) -> tuple[str, int]:
    """'shared-door-17' -> ('shared-door', 17)."""
    client, number = value.rsplit("-", 1)
    return client, int(number)


class LookupBody(BaseModel):
    code: str


class MovementBody(BaseModel):
    request_id: str = Field(pattern=REQUEST_ID)   # '<station id>-<n>', n reserved from /api/request-ids
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
    session_id: int | None = None
    specimen_ids: list[int] | None = None
    room_id: str | None = None
    entered_at: datetime | None = None
    exited_at: datetime | None = None
    event_id: int | None = None

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
