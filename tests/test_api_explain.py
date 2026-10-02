import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.deps import get_llm, get_reference
from app.main import app
from app.services import llm_budget
from tests.api_helpers import sample_claim, upload
from tests.fakes import FakeLLM
from tests.helpers import FIXTURE_REF

OK = "This line repeats another charge for the same service on the same date."


def setup(llm: FakeLLM | None) -> tuple[TestClient, str]:
    app.dependency_overrides[get_reference] = lambda: FIXTURE_REF
    app.dependency_overrides[get_llm] = lambda: llm
    c = TestClient(app)
    case_id = upload(c, [sample_claim()]).json()["cases"][0]["id"]
    c.post(f"/api/cases/{case_id}/audit")
    return c, case_id


def teardown_function() -> None:
    app.dependency_overrides.clear()


def events(text: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(ln.split(": ", 1) for ln in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_stream_explains_every_pending_flag(db: Engine) -> None:
    c, case_id = setup(FakeLLM([OK] * 10))
    r = c.post(f"/api/cases/{case_id}/explain")
    assert r.headers["content-type"].startswith("text/event-stream")
    ev = events(r.text)
    assert ev[0] == ("start", {"pending": 3})
    assert [e[1]["status"] for e in ev if e[0] == "explanation"] == ["ready"] * 3
    assert ev[-1] == ("done", {"ready": 3, "unavailable": 0})
    flags = c.get(f"/api/cases/{case_id}").json()["flags"]
    assert all(f["explanation"] == OK for f in flags if f["explanation_status"] == "ready")
    assert events(c.post(f"/api/cases/{case_id}/explain").text)[0] == ("start", {"pending": 0})


def test_unconfigured_llm_marks_unavailable_and_can_retry(db: Engine) -> None:
    c, case_id = setup(None)
    ev = events(c.post(f"/api/cases/{case_id}/explain").text)
    reasons = {e[1]["reason"] for e in ev if e[0] == "explanation"}
    assert reasons == {"explanations are not configured on this server"}
    app.dependency_overrides[get_llm] = lambda: FakeLLM([OK] * 10)
    assert events(c.post(f"/api/cases/{case_id}/explain").text)[-1] == (
        "done",
        {"ready": 3, "unavailable": 0},
    )


def test_llm_failure_does_not_break_the_stream(db: Engine) -> None:
    c, case_id = setup(FakeLLM(error=ConnectionError("down")))
    ev = events(c.post(f"/api/cases/{case_id}/explain").text)
    assert ev[-1] == ("done", {"ready": 0, "unavailable": 3})
    assert c.get(f"/api/cases/{case_id}").json()["flags"][0]["status"] == "open"


def test_budget_limit_reported(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_budget, "EXPLANATIONS_PER_HOUR", 1)
    c, case_id = setup(FakeLLM([OK] * 10))
    ev = [e[1] for e in events(c.post(f"/api/cases/{case_id}/explain").text) if e[0] == "explanation"]
    assert [e["status"] for e in ev] == ["ready", "unavailable", "unavailable"]
    assert ev[1]["reason"] == "hourly explanation limit reached; try again later"


def test_other_workspace_cannot_stream(db: Engine) -> None:
    _, case_id = setup(FakeLLM([OK]))
    assert TestClient(app).post(f"/api/cases/{case_id}/explain").status_code == 404
