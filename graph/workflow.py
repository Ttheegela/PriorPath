"""
PriorPath LangGraph prior authorization workflow.

Graph topology:

  intake → eligibility → [skip | clinical → decision] → notification → END

Conditional edge after eligibility:
  not covered or no PA required → skip directly to notification
  covered + PA required         → clinical → decision → notification
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from agents.clinical import clinical_agent
from agents.decision import decision_agent
from agents.eligibility import eligibility_agent
from agents.intake import intake_agent
from agents.notification import notification_agent
from models.state import PriorAuthState


def route_after_eligibility(state: PriorAuthState) -> str:
    """Skip clinical review if not covered or PA not required."""
    if state.get("skip_to_notification"):
        return "notification"
    return "clinical"


def build_graph() -> StateGraph:
    """Construct and compile the PriorPath PA StateGraph."""
    graph = StateGraph(PriorAuthState)

    graph.add_node("intake", intake_agent)
    graph.add_node("eligibility", eligibility_agent)
    graph.add_node("clinical", clinical_agent)
    graph.add_node("decision", decision_agent)
    graph.add_node("notification", notification_agent)

    graph.set_entry_point("intake")
    graph.add_edge("intake", "eligibility")

    graph.add_conditional_edges(
        "eligibility",
        route_after_eligibility,
        {"notification": "notification", "clinical": "clinical"},
    )

    graph.add_edge("clinical", "decision")
    graph.add_edge("decision", "notification")
    graph.add_edge("notification", END)

    return graph.compile()


pa_graph = build_graph()
