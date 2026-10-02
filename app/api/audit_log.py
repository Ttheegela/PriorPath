import uuid

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, WorkspaceDep
from app.api.schemas import AuditEventOut
from app.db.models import AuditEvent
from app.services.cases import get_case_or_404

AUDIT_LOG_LIMIT = 200

router = APIRouter()


def _events(
    session: Session, workspace_id: uuid.UUID, case_id: uuid.UUID | None = None
) -> list[AuditEventOut]:
    query = select(AuditEvent).where(AuditEvent.workspace_id == workspace_id)
    if case_id is not None:
        query = query.where(AuditEvent.case_id == case_id)
    rows = session.scalars(query.order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(AUDIT_LOG_LIMIT))
    return [AuditEventOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/api/audit-log", response_model=list[AuditEventOut])
def workspace_audit_log(ws: WorkspaceDep, session: SessionDep) -> list[AuditEventOut]:
    return _events(session, ws.id)


@router.get("/api/cases/{case_id}/audit-log", response_model=list[AuditEventOut])
def case_audit_log(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep) -> list[AuditEventOut]:
    get_case_or_404(session, ws, case_id)
    return _events(session, ws.id, case_id)
