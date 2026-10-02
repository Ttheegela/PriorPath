import json
import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Case, FlagRow, Workspace
from app.ingest.fhir import parse_fhir
from app.ingest.pdf import page_count
from app.llm.cache import cached_explanation
from app.models import Claim, LineItem
from app.reference.base import InMemoryReference
from app.services.audit_log import record
from app.services.audit_run import run_audit
from app.services.cases import case_flags, create_cases, flag_from_row
from app.services.pdf_cases import store_pdf_case

DEMO_CASES = Path(__file__).resolve().parent.parent.parent / "data" / "demo" / "cases.json"
DEMO_BILLS = DEMO_CASES.parent / "bills"
DEMO_EXTRACTIONS = DEMO_CASES.parent / "pdf_extractions.json"
WORKSPACE_TTL = timedelta(hours=24)
log = logging.getLogger(__name__)


def demo_enabled() -> bool:
    return os.environ.get("PRIORPATH_DEMO", "1") != "0"


@lru_cache(maxsize=2)
def _load_demo(path: Path) -> tuple[Claim, ...]:
    if not path.exists():
        return ()
    return tuple(parse_fhir(json.loads(path.read_text())).claims)


def _apply_cached_explanations(case: Case, rows: Iterable[FlagRow]) -> None:
    for row in rows:
        text = cached_explanation(flag_from_row(row, str(case.claim["id"])))
        if text:
            row.explanation, row.explanation_status = text, "ready"


def _seed_pdf_cases(session: Session, ws: Workspace, ref: InMemoryReference) -> list[Case]:
    if not DEMO_EXTRACTIONS.exists():
        log.info("no recorded PDF extractions at %s; seeding FHIR demo cases only", DEMO_EXTRACTIONS)
        return []
    cases = []
    for claim_id, rec in json.loads(DEMO_EXTRACTIONS.read_text()).items():
        bill = DEMO_BILLS / f"{claim_id}.pdf"
        if not bill.exists():
            log.warning("demo bill %s is missing; skipping it", bill)
            continue
        data = bill.read_bytes()
        lines = [LineItem.model_validate(ln) for ln in rec["lines"]]
        case = store_pdf_case(
            session,
            ws,
            data,
            page_count(data),
            claim_id,
            rec.get("provider"),
            rec.get("payer"),
            lines,
            "medicare",
            ref,
            actor="system",
        )
        _apply_cached_explanations(case, case_flags(session, case.id))  # none while awaiting line review
        cases.append(case)
    return cases


def seed_demo(session: Session, ws: Workspace, ref: InMemoryReference) -> int:
    claims = [c.model_copy(deep=True) for c in _load_demo(DEMO_CASES)]
    cases = create_cases(session, ws, claims, payer_type="medicare", actor="system") if claims else []
    for case in cases:
        _apply_cached_explanations(case, run_audit(session, case, ref, actor="system"))
    return len(cases) + len(_seed_pdf_cases(session, ws, ref))


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
