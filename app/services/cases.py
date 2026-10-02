import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CaseSummary
from app.db.models import Case, FlagRow, Workspace
from app.models import Claim, Evidence, Flag, Severity
from app.rules.totals import case_totals
from app.services.audit_log import record


def create_cases(
    session: Session, ws: Workspace, claims: list[Claim], payer_type: str, actor: str = "reviewer"
) -> list[Case]:
    cases = []
    for claim in claims:
        case = Case(
            workspace_id=ws.id,
            source=claim.source,
            payer_type=payer_type,
            claim=claim.model_dump(mode="json"),
        )
        session.add(case)
        session.flush()
        record(
            session,
            ws.id,
            "case_uploaded",
            case_id=case.id,
            actor=actor,
            detail={"claim_id": claim.id, "lines": len(claim.lines), "payer_type": payer_type},
        )
        cases.append(case)
    return cases


def to_claim(case: Case) -> Claim:
    return Claim.model_validate(case.claim)


def case_flags(session: Session, case_id: uuid.UUID) -> list[FlagRow]:
    return list(session.scalars(select(FlagRow).where(FlagRow.case_id == case_id).order_by(FlagRow.position)))


def flag_from_row(row: FlagRow, claim_id: str) -> Flag:
    return Flag(
        id=row.flag_key,
        claim_id=claim_id,
        rule_id=row.rule_id,
        severity=Severity(row.severity),
        line_ids=row.line_ids,
        evidence=Evidence.model_validate(row.evidence),
        est_overcharge=row.est_overcharge,
        message=row.message,
        explanation=row.explanation,
        status=row.status,
        reject_reason=row.reject_reason,
    )


def summarize(case: Case, rows: list[FlagRow]) -> CaseSummary:
    claim = to_claim(case)
    live = [r for r in rows if r.status != "rejected"]
    totals = case_totals(claim, [flag_from_row(r, claim.id) for r in live])
    return CaseSummary(
        id=case.id,
        claim_id=claim.id,
        provider=claim.provider,
        payer=claim.payer,
        payer_type=case.payer_type,
        source=case.source,
        status=case.status,
        line_count=len(claim.lines),
        error_count=sum(r.severity == Severity.ERROR.value for r in live),
        est_overcharge=totals.errors,
        outlier_amount=totals.outliers,
        created_at=case.created_at,
    )


def get_case_or_404(session: Session, ws: Workspace, case_id: uuid.UUID) -> Case:
    case = session.get(Case, case_id)
    if case is None or case.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="case not found")
    return case
