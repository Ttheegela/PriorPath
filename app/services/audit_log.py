import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def record(
    session: Session,
    workspace_id: uuid.UUID,
    action: str,
    *,
    case_id: uuid.UUID | None = None,
    actor: str = "reviewer",
    detail: dict[str, Any] | None = None,
    ref_versions: list[str] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        workspace_id=workspace_id,
        case_id=case_id,
        actor=actor,
        action=action,
        detail=detail or {},
        ref_versions=ref_versions or [],
    )
    session.add(event)
    return event
