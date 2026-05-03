"""
ClinicalAgent — evaluates whether clinical criteria are met for PA approval.

Uses RAG over Qdrant-indexed payer guidelines to ground the decision
in real clinical criteria (CMS Local Coverage Determinations + payer policies).
"""

from __future__ import annotations

import json
import re

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from models.state import PriorAuthState
from tools.qdrant_search import qdrant_tool


def _parse_json(content: str) -> dict:
    """Extract JSON from LLM response, tolerating markdown code fences."""
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1:
        return json.loads(content[start : end + 1])
    return json.loads(content)


_llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)

_SYSTEM = """You are a clinical prior authorization reviewer at an insurance company.

Given a PA request and the relevant coverage criteria, determine if clinical criteria are met.

Output exactly this JSON:
{
  "criteria_met": true or false,
  "criteria_reasoning": "<1-2 sentence explanation citing specific criteria from the guidelines>",
  "missing_documentation": ["<what is needed if criteria_met is false>"]
}"""


async def clinical_agent(state: PriorAuthState) -> dict:
    """
    Evaluate clinical criteria using RAG-grounded review.

    Reads: extracted_procedure, extracted_diagnosis, request.clinical_notes
    Writes: matched_criteria, criteria_met, criteria_reasoning, missing_documentation
    """
    req = state["request"]
    query = f"{state['extracted_procedure']} {state['extracted_diagnosis']} {req['procedure_code']}"

    guidelines = await qdrant_tool.search(query, top_k=2)
    guideline_text = "\n\n".join(g["text"] for g in guidelines)

    prompt = (
        f"Procedure: {state['extracted_procedure']} (CPT {req['procedure_code']})\n"
        f"Diagnosis: {state['extracted_diagnosis']} (ICD-10 {req['diagnosis_code']})\n"
        f"Clinical notes: {req['clinical_notes'] or 'none provided'}\n\n"
        f"Applicable coverage criteria:\n{guideline_text}"
    )

    response = await _llm.ainvoke(
        [SystemMessage(content=_SYSTEM), HumanMessage(content=prompt)]
    )

    try:
        parsed = _parse_json(response.content)
    except Exception:
        parsed = {
            "criteria_met": False,
            "criteria_reasoning": "Could not evaluate criteria — defaulting to pend for manual review.",
            "missing_documentation": ["Complete clinical documentation required"],
        }

    return {
        "matched_criteria": [g["text"][:150] + "..." for g in guidelines],
        "criteria_met": parsed.get("criteria_met", False),
        "criteria_reasoning": parsed.get("criteria_reasoning", ""),
        "missing_documentation": parsed.get("missing_documentation", []),
        "messages": [response],
    }
