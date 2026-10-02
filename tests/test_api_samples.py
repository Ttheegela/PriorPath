from fastapi.testclient import TestClient

from app.ingest.fhir import parse_fhir
from app.main import app


def test_sample_claim_is_a_parseable_fhir_bundle() -> None:
    r = TestClient(app).get("/api/samples/claim.json")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    parsed = parse_fhir(r.json())
    assert len(parsed.claims) == 2 and not parsed.errors
