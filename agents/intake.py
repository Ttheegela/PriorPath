"""
IntakeAgent — validates and structures the incoming PA request.

Extracts procedure/diagnosis descriptions, flags missing required fields,
and produces a clean intake summary for downstream agents.
"""

from __future__ import annotations

import json
import re

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from models.state import PriorAuthState


def _parse_json(content: str) -> dict:
    """Extract JSON from LLM response, tolerating markdown code fences."""
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1:
        return json.loads(content[start : end + 1])
    return json.loads(content)


_llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)

_SYSTEM = """You are a prior authorization intake specialist.
Given a PA request, extract and validate key fields.

Output exactly this JSON:
{
  "intake_summary": "<2-sentence summary of the request>",
  "extracted_procedure": "<plain English procedure name>",
  "extracted_diagnosis": "<plain English diagnosis>",
  "missing_fields": ["<field name if absent>"]
}"""


async def intake_agent(state: PriorAuthState) -> dict:
    """
    Parse and validate the incoming prior authorization request.

    Reads: state.request
    Writes: intake_summary, extracted_procedure, extracted_diagnosis, missing_fields
    """
    req = state["request"]

    prompt = (
        f"Patient: {req['patient_name']} | DOB: {req['patient_dob']} | Insurance: {req['insurance_id']}\n"
        f"Procedure: {req['procedure_code']} — {req['procedure_description']}\n"
        f"Diagnosis: {req['diagnosis_code']} — {req['diagnosis_description']}\n"
        f"Ordering provider: {req['ordering_provider']}\n"
        f"Clinical notes: {req['clinical_notes'] or 'none provided'}"
    )

    response = await _llm.ainvoke(
        [SystemMessage(content=_SYSTEM), HumanMessage(content=prompt)]
    )

    try:
        parsed = _parse_json(response.content)
    except Exception:
        parsed = {
            "intake_summary": f"PA request for {req['procedure_description']} — {req['diagnosis_description']}",
            "extracted_procedure": req["procedure_description"],
            "extracted_diagnosis": req["diagnosis_description"],
            "missing_fields": [],
        }

    return {
        "intake_summary": parsed.get("intake_summary", ""),
        "extracted_procedure": parsed.get("extracted_procedure", req["procedure_description"]),
        "extracted_diagnosis": parsed.get("extracted_diagnosis", req["diagnosis_description"]),
        "missing_fields": parsed.get("missing_fields", []),
        "messages": [response],
    }
