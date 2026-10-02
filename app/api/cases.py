import json
import json as jsonlib
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import LLMDep, RefDep, SessionDep, WorkspaceDep
from app.api.schemas import CaseDetail, CaseSummary, FlagOut, LetterOut, LineOut, ParseErrorOut, UploadResult
from app.db.models import Case, FlagRow, Letter
from app.db.session import get_engine
from app.ingest.fhir import parse_fhir
from app.rules import PayerType
from app.services.audit_log import record
from app.services.audit_run import run_audit
from app.services.capacity import ensure_capacity
from app.services.cases import case_flags, create_cases, get_case_or_404, summarize, to_claim
from app.services.explanations import explain_row

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
    except (UnicodeDecodeError, ValueError, RecursionError):
        return JSONResponse(
            status_code=422,
            content={"cases": [], "errors": [{"path": "$", "message": "body is not valid JSON"}]},
        )
    ensure_capacity(session)
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


@router.post("/api/cases/{case_id}/audit", response_model=CaseDetail)
def audit_case(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep, ref: RefDep) -> CaseDetail:
    case = get_case_or_404(session, ws, case_id)
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

    def stream() -> Iterator[str]:
        # The request's session closes when the response starts streaming, so use our own.
        with Session(get_engine(), expire_on_commit=False) as s:
            yield _sse("start", {"pending": len(flag_ids)})
            ready = unavailable = 0
            for index, flag_id in enumerate(flag_ids, start=1):
                row = s.get(FlagRow, flag_id)
                if row is None:
                    continue
                status, reason = explain_row(s, workspace_id, row, claim_id, llm)
                s.commit()
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
            record(
                s,
                workspace_id,
                "explanations_generated",
                case_id=case_id,
                detail={"ready": ready, "unavailable": unavailable},
            )
            s.commit()
            yield _sse("done", {"ready": ready, "unavailable": unavailable})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
