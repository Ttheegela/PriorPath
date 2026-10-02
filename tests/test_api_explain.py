import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api import cases as cases_api
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
    assert ev[-1] == ("done", {"ready": 3, "unavailable": 0, "remaining": 0})
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
        {"ready": 3, "unavailable": 0, "remaining": 0},
    )


def test_llm_failure_does_not_break_the_stream(db: Engine) -> None:
    c, case_id = setup(FakeLLM(error=ConnectionError("down")))
    ev = events(c.post(f"/api/cases/{case_id}/explain").text)
    assert ev[-1] == ("done", {"ready": 0, "unavailable": 3, "remaining": 0})
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


def test_failure_reasons_are_distinguished(db: Engine) -> None:
    c, case_id = setup(FakeLLM(error=ConnectionError("down")))
    ev = events(c.post(f"/api/cases/{case_id}/explain").text)
    assert {e[1]["reason"] for e in ev if e[0] == "explanation"} == {"explanation model unavailable"}
    c, case_id = setup(FakeLLM(["It is $9,999."] * 10))
    ev = events(c.post(f"/api/cases/{case_id}/explain").text)
    assert {e[1]["reason"] for e in ev if e[0] == "explanation"} == {
        "could not produce an explanation grounded in the evidence"
    }


def test_deadline_stops_new_flags_and_reports_remaining(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cases_api, "EXPLAIN_DEADLINE_SECONDS", 0)
    c, case_id = setup(FakeLLM([OK] * 10))
    ev = events(c.post(f"/api/cases/{case_id}/explain").text)
    assert [e[0] for e in ev] == ["start", "done"]
    assert ev[-1][1]["remaining"] == 3


def test_unexpected_error_emits_error_event_and_continues(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db.models import AuditEvent

    def boom(*a: object, **k: object) -> tuple[str, str | None]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cases_api, "explain_row", boom)
    c, case_id = setup(FakeLLM([OK] * 10))
    ev = events(c.post(f"/api/cases/{case_id}/explain").text)
    kinds = [e[0] for e in ev]
    assert kinds == ["start", "error", "error", "error", "done"]
    assert ev[1][1]["reason"] == "internal error" and ev[1][1]["flag_id"]
    assert ev[-1][1]["remaining"] == 0
    with Session(db) as s:
        assert s.scalars(select(AuditEvent).where(AuditEvent.action == "explanations_generated")).first()


def test_usage_row_lock_released_before_llm_call(db: Engine) -> None:
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session

    from app.db.models import FlagRow, Workspace
    from app.services import llm_budget
    from app.services.explanations import explain_row

    c, case_id = setup(FakeLLM([OK]))
    with Session(db) as s:
        row = s.scalars(select(FlagRow).where(FlagRow.explanation_status == "pending")).first()
        ws_id = s.scalars(select(Workspace.id)).first()
        assert row is not None and ws_id is not None
        seen: list[bool] = []

        class Probe:
            def complete(self, system: str, user: str) -> str:
                with Session(db) as other:
                    other.execute(text("set local lock_timeout = '300ms'"))
                    seen.append(llm_budget.try_consume(other, ws_id))
                return OK

        explain_row(s, ws_id, row, "EOB-1", Probe())
    assert seen == [True]
