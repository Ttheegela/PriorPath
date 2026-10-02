import io
import uuid
from datetime import UTC, datetime
from typing import Literal

from docx import Document
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import delete, select

from app.api.deps import RefDep, SessionDep, WorkspaceDep
from app.api.schemas import LetterEdit, LetterOut
from app.db.models import Case, Letter
from app.llm.grounding import has_url, unsupported_numbers
from app.services.audit_log import record
from app.services.cases import case_flags, get_case_or_404, to_claim
from app.services.letters import LETTER_SEVERITIES, build_letter

router = APIRouter()
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _letter_or_404(session: SessionDep, ws: WorkspaceDep, letter_id: uuid.UUID) -> tuple[Letter, Case]:
    letter = session.get(Letter, letter_id)
    case = session.get(Case, letter.case_id) if letter else None
    if letter is None or case is None or case.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="letter not found")
    return letter, case


@router.post("/api/cases/{case_id}/letter", status_code=201, response_model=LetterOut)
def draft_letter(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep, ref: RefDep) -> LetterOut:
    case = get_case_or_404(session, ws, case_id)
    if session.scalar(select(Letter.id).where(Letter.case_id == case.id, Letter.status == "approved")):
        raise HTTPException(status_code=409, detail="this case already has an approved letter")
    rows = case_flags(session, case.id)
    accepted = [r for r in rows if r.status == "accepted" and r.severity in LETTER_SEVERITIES]
    if not accepted:
        raise HTTPException(
            status_code=409, detail="accept at least one billing error or price outlier first"
        )
    session.execute(delete(Letter).where(Letter.case_id == case.id, Letter.status == "draft"))
    versions = [v.ref_version for v in ref.versions]
    body = build_letter(to_claim(case), rows, versions, datetime.now(UTC).date())
    letter = Letter(case_id=case.id, flag_ids=[str(r.id) for r in accepted], body=body, generated_body=body)
    session.add(letter)
    case.status = "letter_ready"
    session.flush()
    record(session, ws.id, "letter_drafted", case_id=case.id, detail={"letter_id": str(letter.id)})
    session.commit()
    session.refresh(letter)
    return LetterOut.model_validate(letter, from_attributes=True)


@router.patch("/api/letters/{letter_id}", response_model=LetterOut)
def edit_letter(letter_id: uuid.UUID, edit: LetterEdit, ws: WorkspaceDep, session: SessionDep) -> LetterOut:
    letter, case = _letter_or_404(session, ws, letter_id)
    if letter.status != "draft":
        raise HTTPException(status_code=409, detail="approved letters can't be edited")
    added = unsupported_numbers(edit.body, [letter.generated_body])
    if added:
        raise HTTPException(
            status_code=422, detail=f"edit adds numbers not in the findings: {', '.join(added)}"
        )
    if has_url(edit.body) and not has_url(letter.generated_body):
        raise HTTPException(status_code=422, detail="links are not allowed in the letter")
    letter.body = edit.body
    record(session, ws.id, "letter_edited", case_id=case.id, detail={"letter_id": str(letter.id)})
    session.commit()
    return LetterOut.model_validate(letter, from_attributes=True)


@router.post("/api/letters/{letter_id}/approve", response_model=LetterOut)
def approve_letter(letter_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep) -> LetterOut:
    letter, case = _letter_or_404(session, ws, letter_id)
    if letter.status != "draft":
        raise HTTPException(status_code=409, detail="letter is already approved")
    current = {
        str(r.id)
        for r in case_flags(session, case.id)
        if r.status == "accepted" and r.severity in LETTER_SEVERITIES
    }
    if current != set(letter.flag_ids):
        raise HTTPException(
            status_code=409, detail="findings changed since this letter was drafted; draft it again"
        )
    letter.status = "approved"
    letter.approved_at = datetime.now(UTC)
    case.status = "approved"
    record(session, ws.id, "letter_approved", case_id=case.id, detail={"letter_id": str(letter.id)})
    session.commit()
    return LetterOut.model_validate(letter, from_attributes=True)


@router.get("/api/letters/{letter_id}/export")
def export_letter(
    letter_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep, format: Literal["txt", "docx"] = "txt"
) -> Response:
    letter, case = _letter_or_404(session, ws, letter_id)
    if letter.status != "approved":
        raise HTTPException(status_code=409, detail="approve the letter before exporting it")
    if format == "txt":
        payload: bytes = letter.body.encode()
    else:
        doc = Document()
        for paragraph in letter.body.split("\n"):
            doc.add_paragraph(paragraph)
        buf = io.BytesIO()
        doc.save(buf)
        payload = buf.getvalue()
    case.status = "exported"
    record(
        session,
        ws.id,
        "letter_exported",
        case_id=case.id,
        detail={"letter_id": str(letter.id), "format": format},
    )
    session.commit()
    headers = {"Content-Disposition": f'attachment; filename="dispute-letter-{letter.id}.{format}"'}
    if format == "txt":
        return PlainTextResponse(letter.body, headers=headers)
    return Response(payload, media_type=DOCX_TYPE, headers=headers)
