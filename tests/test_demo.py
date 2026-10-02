import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app.db.models import Case, CaseDocument, Workspace
from app.ingest.fhir import parse_fhir
from app.main import app
from app.services.cases import case_flags
from app.services.demo import DEMO_CASES, cleanup_old_workspaces, seed_demo
from evals.pdf_render import render_bill
from tests.api_helpers import sample_claim
from tests.helpers import FIXTURE_REF


def test_demo_file_parses_cleanly() -> None:
    result = parse_fhir(json.loads(DEMO_CASES.read_text()))
    assert result.errors == [] and len(result.claims) == 10


def test_new_workspace_gets_audited_demo_cases(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIORPATH_DEMO", "1")
    c = TestClient(app)
    cases = [x for x in c.get("/api/cases").json() if not x["claim_id"].startswith("B")]  # FHIR only
    assert len(cases) == 10
    assert all(x["status"] == "needs_review" and x["payer_type"] == "medicare" for x in cases)
    assert sum(x["error_count"] > 0 or x["outlier_amount"] != "0.00" for x in cases) >= 7


def test_reset_restores_demo(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIORPATH_DEMO", "1")
    c = TestClient(app)
    first_ids = {x["id"] for x in c.get("/api/cases").json()}
    assert c.post("/api/demo/reset").json()["cases"] >= 10
    assert {x["id"] for x in c.get("/api/cases").json()}.isdisjoint(first_ids)


def test_cleanup_requires_cron_secret_and_deletes_old_workspaces(db: Engine) -> None:
    c = TestClient(app)
    assert c.get("/api/internal/cleanup").status_code == 401
    assert c.get("/api/internal/cleanup", headers={"Authorization": "Bearer wrong"}).status_code == 401
    c.get("/api/workspace")
    with Session(db) as s:
        s.execute(update(Workspace).values(created_at=datetime.now(UTC) - timedelta(hours=25)))
        s.add(Workspace())
        s.commit()
    r = c.get("/api/internal/cleanup", headers={"Authorization": "Bearer test-cron-secret"})
    assert r.json() == {"deleted": 1}
    with Session(db) as s:
        assert len(s.scalars(select(Workspace)).all()) == 1
        assert cleanup_old_workspaces(s) == 0


def test_bad_demo_file_does_not_break_workspace_creation(
    db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "cases.json"
    bad.write_text("not json")
    monkeypatch.setenv("PRIORPATH_DEMO", "1")
    monkeypatch.setattr("app.services.demo.DEMO_CASES", bad)
    c = TestClient(app)
    r = c.get("/api/workspace")
    assert r.status_code == 200 and "pp_ws" in c.cookies
    assert c.get("/api/cases").json() == []


def test_cleanup_non_ascii_or_empty_secret_is_401(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    c = TestClient(app)
    assert c.get("/api/internal/cleanup", headers={"Authorization": "Bearer ñ".encode()}).status_code == 401
    monkeypatch.setenv("CRON_SECRET", "")
    assert c.get("/api/internal/cleanup", headers={"Authorization": "Bearer "}).status_code == 401


def _pdf_demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conf: float) -> None:
    bills = tmp_path / "bills"
    bills.mkdir()
    (bills / "B0001.pdf").write_bytes(render_bill(sample_claim(), "table"))
    lines = [
        ln.model_copy(update={"confidence": conf}).model_dump(mode="json") for ln in sample_claim().lines
    ]
    rec = {"B0001": {"lines": lines, "errors": [], "provider": "Test Clinic", "payer": "Test Plan"}}
    (tmp_path / "pdf_extractions.json").write_text(json.dumps(rec))
    monkeypatch.setattr("app.services.demo.DEMO_BILLS", bills)
    monkeypatch.setattr("app.services.demo.DEMO_EXTRACTIONS", tmp_path / "pdf_extractions.json")


def _seed(db: Engine) -> list[Case]:
    with Session(db) as s:
        ws = Workspace()
        s.add(ws)
        s.flush()
        seed_demo(s, ws, FIXTURE_REF)
        s.commit()
        return list(s.scalars(select(Case).where(Case.source == "pdf")))


def test_pdf_demo_case_is_audited_with_its_document(
    db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pdf_demo(tmp_path, monkeypatch, 0.99)
    (case,) = _seed(db)
    with Session(db) as s:
        doc = s.scalar(select(CaseDocument).where(CaseDocument.case_id == case.id))
        assert doc is not None and doc.page_count == 1 and doc.content.startswith(b"%PDF")
        assert case.status == "needs_review" and case_flags(s, case.id)


def test_pdf_demo_low_confidence_line_needs_line_review(
    db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pdf_demo(tmp_path, monkeypatch, 0.5)
    (case,) = _seed(db)
    assert case.status == "needs_line_review"


def test_missing_recording_or_bill_seeds_fhir_demo_only(
    db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("app.services.demo.DEMO_EXTRACTIONS", tmp_path / "none.json")
    assert _seed(db) == []
    _pdf_demo(tmp_path, monkeypatch, 0.99)
    monkeypatch.setattr("app.services.demo.DEMO_BILLS", tmp_path / "no-bills")
    assert _seed(db) == []


def test_corrupt_extraction_drops_only_that_bill(
    db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pdf_demo(tmp_path, monkeypatch, 0.99)
    rec = json.loads((tmp_path / "pdf_extractions.json").read_text())
    rec["B0002"] = {"lines": [{"id": "x"}], "errors": []}
    (tmp_path / "bills" / "B0002.pdf").write_bytes(b"%PDF-1.4 broken")
    (tmp_path / "pdf_extractions.json").write_text(json.dumps({"B0002": rec["B0002"], "B0001": rec["B0001"]}))
    with Session(db) as s:
        ws = Workspace()
        s.add(ws)
        s.flush()
        assert seed_demo(s, ws, FIXTURE_REF) == 11
        s.commit()
        cases = list(s.scalars(select(Case)))
    assert len(cases) == 11 and [c.claim["id"] for c in cases if c.source == "pdf"] == ["B0001"]


def test_committed_demo_seeds_every_recorded_bill(db: Engine) -> None:
    rec_path = DEMO_CASES.parent / "pdf_extractions.json"
    if not rec_path.exists():
        pytest.skip("data/demo/pdf_extractions.json not committed yet")
    n = len(json.loads(rec_path.read_text()))
    with Session(db) as s:
        ws = Workspace()
        s.add(ws)
        s.flush()
        assert seed_demo(s, ws, FIXTURE_REF) == 10 + n
        s.commit()
        pdf = list(s.scalars(select(Case).where(Case.source == "pdf")))
        assert len(pdf) == n
        assert all(s.scalar(select(CaseDocument).where(CaseDocument.case_id == c.id)) for c in pdf)
