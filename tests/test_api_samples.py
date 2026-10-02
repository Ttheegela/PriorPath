from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingest.fhir import parse_fhir
from app.main import app
from app.services.demo import DEMO_BILLS


def test_sample_claim_is_a_parseable_fhir_bundle() -> None:
    r = TestClient(app).get("/api/samples/claim.json")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    parsed = parse_fhir(r.json())
    assert len(parsed.claims) == 2 and not parsed.errors


def test_sample_bill_is_a_pdf_attachment() -> None:
    if not any(DEMO_BILLS.glob("*.pdf")):
        pytest.skip("no demo bills built (scripts/build_demo.py --bills)")
    r = TestClient(app).get("/api/samples/bill.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"] and r.content.startswith(b"%PDF")


def test_sample_bill_404_when_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.api.samples.DEMO_BILLS", tmp_path)
    r = TestClient(app).get("/api/samples/bill.pdf")
    assert r.status_code == 404 and "No sample bill" in r.json()["detail"]
