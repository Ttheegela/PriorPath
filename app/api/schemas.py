import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class LineOut(BaseModel):
    id: str
    code: str
    modifiers: list[str]
    units: int
    charge: Decimal
    date_of_service: date
    place_of_service: str | None
    source: str
    confidence: float | None
    field_confidence: dict[str, float]


class FlagOut(BaseModel):
    id: uuid.UUID
    rule_id: str
    severity: str
    line_ids: list[str]
    evidence: dict[str, Any]
    est_overcharge: Decimal
    message: str
    explanation: str | None
    explanation_status: str
    status: str
    reject_reason: str | None


class LetterOut(BaseModel):
    id: uuid.UUID
    status: str
    body: str
    flag_ids: list[str]
    created_at: datetime
    approved_at: datetime | None


class CaseSummary(BaseModel):
    id: uuid.UUID
    claim_id: str
    provider: str | None
    payer: str | None
    payer_type: str
    source: str
    status: str
    line_count: int
    page_count: int | None
    error_count: int
    est_overcharge: Decimal
    outlier_amount: Decimal
    created_at: datetime


class CaseDetail(CaseSummary):
    lines: list[LineOut]
    flags: list[FlagOut]
    letter: LetterOut | None


class ParseErrorOut(BaseModel):
    path: str
    message: str


class RedactionSummary(BaseModel):
    pages_redacted: int
    pages_not_redactable: int
    pages_partially_redacted: int  # a match ran off the page edge; masked up to the edge
    entities: dict[str, int]


class UploadResult(BaseModel):
    cases: list[CaseSummary]
    errors: list[ParseErrorOut]
    redaction: RedactionSummary | None = None  # PDF uploads only


class FlagUpdate(BaseModel):
    status: Literal["open", "accepted", "rejected"]
    reject_reason: str | None = Field(default=None, max_length=500)


class LetterEdit(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)

    @field_validator("body")
    @classmethod
    def _no_control_chars(cls, v: str) -> str:
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", v):
            raise ValueError("control characters are not allowed")
        return v


class AuditEventOut(BaseModel):
    id: int
    case_id: uuid.UUID | None
    actor: str
    action: str
    detail: dict[str, Any]
    ref_versions: list[str]
    at: datetime


class LineEdit(BaseModel):
    """Loosely typed here; the endpoint validates each edit through LineItem."""

    id: str = Field(min_length=1, max_length=64)
    code: str
    modifiers: list[str] = Field(default_factory=list)
    units: int
    charge: Decimal
    date_of_service: date
    place_of_service: str | None = None


class LinesEdit(BaseModel):
    lines: list[LineEdit] = Field(min_length=1, max_length=200)
