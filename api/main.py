"""
PriorPath FastAPI application.

Endpoints:
  GET  /                     — frontend UI
  POST /authorize            — run full PA workflow
  POST /authorize/stream     — SSE streaming PA workflow with node progress
  POST /demo                 — run with pre-seeded ophthalmic scenarios
  GET  /history              — last 50 PA decisions (Supabase)
  GET  /history/{patient_id} — PA history for a specific patient
  GET  /health               — health check
"""

from __future__ import annotations

import json
import pathlib
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from graph.workflow import pa_graph
from models.state import PriorAuthRequest, PriorAuthState
from tools.audit_log import get_history, get_patient_history, init_db
from tools.qdrant_search import qdrant_tool

_FRONTEND = pathlib.Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Auto-seed Qdrant on startup — idempotent, re-ingests if collection is empty
    await qdrant_tool._ensure_seeded()
    yield


app = FastAPI(
    title="PriorPath — Multi-Agent Prior Authorization Engine",
    description=(
        "LangGraph-orchestrated prior authorization system. "
        "Covers eligibility verification, RAG-grounded clinical criteria review, "
        "automated determination, and formal letter generation."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AuthorizeRequest(BaseModel):
    patient_id: str
    patient_name: str
    patient_dob: str
    insurance_id: str
    payer_name: str
    procedure_code: str
    procedure_description: str
    diagnosis_code: str
    diagnosis_description: str
    ordering_provider: str
    clinical_notes: str = ""


class AuthorizeResponse(BaseModel):
    request_id: str
    patient_name: str
    procedure: str
    diagnosis: str
    is_covered: bool
    requires_auth: bool
    criteria_met: bool | None
    decision: str
    decision_rationale: str
    missing_documentation: list[str]
    provider_letter: str
    patient_summary: str


class DemoRequest(BaseModel):
    scenario: int = 1


# ---------------------------------------------------------------------------
# Demo scenarios — ophthalmic PA requests
# ---------------------------------------------------------------------------

_DEMO_SCENARIOS = {
    1: {
        "patient_id": "pt-001", "patient_name": "Maria Santos",
        "patient_dob": "1948-06-15", "insurance_id": "INS-001", "payer_name": "Aetna",
        "procedure_code": "67028", "procedure_description": "Intravitreal anti-VEGF injection",
        "diagnosis_code": "H35.32", "diagnosis_description": "Exudative age-related macular degeneration, bilateral",
        "ordering_provider": "Dr. Priya Sharma, MD (Retinal Surgery)",
        "clinical_notes": (
            "Patient presents with wet AMD confirmed by OCT showing active choroidal neovascularization. "
            "Baseline VA: 20/80 OD. OCT central subfield thickness: 412 microns. "
            "Fundus photography confirms neovascular membrane. No prior anti-VEGF treatment."
        ),
    },
    2: {
        "patient_id": "pt-002", "patient_name": "James O'Brien",
        "patient_dob": "1955-03-22", "insurance_id": "INS-003", "payer_name": "BlueCross",
        "procedure_code": "66984", "procedure_description": "Cataract extraction with IOL implantation",
        "diagnosis_code": "H26.9", "diagnosis_description": "Unspecified cataract",
        "ordering_provider": "Dr. Alan Kim, MD (Ophthalmology)",
        "clinical_notes": (
            "Patient has progressive nuclear sclerotic cataract. Best corrected VA: 20/60 OS. "
            "Slit-lamp confirms dense lens opacity. Patient reports difficulty driving and reading. "
            "Pre-op biometry complete. Informed consent obtained."
        ),
    },
    3: {
        "patient_id": "pt-003", "patient_name": "Linda Chen",
        "patient_dob": "1962-11-08", "insurance_id": "INS-002", "payer_name": "UnitedHealth",
        "procedure_code": "67028", "procedure_description": "Intravitreal anti-VEGF injection",
        "diagnosis_code": "H36.0", "diagnosis_description": "Diabetic retinopathy with macular edema",
        "ordering_provider": "Dr. Marcus Webb, MD",
        "clinical_notes": "Patient has diabetic macular edema. Requesting anti-VEGF treatment.",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_initial_state(req: AuthorizeRequest, request_id: str) -> PriorAuthState:
    pa_request: PriorAuthRequest = {
        "request_id": request_id,
        "patient_id": req.patient_id,
        "patient_name": req.patient_name,
        "patient_dob": req.patient_dob,
        "insurance_id": req.insurance_id,
        "payer_name": req.payer_name,
        "procedure_code": req.procedure_code,
        "procedure_description": req.procedure_description,
        "diagnosis_code": req.diagnosis_code,
        "diagnosis_description": req.diagnosis_description,
        "ordering_provider": req.ordering_provider,
        "clinical_notes": req.clinical_notes,
    }
    return {
        "request": pa_request,
        "intake_summary": "", "extracted_procedure": "", "extracted_diagnosis": "",
        "missing_fields": [], "is_covered": False, "coverage_details": "",
        "requires_auth": False, "matched_criteria": [], "criteria_met": None,
        "criteria_reasoning": "", "missing_documentation": [], "decision": "",
        "decision_rationale": "", "provider_letter": "", "patient_summary": "",
        "skip_to_notification": False, "error": None, "messages": [],
    }


def _build_response(req: AuthorizeRequest, request_id: str, result: PriorAuthState) -> AuthorizeResponse:
    return AuthorizeResponse(
        request_id=request_id,
        patient_name=req.patient_name,
        procedure=result.get("extracted_procedure", req.procedure_description),
        diagnosis=result.get("extracted_diagnosis", req.diagnosis_description),
        is_covered=result.get("is_covered", False),
        requires_auth=result.get("requires_auth", False),
        criteria_met=result.get("criteria_met"),
        decision=result.get("decision", ""),
        decision_rationale=result.get("decision_rationale", ""),
        missing_documentation=result.get("missing_documentation", []),
        provider_letter=result.get("provider_letter", ""),
        patient_summary=result.get("patient_summary", ""),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(_FRONTEND / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "priorpath"}


@app.post("/authorize", response_model=AuthorizeResponse)
async def authorize(req: AuthorizeRequest):
    """Run the full prior authorization workflow."""
    request_id = str(uuid.uuid4())[:8]
    initial = _build_initial_state(req, request_id)
    result: PriorAuthState = await pa_graph.ainvoke(initial)
    return _build_response(req, request_id, result)


@app.post("/authorize/stream")
async def authorize_stream(req: AuthorizeRequest):
    """
    SSE streaming PA workflow.
    Yields one event per completed LangGraph node, then a final 'done' event
    containing the full AuthorizeResponse payload.
    """
    request_id = str(uuid.uuid4())[:8]
    initial = _build_initial_state(req, request_id)

    async def event_stream():
        final_state: PriorAuthState = dict(initial)  # type: ignore
        try:
            async for chunk in pa_graph.astream(initial, stream_mode="updates"):
                for node_name, updates in chunk.items():
                    final_state.update(updates)
                    yield f"data: {json.dumps({'node': node_name})}\n\n"

            response = _build_response(req, request_id, final_state)
            yield f"data: {json.dumps({'done': True, 'result': response.model_dump()})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/demo", response_model=AuthorizeResponse)
async def demo(req: DemoRequest):
    """
    Run a demo PA with pre-seeded ophthalmic scenarios.

    Scenario 1 — Wet AMD anti-VEGF (expect APPROVED)
    Scenario 2 — Cataract surgery (expect APPROVED)
    Scenario 3 — DME anti-VEGF, thin notes (expect PENDED)
    """
    scenario = _DEMO_SCENARIOS.get(req.scenario, _DEMO_SCENARIOS[1])
    return await authorize(AuthorizeRequest(**scenario))


@app.get("/history")
async def history(limit: int = 50):
    """Return the most recent PA decisions across all patients."""
    try:
        records = await get_history(limit=limit)
        return {"records": records, "count": len(records)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/history/{patient_id}")
async def patient_history(patient_id: str):
    """Return all PA decisions for a specific patient."""
    try:
        records = await get_patient_history(patient_id)
        return {"patient_id": patient_id, "records": records, "count": len(records)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port)
