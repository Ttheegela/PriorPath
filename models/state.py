"""
LangGraph TypedDict state shared across all PriorPath agents.

The prior authorization workflow:
  Intake → Eligibility → Clinical Criteria → Decision → Notification
"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class PADecision(str):
    APPROVED = "approved"
    DENIED = "denied"
    PENDED = "pended"       # additional info requested
    ESCALATED = "escalated"  # requires physician review


class PriorAuthRequest(TypedDict):
    """Structured prior authorization request (payer-agnostic)."""
    request_id: str
    patient_id: str
    patient_name: str
    patient_dob: str
    insurance_id: str
    payer_name: str
    procedure_code: str       # CPT code
    procedure_description: str
    diagnosis_code: str       # ICD-10 code
    diagnosis_description: str
    ordering_provider: str
    clinical_notes: str


class PriorAuthState(TypedDict):
    """Full state object flowing through the PriorPath graph."""

    # --- Input ---
    request: PriorAuthRequest

    # --- Intake agent output ---
    intake_summary: str
    extracted_procedure: str
    extracted_diagnosis: str
    missing_fields: list[str]   # Fields absent from the request

    # --- Eligibility agent output ---
    is_covered: bool
    coverage_details: str       # Plan type, deductible status, copay
    requires_auth: bool         # Some procedures don't need PA

    # --- Clinical criteria agent output ---
    matched_criteria: list[str]   # Payer guideline excerpts that matched
    criteria_met: bool            # True if all required criteria satisfied
    criteria_reasoning: str       # RAG-grounded LLM reasoning

    # --- Decision agent output ---
    decision: str                 # approved | denied | pended | escalated
    decision_rationale: str       # Formal rationale for the determination
    missing_documentation: list[str]  # If pended: what's needed

    # --- Notification agent output ---
    provider_letter: str     # Formal prior auth determination letter
    patient_summary: str     # Plain-language patient notification

    # --- Control flow ---
    skip_to_notification: bool  # True if not covered or no auth required
    error: str | None

    # --- Audit ---
    messages: Annotated[list[Any], add_messages]
