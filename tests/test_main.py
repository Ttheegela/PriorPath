from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from app import main
from app.main import app, mount_frontend

client = TestClient(app)


def test_health_checks_db_and_reference(db: Engine) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["reference"] == [v["ref_version"] for v in client.get("/api/version").json()["reference"]]
    assert body["reference"]


def test_health_503_without_leaking_when_db_down(monkeypatch: pytest.MonkeyPatch) -> None:
    class DownEngine:
        def connect(self) -> None:
            raise OperationalError("SELECT 1", {}, Exception("postgresql://user:pw@host/db refused"))

    monkeypatch.setattr(main, "get_engine", lambda: DownEngine())
    resp = client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "db": "unavailable"}
    assert "postgresql" not in resp.text


def test_health_503_when_database_url_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db.session import get_engine

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(main, "get_engine", get_engine.__wrapped__)  # uncached: reads the env now
    resp = client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "db": "unavailable"}


def test_docs_served_under_api() -> None:
    assert client.get("/api/docs").status_code == 200


def test_version_reports_loaded_reference() -> None:
    body = client.get("/api/version").json()
    assert body["version"].startswith("2.")
    kinds = {r["kind"] for r in body["reference"]}
    assert kinds == {"ncci", "mue", "pfs"}
    assert all(r["valid_from"] <= r["valid_to"] for r in body["reference"])


def test_frontend_served_without_shadowing_api(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>PriorPath</h1>")
    spa = FastAPI()

    @spa.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    mount_frontend(spa, tmp_path)
    c = TestClient(spa)
    assert "PriorPath" in c.get("/").text
    assert c.get("/api/health").json() == {"status": "ok"}
    assert c.get("/api/does-not-exist").json() == {"detail": "Not Found"}


def test_missing_frontend_dir_is_skipped(tmp_path: Path) -> None:
    api_only = FastAPI()
    mount_frontend(api_only, tmp_path / "nope")
    assert TestClient(api_only).get("/").status_code == 404
