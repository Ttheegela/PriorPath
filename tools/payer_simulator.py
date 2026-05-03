"""
Simulated payer eligibility API.

In production: replace with real-time payer API calls (Availity, Change Healthcare,
or payer-specific EDI 270/271 eligibility transactions).

Supports the major payers B+L works with for ophthalmic prior authorizations.
"""

from __future__ import annotations

import random

# Procedures that always require prior authorization for ophthalmic treatments
_PA_REQUIRED_CODES = {
    "67028",  # Intravitreal injection (anti-VEGF: Lucentis, Eylea, Vabysmo)
    "66984",  # Cataract surgery with IOL (standard)
    "66982",  # Cataract surgery, complex
    "67036",  # Vitrectomy
    "65855",  # Trabeculoplasty (glaucoma)
    "92083",  # Visual field exam (extended)
    "92134",  # OCT (posterior segment)
}

# Procedures that typically do NOT require PA
_NO_PA_CODES = {
    "92002",  # Ophthalmological service, new patient
    "92012",  # Ophthalmological service, established
    "92015",  # Refraction
}

_DEMO_COVERAGE = {
    "INS-001": {
        "payer": "Aetna",
        "plan": "PPO Gold",
        "covered": True,
        "deductible_met": True,
        "copay": "$40 specialist copay",
    },
    "INS-002": {
        "payer": "UnitedHealth",
        "plan": "HMO Silver",
        "covered": True,
        "deductible_met": False,
        "copay": "$200 deductible remaining",
    },
    "INS-003": {
        "payer": "BlueCross",
        "plan": "Medicare Advantage",
        "covered": True,
        "deductible_met": True,
        "copay": "80/20 coinsurance",
    },
}


async def check_eligibility(insurance_id: str, procedure_code: str) -> dict:
    """
    Check insurance eligibility and PA requirement for a procedure.

    Returns coverage details and whether prior authorization is required.
    """
    coverage = _DEMO_COVERAGE.get(insurance_id, {
        "payer": "Unknown Payer",
        "plan": "Standard Plan",
        "covered": True,
        "deductible_met": True,
        "copay": "Standard cost-sharing applies",
    })

    requires_auth = procedure_code in _PA_REQUIRED_CODES

    return {
        "is_covered": coverage["covered"],
        "payer_name": coverage["payer"],
        "plan_type": coverage["plan"],
        "deductible_met": coverage["deductible_met"],
        "copay": coverage["copay"],
        "requires_auth": requires_auth,
        "coverage_details": (
            f"{coverage['plan']} | {coverage['copay']} | "
            f"Deductible {'met' if coverage['deductible_met'] else 'not met'}"
        ),
    }
