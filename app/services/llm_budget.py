import uuid
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import LlmUsage

EXPLANATIONS_PER_HOUR = 20
EXTRACT_PAGES_PER_HOUR = 20
GLOBAL_EXPLANATIONS_PER_HOUR = 100  # shared across both kinds


def _cap(kind: str) -> int:
    return EXTRACT_PAGES_PER_HOUR if kind == "extract" else EXPLANATIONS_PER_HOUR


def _hour(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)


def _global_used(session: Session, hour: datetime) -> int:
    # Count each workspace/kind at most up to its own cap, so refused retries from one workspace
    # can't use up the global budget for everyone else.
    per_workspace = case(
        (LlmUsage.kind == "extract", func.least(LlmUsage.calls, EXTRACT_PAGES_PER_HOUR)),
        else_=func.least(LlmUsage.calls, EXPLANATIONS_PER_HOUR),
    )
    total = session.scalar(
        select(func.coalesce(func.sum(per_workspace), 0)).where(LlmUsage.hour_start == hour)
    )
    return int(total or 0)


def remaining(
    session: Session, workspace_id: uuid.UUID, kind: str = "explain", now: datetime | None = None
) -> int:
    """Units this workspace can still use this hour (its own pool and the shared global cap)."""
    hour = _hour(now)
    used = session.scalar(
        select(LlmUsage.calls).where(
            LlmUsage.workspace_id == workspace_id, LlmUsage.hour_start == hour, LlmUsage.kind == kind
        )
    )
    own = _cap(kind) - (used or 0)
    return max(0, min(own, GLOBAL_EXPLANATIONS_PER_HOUR - _global_used(session, hour)))


def try_consume(
    session: Session, workspace_id: uuid.UUID, kind: str = "explain", now: datetime | None = None
) -> bool:
    hour = _hour(now)
    stmt = (
        insert(LlmUsage)
        .values(workspace_id=workspace_id, hour_start=hour, kind=kind, calls=1)
        .on_conflict_do_update(
            index_elements=[LlmUsage.workspace_id, LlmUsage.hour_start, LlmUsage.kind],
            set_={"calls": LlmUsage.calls + 1},
        )
        .returning(LlmUsage.calls)
    )
    if session.execute(stmt).scalar_one() > _cap(kind):
        return False
    return _global_used(session, hour) <= GLOBAL_EXPLANATIONS_PER_HOUR
