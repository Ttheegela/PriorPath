import json
import json as jsonlib
import logging
import time
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import LLMDep, RefDep, SessionDep, VisionDep, WorkspaceDep
from app.api.schemas import (
    CaseDetail,
    CaseSummary,
    FlagOut,
    LetterOut,
    LineOut,
    LinesEdit,
    ParseErrorOut,
    UploadResult,
)
from app.db.models import Case, CaseDocument, FlagRow, Letter
from app.db.session import get_engine
from app.ingest.fhir import parse_fhir
from app.ingest.pdf import PdfError, render_page
from app.llm.vision import VisionError
from app.models import LineItem, LineSource
from app.rules import PayerType
from app.services.audit_log import record
from app.services.audit_run import run_audit
from app.services.capacity import ensure_capacity
from app.services.cases import case_flags, create_cases, get_case_or_404, summarize, to_claim
from app.services.explanations import explain_row
from app.services.pdf_cases import OverBudget, TooSlow, create_pdf_case

router = APIRouter()
MAX_UPLOAD_BYTES = 4_000_000
NOT_JSON = "This file isn't valid JSON. Upload a FHIR claim bundle (.json) or a PDF bill."
NOT_FHIR = (
    "This file isn't a FHIR claim bundle (ExplanationOfBenefit). "
    "Download the sample file to see the expected format."
)
NEEDS_CONFIRM = "PDF uploads must be synthetic or test bills; confirm to continue"
EXPLAIN_DEADLINE_SECONDS = 240  # stay inside the serverless function time limit


async def read_upload(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"upload larger than {MAX_UPLOAD_BYTES} bytes")
    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"upload larger than {MAX_UPLOAD_BYTES} bytes")
    return body


@router.post("/api/cases", status_code=201, response_model=UploadResult)
def upload_cases(
    body: Annotated[bytes, Depends(read_upload)],
    ws: WorkspaceDep,
    session: SessionDep,
    ref: RefDep,
    request: Request,
    vision: VisionDep,
    payer_type: PayerType = "unknown",
    confirm_synthetic: bool = False,
) -> UploadResult | JSONResponse:
    if body[:5] == b"%PDF-" or request.headers.get("content-type", "").startswith("application/pdf"):
        return upload_pdf(body, ws, session, ref, vision, payer_type, confirm_synthetic)
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return JSONResponse(
            status_code=422,
            content={"cases": [], "errors": [{"path": "$", "message": NOT_JSON}]},
        )
    ensure_capacity(session)
    result = parse_fhir(data)
    errors = [ParseErrorOut(path=e.path, message=e.message) for e in result.errors]
    if not result.claims:
        if not (isinstance(data, dict) and data.get("resourceType") == "Bundle"):
            errors = [ParseErrorOut(path="$", message=NOT_FHIR)]
        return JSONResponse(
            status_code=422, content=UploadResult(cases=[], errors=errors).model_dump(mode="json")
        )
    cases = create_cases(session, ws, result.claims, payer_type)
    for c in cases:
        run_audit(session, c, ref)
    session.commit()
    return UploadResult(cases=[summarize(c, case_flags(session, c.id)) for c in cases], errors=errors)


def upload_pdf(
    body: bytes,
    ws: WorkspaceDep,
    session: SessionDep,
    ref: RefDep,
    vision: VisionDep,
    payer_type: PayerType,
    confirm_synthetic: bool,
) -> UploadResult:
    if not confirm_synthetic:
        raise HTTPException(status_code=422, detail=NEEDS_CONFIRM)
    if vision is None:
        raise HTTPException(status_code=503, detail="PDF extraction is not configured on this server")
    ensure_capacity(session)
    try:
        case, errors = create_pdf_case(session, ws, body, payer_type, ref, vision)
    except PdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OverBudget:
        raise HTTPException(status_code=429, detail="hourly AI limit reached; try again later") from None
    except TooSlow:
        raise HTTPException(
            status_code=504, detail="this bill took too long to read; try a shorter PDF"
        ) from None
    except VisionError:
        session.rollback()
        raise HTTPException(
            status_code=502, detail="the AI model could not read this bill; try again"
        ) from None
    session.commit()
    return UploadResult(
        cases=[summarize(case, case_flags(session, case.id))],
        errors=[ParseErrorOut(path=p, message=m) for p, m in errors],
    )


@router.get("/api/cases", response_model=list[CaseSummary])
def list_cases(ws: WorkspaceDep, session: SessionDep) -> list[CaseSummary]:
    cases = session.scalars(
        select(Case)
        .options(selectinload(Case.document))
        .where(Case.workspace_id == ws.id)
        .order_by(Case.created_at.desc(), Case.id.desc())
    )
    # ponytail: one flags query per case; fine for demo-sized workspaces, batch it if lists grow past ~100.
    return [summarize(c, case_flags(session, c.id)) for c in cases]


def case_detail(session: SessionDep, case: Case) -> CaseDetail:
    rows = case_flags(session, case.id)
    letter = session.scalars(
        select(Letter).where(Letter.case_id == case.id).order_by(Letter.created_at.desc())
    ).first()
    return CaseDetail(
        **summarize(case, rows).model_dump(),
        lines=[LineOut.model_validate(ln.model_dump()) for ln in to_claim(case).lines],
        flags=[FlagOut.model_validate(r, from_attributes=True) for r in rows],
        letter=LetterOut.model_validate(letter, from_attributes=True) if letter else None,
    )


@router.get("/api/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep) -> CaseDetail:
    return case_detail(session, get_case_or_404(session, ws, case_id))


@router.get("/api/cases/{case_id}/pages/{page}")
def get_page(case_id: uuid.UUID, page: int, ws: WorkspaceDep, session: SessionDep) -> Response:
    case = get_case_or_404(session, ws, case_id)
    doc = session.get(CaseDocument, case.id)
    if doc is None or not 1 <= page <= doc.page_count:
        raise HTTPException(status_code=404, detail="page not found")
    return Response(
        render_page(doc.content, page),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},  # stored PDFs never change
    )


@router.patch("/api/cases/{case_id}/lines", response_model=CaseDetail)
def edit_lines(case_id: uuid.UUID, edit: LinesEdit, ws: WorkspaceDep, session: SessionDep) -> CaseDetail:
    case = get_case_or_404(session, ws, case_id)
    if session.scalar(select(Letter.id).where(Letter.case_id == case.id, Letter.status == "approved")):
        raise HTTPException(status_code=409, detail="this case already has an approved letter")
    ids = [e.id for e in edit.lines]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="line ids must be unique")
    claim = to_claim(case)
    diagnoses = {ln.id: ln.diagnosis_codes for ln in claim.lines}
    lines = []
    for i, e in enumerate(edit.lines):
        try:
            lines.append(
                LineItem(
                    **e.model_dump(),
                    diagnosis_codes=diagnoses.get(e.id, []),
                    source=LineSource.EXTRACTED,
                    confidence=1.0,
                )
            )
        except ValidationError as exc:
            problems = [f"lines[{i}].{'.'.join(map(str, p['loc']))}: {p['msg']}" for p in exc.errors()]
            raise HTTPException(status_code=422, detail="; ".join(problems)) from exc
    claim.lines = lines
    case.claim = claim.model_dump(mode="json")
    session.execute(delete(FlagRow).where(FlagRow.case_id == case.id))
    session.execute(delete(Letter).where(Letter.case_id == case.id, Letter.status == "draft"))
    case.status = "uploaded"
    record(session, ws.id, "lines_edited", case_id=case.id, detail={"lines": len(lines)})
    session.commit()
    return case_detail(session, case)


@router.post("/api/cases/{case_id}/audit", response_model=CaseDetail)
def audit_case(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep, ref: RefDep) -> CaseDetail:
    case = get_case_or_404(session, ws, case_id)
    if session.scalar(select(Letter.id).where(Letter.case_id == case.id, Letter.status == "approved")):
        raise HTTPException(status_code=409, detail="this case already has an approved letter")
    if case.status == "needs_line_review":
        raise HTTPException(status_code=409, detail="review the extracted lines first")
    run_audit(session, case, ref)
    session.commit()
    return case_detail(session, case)


EXPLAINABLE = ("pending", "unavailable")


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {jsonlib.dumps(data)}\n\n"


@router.post("/api/cases/{case_id}/explain")
def explain_case(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep, llm: LLMDep) -> StreamingResponse:
    case = get_case_or_404(session, ws, case_id)
    claim_id = str(case.claim["id"])
    workspace_id = ws.id
    flag_ids = [r.id for r in case_flags(session, case.id) if r.explanation_status in EXPLAINABLE]
    session.close()  # release the request's connection; the stream opens its own

    def stream() -> Iterator[str]:
        started = time.monotonic()
        ready = unavailable = remaining = 0
        with Session(get_engine(), expire_on_commit=False) as s:
            yield _sse("start", {"pending": len(flag_ids)})
            try:
                for index, flag_id in enumerate(flag_ids, start=1):
                    if time.monotonic() - started >= EXPLAIN_DEADLINE_SECONDS:
                        remaining = len(flag_ids) - index + 1
                        break
                    try:
                        row = s.get(FlagRow, flag_id)
                        if row is None:
                            continue
                        status, reason = explain_row(s, workspace_id, row, claim_id, llm)
                        s.commit()
                    except Exception:
                        logging.getLogger(__name__).exception("explanation failed unexpectedly")
                        s.rollback()
                        yield _sse("error", {"flag_id": str(flag_id), "reason": "internal error"})
                        continue
                    ready += status == "ready"
                    unavailable += status == "unavailable"
                    yield _sse(
                        "explanation",
                        {
                            "index": index,
                            "total": len(flag_ids),
                            "flag_id": str(flag_id),
                            "status": status,
                            "explanation": row.explanation,
                            "reason": reason,
                        },
                    )
            finally:
                record(
                    s,
                    workspace_id,
                    "explanations_generated",
                    case_id=case_id,
                    detail={"ready": ready, "unavailable": unavailable, "remaining": remaining},
                )
                s.commit()
            yield _sse("done", {"ready": ready, "unavailable": unavailable, "remaining": remaining})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
