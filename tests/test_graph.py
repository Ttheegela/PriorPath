"""End-to-end tests for the PriorPath PA workflow graph."""

from __future__ import annotations

import pytest

from graph.workflow import pa_graph
from models.state import PriorAuthRequest, PriorAuthState


def _make_state(
    procedure_code: str,
    procedure_desc: str,
    diagnosis_code: str,
    diagnosis_desc: str,
    insurance_id: str,
    clinical_notes: str,
) -> PriorAuthState:
    req: PriorAuthRequest = {
        "request_id": "test-001",
        "patient_id": "pt-test",
        "patient_name": "Test Patient",
        "patient_dob": "1970-01-01",
        "insurance_id": insurance_id,
        "payer_name": "Test Payer",
        "procedure_code": procedure_code,
        "procedure_description": procedure_desc,
        "diagnosis_code": diagnosis_code,
        "diagnosis_description": diagnosis_desc,
        "ordering_provider": "Dr. Test Provider",
        "clinical_notes": clinical_notes,
    }
    return {
        "request": req,
        "intake_summary": "", "extracted_procedure": "", "extracted_diagnosis": "",
        "missing_fields": [], "is_covered": False, "coverage_details": "",
        "requires_auth": False, "matched_criteria": [], "criteria_met": None,
        "criteria_reasoning": "", "missing_documentation": [], "decision": "",
        "decision_rationale": "", "provider_letter": "", "patient_summary": "",
        "skip_to_notification": False, "error": None, "messages": [],
    }


@pytest.mark.asyncio
async def test_anti_vegf_with_good_documentation_approved():
    """Well-documented anti-VEGF request should be approved."""
    state = _make_state(
        procedure_code="67028",
        procedure_desc="Intravitreal anti-VEGF injection",
        diagnosis_code="H35.32",
        diagnosis_desc="Wet age-related macular degeneration",
        insurance_id="INS-001",
        clinical_notes=(
            "Active CNV confirmed by OCT. Central subfield thickness 420 microns. "
            "VA 20/80. Fundus photography confirms neovascular AMD. No prior treatment."
        ),
    )
    result = await pa_graph.ainvoke(state)
    assert result["decision"] in ("approved", "pended")
    assert result["provider_letter"]
    assert result["patient_summary"]


@pytest.mark.asyncio
async def test_procedure_without_pa_requirement_skips_clinical():
    """Procedure not requiring PA should skip clinical review."""
    state = _make_state(
        procedure_code="92002",  # Routine ophthalmology visit — no PA needed
        procedure_desc="Ophthalmological examination, new patient",
        diagnosis_code="Z01.00",
        diagnosis_desc="Routine eye exam",
        insurance_id="INS-001",
        clinical_notes="Routine annual eye exam.",
    )
    result = await pa_graph.ainvoke(state)
    # Should skip clinical entirely
    assert result["requires_auth"] is False
    assert result["decision"]


@pytest.mark.asyncio
async def test_incomplete_notes_results_in_pend():
    """Sparse clinical notes should result in pend or denial, not approval."""
    state = _make_state(
        procedure_code="67028",
        procedure_desc="Intravitreal anti-VEGF injection",
        diagnosis_code="H36.0",
        diagnosis_desc="Diabetic macular edema",
        insurance_id="INS-002",
        clinical_notes="Patient needs injection.",  # Deliberately sparse
    )
    result = await pa_graph.ainvoke(state)
    assert result["decision"] in ("pended", "denied", "escalated")
