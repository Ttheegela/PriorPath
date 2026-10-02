import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = _created_at()


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    source: Mapped[str] = mapped_column(String(8))
    payer_type: Mapped[str] = mapped_column(String(16))
    claim: Mapped[dict[str, Any]] = mapped_column(JSONB)  # Claim.model_dump(mode="json")
    created_at: Mapped[datetime] = _created_at()


class FlagRow(Base):
    __tablename__ = "flags"
    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    flag_key: Mapped[str] = mapped_column(String(300))  # the engine's Flag.id
    rule_id: Mapped[str] = mapped_column(String(8))
    severity: Mapped[str] = mapped_column(String(16))
    line_ids: Mapped[list[str]] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    est_overcharge: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    message: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text, default=None)
    explanation_status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending|ready|unavailable|none
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|accepted|rejected
    reject_reason: Mapped[str | None] = mapped_column(Text, default=None)


class Letter(Base):
    __tablename__ = "letters"
    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    flag_ids: Mapped[list[str]] = mapped_column(JSONB)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|approved
    created_at: Mapped[datetime] = _created_at()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True, default=None
    )
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ref_versions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    at: Mapped[datetime] = _created_at()


class LlmUsage(Base):
    __tablename__ = "llm_usage"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    hour_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
