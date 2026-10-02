import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app.db.models import Workspace
from app.ingest.fhir import parse_fhir
from app.main import app
from app.services.demo import DEMO_CASES, cleanup_old_workspaces


def test_demo_file_parses_cleanly() -> None:
    result = parse_fhir(json.loads(DEMO_CASES.read_text()))
    assert result.errors == [] and len(result.claims) == 10


def test_new_workspace_gets_audited_demo_cases(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIORPATH_DEMO", "1")
    c = TestClient(app)
    cases = c.get("/api/cases").json()
    assert len(cases) == 10
    assert all(x["status"] == "needs_review" and x["payer_type"] == "medicare" for x in cases)
    assert sum(x["error_count"] > 0 or x["outlier_amount"] != "0.00" for x in cases) >= 7


def test_reset_restores_demo(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIORPATH_DEMO", "1")
    c = TestClient(app)
    first_ids = {x["id"] for x in c.get("/api/cases").json()}
    assert c.post("/api/demo/reset").json() == {"cases": 10}
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
