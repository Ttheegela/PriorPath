import hashlib
from collections import Counter
from time import monotonic
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Case, CaseDocument, Workspace
from app.ingest.fhir import pseudonym
from app.ingest.pdf import pdf_page_images_with_redaction
from app.llm.extract import extract_page, merge, needs_review
from app.llm.vision import VisionClient
from app.models import Claim, LineItem
from app.reference.base import InMemoryReference
from app.services import llm_budget
from app.services.audit_run import run_audit
from app.services.cases import create_cases

MAX_ERROR_CHARS = 160
EXTRACT_DEADLINE_SECONDS = 240  # stay inside the serverless function time limit (300 s)


class OverBudget(Exception):
    pass


class TooSlow(Exception):
    pass


def store_pdf_case(
    session: Session,
    ws: Workspace,
    data: bytes,
    page_count: int,
    claim_id: str | None,
    provider: str | None,
    payer: str | None,
    lines: list[LineItem],
    payer_type: str,
    ref: InMemoryReference,
    actor: str = "reviewer",
    redaction: dict[str, Any] | None = None,
) -> Case:
    """Create the case and its stored PDF from already-extracted lines (no model call)."""
    digest = hashlib.sha256(data).hexdigest()
    claim = Claim(
        id=(claim_id or f"PDF-{digest[:10]}")[:128],
        patient_pseudonym=pseudonym(f"pdf:{digest}"),
        provider=provider,
        payer=payer,
        lines=lines,
        source="pdf",
    )
    extra = {"redaction": redaction} if redaction is not None else None
    (case,) = create_cases(session, ws, [claim], payer_type, actor, extra)
    session.add(CaseDocument(case_id=case.id, content=data, page_count=page_count))
    if needs_review(lines):
        case.status = "needs_line_review"
    else:
        run_audit(session, case, ref, actor)
    return case


def create_pdf_case(
    session: Session,
    ws: Workspace,
    data: bytes,
    payer_type: str,
    ref: InMemoryReference,
    vision: VisionClient,
) -> tuple[Case, list[tuple[str, str]], dict[str, Any]]:
    started = monotonic()
    pages, redactions = pdf_page_images_with_redaction(data)  # PdfError -> caller maps to 422
    entities: Counter[str] = Counter()
    for r in redactions:
        entities.update(r.entities)
    redaction = {
        "pages_redacted": sum(r.redactable for r in redactions),
        "pages_not_redactable": sum(not r.redactable for r in redactions),
        "entities": dict(entities),
    }
    if llm_budget.remaining(session, ws.id, "extract") < len(pages):
        raise OverBudget()  # fail fast, before any model call
    results = []
    for no, image in enumerate(pages, start=1):
        if monotonic() - started >= EXTRACT_DEADLINE_SECONDS:
            session.rollback()
            raise TooSlow()
        if not llm_budget.try_consume(session, ws.id, "extract"):
            session.rollback()
            raise OverBudget()
        session.commit()  # release the usage-row lock before the slow call
        results.append(extract_page(image, no, vision))  # VisionError -> caller maps to 502
    merged = merge(results)
    case = store_pdf_case(
        session,
        ws,
        data,
        len(pages),
        merged.claim_id,
        merged.provider,
        merged.payer,
        merged.lines,
        payer_type,
        ref,
        redaction=redaction,
    )
    # Model-supplied text can be huge; keep the "page N, row M" prefix and cap the rest.
    errors = []
    for e in merged.errors:
        path, _, message = e.partition(": ")
        errors.append((path, message[:MAX_ERROR_CHARS]))
    return case, errors, redaction
