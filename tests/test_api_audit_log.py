from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api import audit_log
from app.api.deps import get_reference
from app.main import app
from tests.api_helpers import sample_claim, upload
from tests.helpers import FIXTURE_REF


def setup_function() -> None:
    app.dependency_overrides[get_reference] = lambda: FIXTURE_REF


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_case_log_is_newest_first_and_scoped_to_the_case(db: Engine) -> None:
    c = TestClient(app)
    first, second = upload(c, [sample_claim("A"), sample_claim("B")]).json()["cases"]
    c.post(f"/api/cases/{first['id']}/audit")
    events = c.get(f"/api/cases/{first['id']}/audit-log").json()
    assert [e["action"] for e in events] == ["audit_run", "case_uploaded"]
    assert {e["case_id"] for e in events} == {first["id"]}
    assert second["id"] not in {e["case_id"] for e in events}


def test_workspace_log_lists_every_case(db: Engine) -> None:
    c = TestClient(app)
    ids = {case["id"] for case in upload(c, [sample_claim("A"), sample_claim("B")]).json()["cases"]}
    assert ids <= {e["case_id"] for e in c.get("/api/audit-log").json()}


def test_other_workspace_cannot_read_a_case_log(db: Engine) -> None:
    owner, stranger = TestClient(app), TestClient(app)
    case_id = upload(owner, [sample_claim()]).json()["cases"][0]["id"]
    assert stranger.get(f"/api/cases/{case_id}/audit-log").status_code == 404
    assert case_id not in {e["case_id"] for e in stranger.get("/api/audit-log").json()}


def test_log_is_capped(db: Engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(audit_log, "AUDIT_LOG_LIMIT", 2)
    c = TestClient(app)
    upload(c, [sample_claim("A"), sample_claim("B"), sample_claim("C")])
    assert len(c.get("/api/audit-log").json()) == 2
