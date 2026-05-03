"""
DecisionAgent — produces the final PA determination.

Decision logic:
  criteria_met = True  → APPROVED
  criteria_met = False + missing docs → PENDED (additional info request)
  not covered → DENIED (coverage)
  complex cases → ESCALATED (physician review)
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

_SYSTEM = """You are a senior prior authorization decision specialist.
Based on the eligibility check and clinical criteria evaluation, write a formal determination rationale.

Output exactly this JSON:
{
  "decision": "approved" or "denied" or "pended" or "escalated",
  "decision_rationale": "<formal 2-3 sentence rationale suitable for the determination letter>"
}"""


async def decision_agent(state: PriorAuthState) -> dict:
    """
    Determine final PA outcome and produce formal rationale.

    Reads: is_covered, requires_auth, criteria_met, criteria_reasoning, missing_documentation
    Writes: decision, decision_rationale
    """
    req = state["request"]

    # Hard rule: not covered → deny
    if not state.get("is_covered", True):
        return {
            "decision": "denied",
            "decision_rationale": (
                f"Prior authorization for {state.get('extracted_procedure')} (CPT {req['procedure_code']}) "
                f"is denied. The procedure is not covered under the patient's current benefit plan."
            ),
        }

    prompt = (
        f"Procedure: {state.get('extracted_procedure')} (CPT {req['procedure_code']})\n"
        f"Diagnosis: {state.get('extracted_diagnosis')} (ICD-10 {req['diagnosis_code']})\n"
        f"Coverage: {state.get('coverage_details', 'confirmed')}\n"
        f"Clinical criteria met: {state.get('criteria_met')}\n"
        f"Clinical reasoning: {state.get('criteria_reasoning', '')}\n"
        f"Missing documentation: {', '.join(state.get('missing_documentation', [])) or 'none'}"
    )

    response = await _llm.ainvoke(
        [SystemMessage(content=_SYSTEM), HumanMessage(content=prompt)]
    )

    try:
        parsed = _parse_json(response.content)
        decision = parsed.get("decision", "pended")
        rationale = parsed.get("decision_rationale", "")
    except Exception:
        decision = "pended"
        rationale = "Determination could not be completed automatically. Routing to manual review."

    return {
        "decision": decision,
        "decision_rationale": rationale,
        "messages": [response],
    }
