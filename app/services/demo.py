import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Case, Workspace
from app.ingest.fhir import parse_fhir
from app.llm.cache import cached_explanation
from app.reference.base import InMemoryReference
from app.services.audit_log import record
from app.services.audit_run import run_audit
from app.services.cases import create_cases, flag_from_row

DEMO_CASES = Path(__file__).resolve().parent.parent.parent / "data" / "demo" / "cases.json"
WORKSPACE_TTL = timedelta(hours=24)


def demo_enabled() -> bool:
    return os.environ.get("PRIORPATH_DEMO", "1") != "0"


def seed_demo(session: Session, ws: Workspace, ref: InMemoryReference) -> int:
    if not DEMO_CASES.exists():
        return 0
    result = parse_fhir(json.loads(DEMO_CASES.read_text()))
    cases = create_cases(session, ws, result.claims, payer_type="medicare", actor="system")
    for case in cases:
        for row in run_audit(session, case, ref, actor="system"):
            text = cached_explanation(flag_from_row(row, str(case.claim["id"])))
            if text:
                row.explanation, row.explanation_status = text, "ready"
    return len(cases)


def reset_demo(session: Session, ws: Workspace, ref: InMemoryReference) -> int:
    session.execute(delete(Case).where(Case.workspace_id == ws.id))
    count = seed_demo(session, ws, ref)
    record(session, ws.id, "demo_reset", detail={"cases": count})
    return count


def cleanup_old_workspaces(session: Session, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - WORKSPACE_TTL
    deleted = session.execute(delete(Workspace).where(Workspace.created_at < cutoff)).rowcount  # type: ignore[attr-defined]
    session.commit()
    return int(deleted or 0)
