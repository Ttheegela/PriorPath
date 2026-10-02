import os
from decimal import Decimal

import pytest
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from app.db.models import Case, FlagRow, Workspace
from app.db.session import database_url


def test_database_url_rewrites_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h/db?sslmode=require")
    assert database_url() == "postgresql+psycopg://u:p@h/db?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    assert database_url() == "postgresql+psycopg://u:p@h/db"
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(RuntimeError):
        database_url()
    monkeypatch.setenv("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "x"))


def test_migration_creates_tables(db: Engine) -> None:
    names = set(inspect(db).get_table_names())
    assert {
        "workspaces",
        "cases",
        "flags",
        "letters",
        "audit_events",
        "llm_usage",
        "alembic_version",
    } <= names


def test_deleting_workspace_cascades(db: Engine) -> None:
    with Session(db) as s:
        ws = Workspace()
        s.add(ws)
        s.flush()
        case = Case(workspace_id=ws.id, source="fhir", payer_type="medicare", claim={"id": "C1"})
        s.add(case)
        s.flush()
        s.add(
            FlagRow(
                case_id=case.id,
                position=0,
                flag_key="C1:R1:L1+L2",
                rule_id="R1",
                severity="error",
                line_ids=["L1", "L2"],
                evidence={},
                est_overcharge=Decimal("1.00"),
                message="m",
            )
        )
        s.commit()
        assert s.scalars(select(Case)).one().status == "uploaded"
        s.delete(ws)
        s.commit()
        assert s.scalars(select(FlagRow)).all() == []
        assert s.scalars(select(Case)).all() == []
