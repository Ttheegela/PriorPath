from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CENT = Decimal("0.01")


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


class Severity(StrEnum):
    ERROR = "error"
    OUTLIER = "outlier"
    LEAD = "lead"
    NOTICE = "notice"


class LineSource(StrEnum):
    STRUCTURED = "structured"
    EXTRACTED = "extracted"


class LineItem(BaseModel):
    id: str
    code: str = Field(max_length=16)
    modifiers: list[str] = Field(default_factory=list)
    units: int = Field(gt=0)
    charge: Decimal = Field(ge=0)
    date_of_service: date
    place_of_service: str | None = None
    diagnosis_codes: list[str] = Field(default_factory=list)
    source: LineSource = LineSource.STRUCTURED
    confidence: float | None = None
    field_confidence: dict[str, float] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("code is empty")
        return v

    @field_validator("charge")
    @classmethod
    def _cents(cls, v: Decimal) -> Decimal:
        return money(v)

    @field_validator("modifiers")
    @classmethod
    def _normalize_modifiers(cls, v: list[str]) -> list[str]:
        mods = [m.strip().upper() for m in v if m.strip()]
        if any(len(m) > 4 for m in mods):
            raise ValueError("modifier longer than 4 characters")
        return mods

    @field_validator("diagnosis_codes")
    @classmethod
    def _diagnosis_length(cls, v: list[str]) -> list[str]:
        if any(len(d) > 16 for d in v):
            raise ValueError("diagnosis code longer than 16 characters")
        return v

    @property
    def unit_price(self) -> Decimal:
        return self.charge / self.units


class Claim(BaseModel):
    id: str = Field(max_length=128)
    patient_pseudonym: str
    provider: str | None = Field(default=None, max_length=200)
    payer: str | None = Field(default=None, max_length=200)
    lines: list[LineItem]
    source: Literal["fhir", "pdf"] = "fhir"


class Evidence(BaseModel):
    table: str
    ref_version: str | None
    row: dict[str, str]


class Flag(BaseModel):
    id: str
    claim_id: str
    rule_id: str
    severity: Severity
    line_ids: list[str]
    evidence: Evidence
    est_overcharge: Decimal
    message: str
    explanation: str | None = None
    status: Literal["open", "accepted", "rejected"] = "open"
    reject_reason: str | None = None


def make_flag(
    claim: Claim,
    rule_id: str,
    severity: Severity,
    lines: Sequence[LineItem],
    evidence: Evidence,
    overcharge: Decimal,
    message: str,
) -> Flag:
    ids = [line.id for line in lines]
    return Flag(
        id=f"{claim.id}:{rule_id}:{'+'.join(ids)}",
        claim_id=claim.id,
        rule_id=rule_id,
        severity=severity,
        line_ids=ids,
        evidence=evidence,
        est_overcharge=money(overcharge),
        message=message,
    )
