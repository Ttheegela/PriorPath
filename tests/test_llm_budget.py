from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db.models import Workspace
from app.services import llm_budget


def test_budget_per_workspace_per_hour(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_budget, "EXPLANATIONS_PER_HOUR", 2)
    now = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    with Session(db) as s:
        a, b = Workspace(), Workspace()
        s.add_all([a, b])
        s.flush()
        assert [llm_budget.try_consume(s, a.id, now) for _ in range(3)] == [True, True, False]
        assert llm_budget.try_consume(s, b.id, now) is True
        assert llm_budget.try_consume(s, a.id, now + timedelta(hours=1)) is True


def test_global_hourly_cap_across_workspaces(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_budget, "EXPLANATIONS_PER_HOUR", 5)
    monkeypatch.setattr(llm_budget, "GLOBAL_EXPLANATIONS_PER_HOUR", 3)
    now = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    with Session(db) as s:
        a, b = Workspace(), Workspace()
        s.add_all([a, b])
        s.flush()
        assert [llm_budget.try_consume(s, a.id, now) for _ in range(2)] == [True, True]
        assert llm_budget.try_consume(s, b.id, now) is True
        assert llm_budget.try_consume(s, b.id, now) is False
        assert llm_budget.try_consume(s, a.id, now + timedelta(hours=1)) is True
