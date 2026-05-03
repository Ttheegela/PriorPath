"""
NotificationAgent — final node. Generates determination letters and logs to PostgreSQL.

Produces:
1. Formal provider determination letter (clinical language)
2. Plain-language patient notification
3. PostgreSQL audit record
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from models.state import PriorAuthState
from tools.audit_log import log_pa_decision

_llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0.2)

_PROVIDER_SYSTEM = """You are a prior authorization correspondence specialist.
Write a formal prior authorization determination letter for the ordering provider.
Include: decision, procedure, diagnosis, rationale, and next steps. Under 150 words. Professional tone."""

_PATIENT_SYSTEM = """You are a patient communications specialist at an insurance company.
Write a clear, compassionate, non-technical notification for the patient.
Explain the outcome in plain language. Under 80 words. No jargon."""


async def notification_agent(state: PriorAuthState) -> dict:
    """
    Generate provider letter + patient summary, then log the audit record.

    Reads: decision, decision_rationale, request, missing_documentation
    Writes: provider_letter, patient_summary
    """
    req = state["request"]
    decision = state.get("decision", "pended")
    rationale = state.get("decision_rationale", "")
    missing = state.get("missing_documentation", [])

    provider_ctx = (
        f"Patient: {req['patient_name']} | Insurance: {req['insurance_id']} ({req.get('payer_name', '')})\n"
        f"Procedure: {req['procedure_description']} (CPT {req['procedure_code']})\n"
        f"Diagnosis: {req['diagnosis_description']} (ICD-10 {req['diagnosis_code']})\n"
        f"Decision: {decision.upper()}\n"
        f"Rationale: {rationale}\n"
        f"Missing documentation: {', '.join(missing) if missing else 'N/A'}"
    )

    patient_ctx = (
        f"The patient {req['patient_name']} submitted a request for {req['procedure_description']}. "
        f"Decision: {decision}. {rationale}"
    )

    provider_resp = await _llm.ainvoke(
        [SystemMessage(content=_PROVIDER_SYSTEM), HumanMessage(content=provider_ctx)]
    )
    patient_resp = await _llm.ainvoke(
        [SystemMessage(content=_PATIENT_SYSTEM), HumanMessage(content=patient_ctx)]
    )

    await log_pa_decision(
        request_id=req["request_id"],
        patient_id=req["patient_id"],
        procedure_code=req["procedure_code"],
        diagnosis_code=req["diagnosis_code"],
        payer_name=req.get("payer_name", "unknown"),
        is_covered=state.get("is_covered", False),
        requires_auth=state.get("requires_auth", False),
        criteria_met=state.get("criteria_met"),
        decision=decision,
        decision_rationale=rationale,
        error=state.get("error"),
    )

    return {
        "provider_letter": provider_resp.content,
        "patient_summary": patient_resp.content,
        "messages": [provider_resp, patient_resp],
    }
