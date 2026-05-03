"""
Supabase audit logger for prior authorization decisions.

Every PA determination is logged for compliance, appeals support,
and analytics (approval rates, pend reasons, turnaround time).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from supabase import AsyncClient, acreate_client

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_client: Optional[AsyncClient] = None


async def _get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = await acreate_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


async def init_db() -> None:
    """No-op: table is created via Supabase migration."""
    pass


async def log_pa_decision(
    request_id: str,
    patient_id: str,
    procedure_code: str,
    diagnosis_code: str,
    payer_name: str,
    is_covered: bool,
    requires_auth: bool,
    criteria_met: bool | None,
    decision: str,
    decision_rationale: str,
    error: str | None,
) -> str:
    """Persist a prior auth audit record and return its ID."""
    record_id = str(uuid.uuid4())
    client = await _get_client()
    await (
        client.table("prior_auth_audit")
        .insert(
            {
                "id": record_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "request_id": request_id,
                "patient_id": patient_id,
                "procedure_code": procedure_code,
                "diagnosis_code": diagnosis_code,
                "payer_name": payer_name,
                "is_covered": is_covered,
                "requires_auth": requires_auth,
                "criteria_met": criteria_met,
                "decision": decision,
                "decision_rationale": decision_rationale,
                "error": error,
            }
        )
        .execute()
    )
    return record_id


async def get_history(limit: int = 50) -> list[dict]:
    """Return the most recent PA decisions across all patients."""
    client = await _get_client()
    result = await (
        client.table("prior_auth_audit")
        .select(
            "id, created_at, request_id, patient_id, procedure_code, "
            "diagnosis_code, payer_name, decision, criteria_met, is_covered, requires_auth"
        )
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


async def get_patient_history(patient_id: str) -> list[dict]:
    """Return all PA decisions for a specific patient."""
    client = await _get_client()
    result = await (
        client.table("prior_auth_audit")
        .select("*")
        .eq("patient_id", patient_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data
