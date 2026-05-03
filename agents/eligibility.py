"""
EligibilityAgent — verifies insurance coverage and PA requirement.

Calls the payer eligibility tool to confirm:
- Procedure is covered under the patient's plan
- Prior authorization is actually required (not all procedures need one)

If not covered or no auth needed → sets skip_to_notification = True.
"""

from __future__ import annotations

from models.state import PriorAuthState
from tools.payer_simulator import check_eligibility


async def eligibility_agent(state: PriorAuthState) -> dict:
    """
    Check insurance eligibility and whether PA is required.

    Reads: request.insurance_id, request.procedure_code
    Writes: is_covered, coverage_details, requires_auth, skip_to_notification
    """
    req = state["request"]
    result = await check_eligibility(req["insurance_id"], req["procedure_code"])

    # Skip the clinical review if not covered or no PA needed
    skip = not result["is_covered"] or not result["requires_auth"]

    return {
        "is_covered": result["is_covered"],
        "coverage_details": result["coverage_details"],
        "requires_auth": result["requires_auth"],
        "skip_to_notification": skip,
    }
