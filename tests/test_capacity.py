import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.deps import get_reference
from app.main import app
from app.services import capacity
from tests.api_helpers import sample_claim, upload
from tests.helpers import FIXTURE_REF

FULL = {"detail": "the demo is full right now; please try again later"}


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_full_database_blocks_new_workspaces_and_uploads(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[get_reference] = lambda: FIXTURE_REF
    c = TestClient(app)
    assert c.get("/api/workspace").status_code == 200
    monkeypatch.setattr(capacity, "MAX_DB_BYTES", 1)
    assert TestClient(app).get("/api/workspace").json() == FULL
    assert TestClient(app).get("/api/workspace").status_code == 503
    assert upload(c, [sample_claim()]).status_code == 503
    assert c.get("/api/cases").status_code == 200  # existing workspace still reads


def test_new_workspaces_are_capped_per_client_ip(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capacity, "NEW_WORKSPACES_PER_CLIENT", 2)
    ip_a, ip_b = {"x-real-ip": "203.0.113.1"}, {"x-real-ip": "203.0.113.2"}
    kept = TestClient(app, headers=ip_a)
    assert kept.get("/api/workspace").status_code == 200
    assert TestClient(app, headers=ip_a).get("/api/workspace").status_code == 200
    r = TestClient(app, headers=ip_a).get("/api/workspace")
    assert r.status_code == 429
    assert r.json() == {"detail": "too many new demo sessions from your network; please try again later"}
    first_hop_a = {"x-forwarded-for": "203.0.113.1, 10.0.0.1"}
    assert TestClient(app, headers=first_hop_a).get("/api/workspace").status_code == 429
    assert kept.get("/api/workspace").status_code == 200  # a valid cookie is never counted
    assert TestClient(app, headers=ip_b).get("/api/workspace").status_code == 200
    assert TestClient(app).get("/api/workspace").status_code == 200  # falls back to the socket address
    assert not any("203.0.113" in k for k in capacity._new_workspaces)  # only hashes are kept
