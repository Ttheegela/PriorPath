from collections import Counter
from typing import cast

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Case, FlagRow, Letter
from app.models import Severity
from app.reference.base import InMemoryReference
from app.rules import PayerType, RuleConfig, run_rules
from app.services.audit_log import record
from app.services.cases import to_claim

EXPLAINED = {Severity.ERROR, Severity.OUTLIER, Severity.LEAD}


def run_audit(session: Session, case: Case, ref: InMemoryReference, actor: str = "reviewer") -> list[FlagRow]:
    claim = to_claim(case)
    reviewed = session.scalar(
        select(func.count()).select_from(FlagRow).where(FlagRow.case_id == case.id, FlagRow.status != "open")
    )
    drafts = session.scalar(
        select(func.count()).select_from(Letter).where(Letter.case_id == case.id, Letter.status == "draft")
    )
    session.execute(delete(FlagRow).where(FlagRow.case_id == case.id))
    session.execute(delete(Letter).where(Letter.case_id == case.id))
    flags = run_rules(claim, ref, RuleConfig(payer_type=cast(PayerType, case.payer_type)))
    rows = [
        FlagRow(
            case_id=case.id,
            position=i,
            flag_key=f.id,
            rule_id=f.rule_id,
            severity=f.severity.value,
            line_ids=f.line_ids,
            evidence=f.evidence.model_dump(mode="json"),
            est_overcharge=f.est_overcharge,
            message=f.message,
            explanation_status="pending" if f.severity in EXPLAINED else "none",
        )
        for i, f in enumerate(flags)
    ]
    session.add_all(rows)
    case.status = "needs_review"
    record(
        session,
        case.workspace_id,
        "audit_run",
        case_id=case.id,
        actor=actor,
        detail={
            "flags": len(rows),
            "by_rule": dict(Counter(f.rule_id for f in flags)),
            "discarded_reviewed_flags": reviewed,
            "discarded_draft_letters": drafts,
        },
        ref_versions=[v.ref_version for v in ref.versions],
    )
    session.flush()
    return rows
