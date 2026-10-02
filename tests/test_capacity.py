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
