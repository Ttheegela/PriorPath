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
        assert [llm_budget.try_consume(s, a.id, now=now) for _ in range(3)] == [True, True, False]
        assert llm_budget.try_consume(s, b.id, now=now) is True
        assert llm_budget.try_consume(s, a.id, now=now + timedelta(hours=1)) is True


def test_global_hourly_cap_across_workspaces(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_budget, "EXPLANATIONS_PER_HOUR", 5)
    monkeypatch.setattr(llm_budget, "GLOBAL_EXPLANATIONS_PER_HOUR", 3)
    now = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    with Session(db) as s:
        a, b = Workspace(), Workspace()
        s.add_all([a, b])
        s.flush()
        assert [llm_budget.try_consume(s, a.id, now=now) for _ in range(2)] == [True, True]
        assert llm_budget.try_consume(s, b.id, now=now) is True
        assert llm_budget.try_consume(s, b.id, now=now) is False
        assert llm_budget.try_consume(s, a.id, now=now + timedelta(hours=1)) is True


def test_refused_retries_do_not_drain_the_global_budget(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_budget, "EXPLANATIONS_PER_HOUR", 2)
    monkeypatch.setattr(llm_budget, "GLOBAL_EXPLANATIONS_PER_HOUR", 3)
    now = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    with Session(db) as s:
        noisy, other = Workspace(), Workspace()
        s.add_all([noisy, other])
        s.flush()
        # The noisy workspace keeps retrying long after its own cap of 2.
        assert [llm_budget.try_consume(s, noisy.id, now=now) for _ in range(10)] == [True, True] + [False] * 8
        # Its refused attempts count at most 2 toward the global cap of 3, so another workspace still gets 1.
        assert llm_budget.try_consume(s, other.id, now=now) is True
        assert llm_budget.try_consume(s, other.id, now=now) is False


def test_extract_pages_are_a_separate_pool_with_a_shared_global_cap(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_budget, "EXPLANATIONS_PER_HOUR", 2)
    monkeypatch.setattr(llm_budget, "EXTRACT_PAGES_PER_HOUR", 2)
    monkeypatch.setattr(llm_budget, "GLOBAL_EXPLANATIONS_PER_HOUR", 3)
    now = datetime(2026, 10, 2, 14, 30, tzinfo=UTC)
    with Session(db) as s:
        a, b = Workspace(), Workspace()
        s.add_all([a, b])
        s.flush()
        assert [llm_budget.try_consume(s, a.id, "extract", now) for _ in range(3)] == [True, True, False]
        # All page units used, explanations still available from the workspace's own pool...
        assert llm_budget.try_consume(s, a.id, "explain", now) is True
        # ...but the global cap (3) counts both kinds: 2 extract + 1 explain is already 3.
        assert llm_budget.try_consume(s, b.id, "explain", now) is False
        assert llm_budget.try_consume(s, b.id, "extract", now + timedelta(hours=1)) is True
