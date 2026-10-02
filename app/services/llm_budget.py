import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import LlmUsage

EXPLANATIONS_PER_HOUR = 20


def try_consume(session: Session, workspace_id: uuid.UUID, now: datetime | None = None) -> bool:
    hour = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    stmt = (
        insert(LlmUsage)
        .values(workspace_id=workspace_id, hour_start=hour, calls=1)
        .on_conflict_do_update(
            index_elements=[LlmUsage.workspace_id, LlmUsage.hour_start], set_={"calls": LlmUsage.calls + 1}
        )
        .returning(LlmUsage.calls)
    )
    return session.execute(stmt).scalar_one() <= EXPLANATIONS_PER_HOUR
