"""Draft → check numbers → retry once. The model explains a flag; it never decides one."""

import json
import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.llm.client import LLMClient
from app.llm.grounding import has_url, unsupported_numbers
from app.llm.rule_text import RULE_TEXT
from app.models import Flag

MAX_ATTEMPTS = 2
LLM_FAILED = "llm_failed"
UNGROUNDED = "ungrounded"
logger = logging.getLogger(__name__)
SYSTEM_PROMPT = (
    "You explain one medical-billing finding to a claims auditor in plain English, in at most three "
    "sentences. "
    "Use only facts from the RULE, FINDING and EVIDENCE sections. Do not introduce any number, "
    "dollar amount, "
    "date or code that is not written there. Do not call anything fraud or illegal. If the severity "
    "is 'lead', "
    "say the finding needs confirmation before it is disputed."
)


class ExplainState(TypedDict):
    prompt: str
    sources: list[str]
    attempt: int
    draft: str
    problems: list[str]
    explanation: str | None


def build_prompt(flag: Flag) -> tuple[str, list[str]]:
    rule = RULE_TEXT.get(flag.rule_id, "")
    evidence = json.dumps(flag.evidence.row, sort_keys=True)
    prompt = (
        f"RULE ({flag.rule_id}): {rule}\nSEVERITY: {flag.severity.value}\nFINDING: {flag.message}\n"
        f"EVIDENCE ({flag.evidence.table}, {flag.evidence.ref_version}): {evidence}\n"
        f"ESTIMATED OVERCHARGE: ${flag.est_overcharge}"
    )
    sources = [
        rule,
        flag.message,
        evidence,
        f"${flag.est_overcharge}",
        flag.evidence.ref_version or "",
    ]
    return prompt, sources


def build_explain_graph(llm: LLMClient) -> Any:
    def draft(state: ExplainState) -> dict[str, Any]:
        note = ""
        if state["problems"]:
            note = (
                f"\n\nYour previous answer used numbers that are not in the evidence: "
                f"{', '.join(state['problems'])}. Rewrite it using only numbers from the evidence."
            )
        return {"draft": llm.complete(SYSTEM_PROMPT, state["prompt"] + note), "attempt": state["attempt"] + 1}

    def check(state: ExplainState) -> dict[str, Any]:
        problems = unsupported_numbers(state["draft"], state["sources"])
        ok = bool(state["draft"].strip()) and not problems and not has_url(state["draft"])
        return {"problems": problems, "explanation": state["draft"].strip() if ok else None}

    def route(state: ExplainState) -> str:
        return "done" if state["explanation"] is not None or state["attempt"] >= MAX_ATTEMPTS else "retry"

    graph = StateGraph(ExplainState)
    graph.add_node("draft", draft)
    graph.add_node("check", check)
    graph.add_edge(START, "draft")
    graph.add_edge("draft", "check")
    graph.add_conditional_edges("check", route, {"retry": "draft", "done": END})
    return graph.compile()


def explain_flag_detailed(flag: Flag, llm: LLMClient) -> tuple[str | None, str | None]:
    """(explanation, None) on success, else (None, LLM_FAILED | UNGROUNDED)."""
    prompt, sources = build_prompt(flag)
    try:
        result = build_explain_graph(llm).invoke(
            {
                "prompt": prompt,
                "sources": sources,
                "attempt": 0,
                "draft": "",
                "problems": [],
                "explanation": None,
            }
        )
    except Exception as e:  # noqa: BLE001 — an LLM or network failure must never break an audit
        logger.warning("explanation LLM call failed: %s", type(e).__name__)
        return None, LLM_FAILED
    explanation = result.get("explanation")
    if isinstance(explanation, str):
        return explanation, None
    return None, UNGROUNDED


def explain_flag(flag: Flag, llm: LLMClient) -> str | None:
    return explain_flag_detailed(flag, llm)[0]
