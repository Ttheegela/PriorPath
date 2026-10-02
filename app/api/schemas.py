import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class LineOut(BaseModel):
    id: str
    code: str
    modifiers: list[str]
    units: int
    charge: Decimal
    date_of_service: date
    place_of_service: str | None


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


class UploadResult(BaseModel):
    cases: list[CaseSummary]
    errors: list[ParseErrorOut]


class FlagUpdate(BaseModel):
    status: Literal["open", "accepted", "rejected"]
    reject_reason: str | None = Field(default=None, max_length=500)


class LetterEdit(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
