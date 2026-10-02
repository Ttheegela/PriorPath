from typing import Any

import httpx
from fastapi.testclient import TestClient

from app.ingest.fhir import claims_to_bundle
from app.models import Claim
from tests.helpers import line


def sample_claim(claim_id: str = "EOB-1") -> Claim:
    """Fixture claim: R1 duplicate (96372 x2), R5 outlier (99213 at 300), R4 status-I (77061)."""
    return Claim(
        id=claim_id,
        patient_pseudonym="P-test",
        provider="Test Clinic",
        payer="Test Plan",
        lines=[
            line("L1", code="96372", charge="30.00"),
            line("L2", code="96372", charge="30.00"),
            line("L3", code="99213", charge="300.00"),
            line("L4", code="77061", charge="80.00"),
        ],
    )


def upload(
    client: TestClient, claims: list[Claim], payer_type: str = "unknown", **extra: Any
) -> httpx.Response:
    return client.post(f"/api/cases?payer_type={payer_type}", json=claims_to_bundle(claims), **extra)
