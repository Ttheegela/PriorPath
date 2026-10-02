from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.ingest.fhir import claims_to_bundle
from app.main import app
from tests.api_helpers import sample_claim, upload


def test_upload_creates_one_case_per_claim(db: Engine) -> None:
    c = TestClient(app)
    r = upload(c, [sample_claim("A"), sample_claim("B")])
    assert r.status_code == 201
    body = r.json()
    assert body["errors"] == []
    assert [x["claim_id"] for x in body["cases"]] == ["A", "B"]
    assert body["cases"][0]["status"] == "uploaded" and body["cases"][0]["line_count"] == 4
    with Session(db) as s:
        assert [e.action for e in s.scalars(select(AuditEvent))] == ["case_uploaded", "case_uploaded"]


def test_partial_bundle_keeps_valid_claims_and_reports_paths(db: Engine) -> None:
    bundle = claims_to_bundle([sample_claim("A")])
    bundle["entry"].append({"resource": {"resourceType": "ExplanationOfBenefit", "item": []}})  # type: ignore[union-attr]
    r = TestClient(app).post("/api/cases", json=bundle)
    assert r.status_code == 201
    assert len(r.json()["cases"]) == 1
    assert r.json()["errors"][0]["path"] == "$.entry[1].resource.id"


def test_no_valid_claims_is_422_with_paths(db: Engine) -> None:
    r = TestClient(app).post("/api/cases", json={"resourceType": "Patient"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["path"] == "$.resourceType"


def test_non_json_body_is_422(db: Engine) -> None:
    r = TestClient(app).post("/api/cases", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert r.json()["errors"] == [{"path": "$", "message": "body is not valid JSON"}]


def test_oversized_upload_is_413(db: Engine) -> None:
    r = TestClient(app).post(
        "/api/cases", content=b" " * 4_000_001, headers={"content-type": "application/json"}
    )
    assert r.status_code == 413


def test_invalid_payer_type_is_422(db: Engine) -> None:
    assert upload(TestClient(app), [sample_claim()], payer_type="martian").status_code == 422


def test_list_is_newest_first_and_detail_has_lines(db: Engine) -> None:
    c = TestClient(app)
    upload(c, [sample_claim("A")])
    upload(c, [sample_claim("B")])
    cases = c.get("/api/cases").json()
    assert [x["claim_id"] for x in cases] == ["B", "A"]
    detail = c.get(f"/api/cases/{cases[0]['id']}").json()
    assert [ln["id"] for ln in detail["lines"]] == ["L1", "L2", "L3", "L4"]
    assert detail["lines"][2]["charge"] == "300.00"
    assert detail["flags"] == [] and detail["letter"] is None


def test_other_workspace_cannot_see_case(db: Engine) -> None:
    owner, other = TestClient(app), TestClient(app)
    case_id = upload(owner, [sample_claim()]).json()["cases"][0]["id"]
    assert other.get(f"/api/cases/{case_id}").status_code == 404
    assert other.get("/api/cases").json() == []


def test_deeply_nested_json_is_422(db: Engine) -> None:
    r = TestClient(app).post(
        "/api/cases", content=b"[" * 100000, headers={"content-type": "application/json"}
    )
    assert r.status_code == 422
    assert r.json()["errors"] == [{"path": "$", "message": "body is not valid JSON"}]
