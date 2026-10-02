import uuid

from fastapi import APIRouter, HTTPException

from app.api.deps import SessionDep, WorkspaceDep
from app.api.schemas import FlagOut, FlagUpdate
from app.db.models import Case, FlagRow
from app.services.audit_log import record

router = APIRouter()


@router.patch("/api/flags/{flag_id}", response_model=FlagOut)
def review_flag(flag_id: uuid.UUID, update: FlagUpdate, ws: WorkspaceDep, session: SessionDep) -> FlagOut:
    row = session.get(FlagRow, flag_id)
    case = session.get(Case, row.case_id) if row else None
    if row is None or case is None or case.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="flag not found")
    if row.severity == "notice":
        raise HTTPException(status_code=422, detail="notices are informational and can't be reviewed")
    reason = (update.reject_reason or "").strip()
    if update.status == "rejected" and not reason:
        raise HTTPException(status_code=422, detail="rejecting a flag needs a reason")
    before = row.status
    row.status = update.status
    row.reject_reason = reason if update.status == "rejected" else None
    record(
        session,
        ws.id,
        "flag_reviewed",
        case_id=case.id,
        detail={
            "flag_id": str(row.id),
            "rule_id": row.rule_id,
            "from": before,
            "to": row.status,
            "reason": row.reject_reason,
        },
    )
    session.commit()
    return FlagOut.model_validate(row, from_attributes=True)
