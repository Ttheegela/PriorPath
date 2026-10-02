import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://priorpath:priorpath@localhost:5433/priorpath_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")
os.environ.setdefault("PRIORPATH_DEMO", "0")
os.environ.pop("OPENROUTER_API_KEY", None)

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import Engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.db.session import get_engine  # noqa: E402

TABLES = "workspaces, cases, flags, letters, audit_events, llm_usage"


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[Engine]:
    engine = get_engine()
    try:
        with engine.connect():
            pass
    except OperationalError:
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail("Postgres is required but not reachable at TEST_DATABASE_URL")
        pytest.skip("Postgres not running: docker compose up -d db")
    command.upgrade(Config("alembic.ini"), "head")
    yield engine


@pytest.fixture
def db(migrated_db: Engine) -> Iterator[Engine]:
    yield migrated_db
    with migrated_db.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
