import hashlib

from sqlalchemy.orm import Session

from app.db.models import Case, CaseDocument, Workspace
from app.ingest.fhir import pseudonym
from app.ingest.pdf import pdf_page_images
from app.llm.extract import extract_page, merge, needs_review
from app.llm.vision import VisionClient
from app.models import Claim
from app.reference.base import InMemoryReference
from app.services import llm_budget
from app.services.audit_run import run_audit
from app.services.cases import create_cases

MAX_ERROR_CHARS = 160


class OverBudget(Exception):
    pass


def create_pdf_case(
    session: Session,
    ws: Workspace,
    data: bytes,
    payer_type: str,
    ref: InMemoryReference,
    vision: VisionClient,
) -> tuple[Case, list[tuple[str, str]]]:
    pages = pdf_page_images(data)  # PdfError -> caller maps to 422
    results = []
    for no, png in enumerate(pages, start=1):
        if not llm_budget.try_consume(session, ws.id, "extract"):
            session.rollback()
            raise OverBudget()
        session.commit()  # release the usage-row lock before the slow call
        results.append(extract_page(png, no, vision))  # VisionError -> caller maps to 502
    merged = merge(results)
    digest = hashlib.sha256(data).hexdigest()
    claim = Claim(
        id=(merged.claim_id or f"PDF-{digest[:10]}")[:128],
        patient_pseudonym=pseudonym(f"pdf:{digest}"),
        provider=merged.provider,
        payer=merged.payer,
        lines=merged.lines,
        source="pdf",
    )
    (case,) = create_cases(session, ws, [claim], payer_type)
    session.add(CaseDocument(case_id=case.id, content=data, page_count=len(pages)))
    if needs_review(merged.lines):
        case.status = "needs_line_review"
    else:
        run_audit(session, case, ref)
    # Model-supplied text can be huge; keep the "page N, row M" prefix and cap the rest.
    errors = []
    for e in merged.errors:
        path, _, message = e.partition(": ")
        errors.append((path, message[:MAX_ERROR_CHARS]))
    return case, errors
