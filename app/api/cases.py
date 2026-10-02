import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import SessionDep, WorkspaceDep
from app.api.schemas import CaseDetail, CaseSummary, FlagOut, LetterOut, LineOut, ParseErrorOut, UploadResult
from app.db.models import Case, Letter
from app.ingest.fhir import parse_fhir
from app.rules import PayerType
from app.services.cases import case_flags, create_cases, get_case_or_404, summarize, to_claim

router = APIRouter()
MAX_UPLOAD_BYTES = 4_000_000


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
    payer_type: PayerType = "unknown",
) -> UploadResult | JSONResponse:
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return JSONResponse(
            status_code=422,
            content={"cases": [], "errors": [{"path": "$", "message": "body is not valid JSON"}]},
        )
    result = parse_fhir(data)
    errors = [ParseErrorOut(path=e.path, message=e.message) for e in result.errors]
    if not result.claims:
        return JSONResponse(
            status_code=422, content=UploadResult(cases=[], errors=errors).model_dump(mode="json")
        )
    cases = create_cases(session, ws, result.claims, payer_type)
    session.commit()
    for c in cases:
        session.refresh(c)
    return UploadResult(cases=[summarize(c, []) for c in cases], errors=errors)


@router.get("/api/cases", response_model=list[CaseSummary])
def list_cases(ws: WorkspaceDep, session: SessionDep) -> list[CaseSummary]:
    cases = session.scalars(
        select(Case).where(Case.workspace_id == ws.id).order_by(Case.created_at.desc(), Case.id.desc())
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
