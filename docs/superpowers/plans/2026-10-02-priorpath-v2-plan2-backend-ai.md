# PriorPath v2 — Plan 2: Backend, Database and AI Explanations

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A usable audit API on the live site: upload FHIR claims, run the rule engine, stream grounded AI explanations, review flags, and draft/approve/export a dispute letter — per-visitor demo workspaces, Postgres persistence, audit log — all testable through `/api/docs`.

**Architecture:** FastAPI routers (`app/api/`) stay thin and call services (`app/services/`) that own database work through SQLAlchemy 2.0 (sync, psycopg 3, Neon in production, Postgres 17 in Docker for tests). Rules from Plan 1 are untouched except a payer-aware R4. The LLM layer (`app/llm/`) is a small LangGraph loop (draft → numeric-grounding check → one retry) behind an `LLMClient` protocol, with OpenRouter as the provider and a fake client in tests. Letters are built by deterministic templates from accepted flags; no LLM writes numbers.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0, psycopg 3, Alembic, Postgres 17 (Docker) / Neon, LangGraph, openai SDK → OpenRouter (`deepseek/deepseek-v4-pro`), itsdangerous (signed cookies), python-docx, pytest, ruff, mypy strict, Vercel (Python, Fluid Compute, Cron).

**Spec:** `docs/superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md` — this plan implements §6 (explanations, letters), §7 (workflow, API, demo), §9, §10 (secrets, retention), §11 (integration tests), the week-2 row of §13, and §16 items 1–2. Plan 3 = React reviewer UI. Plan 4 = PDF path, Presidio redaction, Langfuse, docs/README rewrite, §16 items 3–5.

## Deviations from the spec (for the reviewer to approve)

1. **Reference data stays in the committed CSV subset, loaded in memory**, not copied into Postgres tables. It is read-only and versioned in git; a database copy adds nothing until users can load their own releases.
2. **Rule text is a fixed dictionary keyed by rule id**, not pgvector retrieval. There are six rules; exact text is more faithful than nearest-neighbour search. Revisit when the rule corpus grows.
3. **Running rules and generating explanations are two endpoints**: `POST /api/cases/{id}/audit` (fast JSON) and `POST /api/cases/{id}/explain` (SSE stream). Same behavior as the spec's single streamed audit, simpler to test and retry.
4. **One case per claim.** A bundle with N ExplanationOfBenefit resources creates N cases.
5. **Letter export is DOCX and plain text.** PDF comes with the UI (browser print) in Plan 3.
6. **`PATCH /api/cases/{id}/lines` (PDF line review) and Langfuse tracing move to Plan 4** with the PDF path.

## Global Constraints

- Python `3.12`; Postgres `17` for tests (Docker locally, GitHub Actions service in CI); Neon in production.
- Money is `decimal.Decimal`; DB column `NUMERIC(12,2)`; JSON responses carry money as strings.
- Rules decide flags; the LLM only explains. An explanation is stored only if every number in it appears in the flag's evidence, message, rule text or overcharge (small integers 0–10 allowed). Letters are template-built from accepted flags only; edits may not introduce new numbers.
- Never send anything to the LLM except the flag (rule id, severity, message, evidence row, overcharge) and fixed rule text — no patient pseudonym, provider or payer names.
- Workspace isolation: every case/flag/letter route resolves ownership through the visitor's signed `pp_ws` cookie; another workspace's ids return 404.
- Uploads: JSON only, at most `4_000_000` bytes → otherwise 413. Invalid JSON or zero valid claims → 422 with `{path, message}` errors; partial success → 201 with created cases plus errors.
- LLM budget: `20` explanations per workspace per clock hour (`EXPLANATIONS_PER_HOUR`). Default explain model: `deepseek/deepseek-v4-pro` (cheap, near-Sonnet open-weights model; env `EXPLAIN_MODEL` overrides). The final choice is confirmed in Task 10 by grounding-pass rate on the demo flags.
- Demo workspaces older than 24 h are deleted by the daily cron `GET /api/internal/cleanup` (header `Authorization: Bearer $CRON_SECRET`).
- Secrets only from environment variables: `DATABASE_URL`, `OPENROUTER_API_KEY`, `SESSION_SECRET`, `CRON_SECRET`. Never log, print or commit them.
- Serverless: `NullPool` engines; no in-process state except read-only caches (reference data, demo explanations).
- Runtime dependencies are listed in BOTH `requirements.txt` and `pyproject.toml [project].dependencies` (Vercel reads the latter) and must stay identical.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A bundle where some EOBs are valid and some are not**: valid ones become cases, the rest come back as path errors with HTTP 201 — not a 500 and not all-or-nothing. → test in Task 4.
2. **Two visitors at once**: visitor B gets 404 for visitor A's case, flag and letter ids, including on PATCH/POST routes. → tests in Tasks 4, 5, 8.
3. **The model returns an explanation containing a number that isn't in the evidence** (e.g. a made-up dollar amount): it is retried once with feedback and, if still ungrounded, never stored. → test in Task 6.
4. **The LLM is down, unconfigured, or over budget**: the explain stream still completes, affected flags are marked `unavailable` with a reason, and can be retried later; rules and reviews keep working. → test in Task 7.
5. **A reviewer edits a draft letter and changes an amount**: the edit is rejected with 422 naming the new number; wording-only edits are accepted. → test in Task 8.

---

## File Structure

```
app/
├── main.py                      app + routers; /api/health, /api/version
├── api/
│   ├── __init__.py
│   ├── deps.py                  SessionDep, WorkspaceDep, RefDep, LLMDep, get_reference, current_workspace
│   ├── schemas.py               request/response models
│   ├── workspace.py             GET /api/workspace
│   ├── cases.py                 POST/GET cases, POST audit, POST explain (SSE)
│   ├── flags.py                 PATCH /api/flags/{id}
│   ├── letters.py               letter draft/edit/approve/export
│   └── demo.py                  POST /api/demo/reset, GET /api/internal/cleanup
├── db/
│   ├── __init__.py
│   ├── models.py                Base + 6 tables
│   └── session.py               database_url, get_engine, get_session
├── services/
│   ├── __init__.py
│   ├── audit_log.py             record()
│   ├── cases.py                 create_cases, case_flags, flag_from_row, summarize, get_case_or_404
│   ├── audit_run.py             run_audit()
│   ├── llm_budget.py            try_consume()
│   ├── explanations.py          explain_row()
│   ├── letters.py               build_letter()
│   └── demo.py                  demo_enabled, seed_demo, reset_demo, cleanup_old_workspaces
├── llm/
│   ├── __init__.py
│   ├── client.py                LLMClient protocol, OpenRouterClient, default_client
│   ├── grounding.py             numbers_in, unsupported_numbers
│   ├── rule_text.py             RULE_TEXT
│   ├── explain.py               LangGraph draft→check→retry; explain_flag
│   └── cache.py                 demo explanation cache
└── rules/
    ├── __init__.py              + PayerType, RuleConfig.payer_type
    ├── invalid_code.py          payer-aware R4
    └── totals.py                case_totals (per-line cap)
alembic.ini, migrations/         Alembic env + generated initial revision
docker-compose.yml               Postgres 17 for local tests
data/demo/cases.json             10 synthetic demo claims (FHIR bundle)
data/demo/explanations.json      precomputed grounded explanations (built in Task 10)
scripts/build_demo.py            builds data/demo/*
scripts/smoke.py                 end-to-end check against a deployed URL
tests/conftest.py                DB fixtures (skip locally if no DB; fail in CI)
tests/fakes.py                   FakeLLM
```

---

### Task 1: Database foundation (models, sessions, Alembic, test DB, CI service)

**Files:**
- Modify: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `.github/workflows/ci.yml`
- Create: `docker-compose.yml`, `app/db/__init__.py` (empty), `app/db/session.py`, `app/db/models.py`, `alembic.ini`, `migrations/` (via `alembic init`), `migrations/versions/<rev>_initial_schema.py` (generated), `tests/conftest.py`, `tests/test_db.py`

**Interfaces:**
- Produces:
  - `app.db.session.database_url() -> str` (rewrites `postgres://`/`postgresql://` to `postgresql+psycopg://`; raises `RuntimeError` if unset)
  - `app.db.session.get_engine() -> Engine` (cached, `NullPool`)
  - `app.db.session.get_session() -> Iterator[Session]` (FastAPI dependency; `expire_on_commit=False`)
  - `app.db.models`: `Base`, `Workspace(id, created_at)`, `Case(id, workspace_id, status, source, payer_type, claim, created_at)`, `FlagRow(id, case_id, position, flag_key, rule_id, severity, line_ids, evidence, est_overcharge, message, explanation, explanation_status, status, reject_reason)`, `Letter(id, case_id, flag_ids, body, status, created_at, approved_at)`, `AuditEvent(id, workspace_id, case_id, actor, action, detail, ref_versions, at)`, `LlmUsage(workspace_id, hour_start, calls)`
  - pytest fixtures `migrated_db` (session) and `db` (function; truncates all tables after each test) in `tests/conftest.py`

- [ ] **Step 1: Dependencies**

Append to `requirements.txt` (keep existing lines):
```
sqlalchemy>=2.0.30,<2.1
psycopg[binary]>=3.2,<4
langgraph>=1.2,<2
openai>=1.40
itsdangerous>=2.2,<3
python-docx>=1.1,<2
```
Make `pyproject.toml` `[project].dependencies` list exactly the same eight entries (fastapi, pydantic + these six). Append to `requirements-dev.txt`: `alembic>=1.13,<2`.
Add to `pyproject.toml`:
```toml
[tool.ruff]
extend-exclude = ["migrations/versions"]
```
(merge `extend-exclude` into the existing `[tool.ruff]` table) and extend the mypy override module list to `["openpyxl", "openpyxl.*", "docx", "docx.*"]`.
Run `source .venv/bin/activate && uv pip install -r requirements-dev.txt`.

- [ ] **Step 2: Local Postgres** — `docker-compose.yml`

```yaml
services:
  db:
    image: postgres:17
    environment:
      POSTGRES_USER: priorpath
      POSTGRES_PASSWORD: priorpath
      POSTGRES_DB: priorpath_test
    ports:
      - "5433:5432"
```
Run `docker compose up -d db`. Expected: container running; `docker compose ps` shows `db` up.

- [ ] **Step 3: Write the failing tests** — `tests/conftest.py`

```python
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
```

`tests/test_db.py`:
```python
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
    assert {"workspaces", "cases", "flags", "letters", "audit_events", "llm_usage", "alembic_version"} <= names


def test_deleting_workspace_cascades(db: Engine) -> None:
    with Session(db) as s:
        ws = Workspace()
        s.add(ws)
        s.flush()
        case = Case(workspace_id=ws.id, source="fhir", payer_type="medicare", claim={"id": "C1"})
        s.add(case)
        s.flush()
        s.add(FlagRow(case_id=case.id, position=0, flag_key="C1:R1:L1+L2", rule_id="R1", severity="error",
                      line_ids=["L1", "L2"], evidence={}, est_overcharge=Decimal("1.00"), message="m"))
        s.commit()
        assert s.scalars(select(Case)).one().status == "uploaded"
        s.delete(ws)
        s.commit()
        assert s.scalars(select(FlagRow)).all() == []
        assert s.scalars(select(Case)).all() == []
```
Note: `test_database_url_rewrites_driver` restores the test URL at the end; `get_engine` is cached, so it is unaffected.

- [ ] **Step 4: Run to verify failure**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`.

- [ ] **Step 5: Implement** — `app/db/session.py`

```python
import os
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    # Neon and Vercel hand out postgres:// URLs; SQLAlchemy needs the psycopg 3 driver name.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    # ponytail: NullPool — serverless instances don't keep pools warm; Neon's pooled URL does the pooling.
    return create_engine(database_url(), poolclass=NullPool)


def get_session() -> Iterator[Session]:
    with sessionmaker(get_engine(), expire_on_commit=False)() as session:
        yield session
```

`app/db/models.py`:
```python
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _uuid_pk()
    created_at: Mapped[datetime] = _created_at()


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    source: Mapped[str] = mapped_column(String(8))
    payer_type: Mapped[str] = mapped_column(String(16))
    claim: Mapped[dict[str, Any]] = mapped_column(JSONB)  # Claim.model_dump(mode="json")
    created_at: Mapped[datetime] = _created_at()


class FlagRow(Base):
    __tablename__ = "flags"
    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    flag_key: Mapped[str] = mapped_column(String(300))  # the engine's Flag.id
    rule_id: Mapped[str] = mapped_column(String(8))
    severity: Mapped[str] = mapped_column(String(16))
    line_ids: Mapped[list[str]] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    est_overcharge: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    message: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text, default=None)
    explanation_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|ready|unavailable|none
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|accepted|rejected
    reject_reason: Mapped[str | None] = mapped_column(Text, default=None)


class Letter(Base):
    __tablename__ = "letters"
    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    flag_ids: Mapped[list[str]] = mapped_column(JSONB)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|approved
    created_at: Mapped[datetime] = _created_at()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True, default=None
    )
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ref_versions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    at: Mapped[datetime] = _created_at()


class LlmUsage(Base):
    __tablename__ = "llm_usage"
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    hour_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 6: Alembic**

```bash
alembic init migrations
```
Edit `migrations/env.py`: add near the top
```python
from app.db.models import Base
from app.db.session import database_url
```
set `target_metadata = Base.metadata`, and in BOTH `run_migrations_offline` and `run_migrations_online` use `database_url()` instead of `config.get_main_option("sqlalchemy.url")` (online: `connectable = create_engine(database_url(), poolclass=pool.NullPool)`; import `create_engine` from sqlalchemy). Leave `sqlalchemy.url` in `alembic.ini` empty. Then generate the schema against the Docker DB:
```bash
DATABASE_URL=postgresql+psycopg://priorpath:priorpath@localhost:5433/priorpath_test \
  alembic revision --autogenerate -m "initial schema"
```
Open the generated file and confirm it creates exactly the six tables with the columns above, the foreign keys with `ondelete="CASCADE"`, and the indexes on `workspace_id`/`case_id`. Fix nothing by hand unless autogenerate missed something; if it did, say so in your report.

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_db.py -v && pytest -q`
Expected: 3 new tests pass; full suite passes (DB tests run against Docker).

- [ ] **Step 8: CI Postgres service** — in `.github/workflows/ci.yml` add under the `backend` job (before `steps:`):

```yaml
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_USER: priorpath
          POSTGRES_PASSWORD: priorpath
          POSTGRES_DB: priorpath_test
        ports:
          - 5433:5432
        options: >-
          --health-cmd "pg_isready -U priorpath"
          --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      TEST_DATABASE_URL: postgresql+psycopg://priorpath:priorpath@localhost:5433/priorpath_test
      REQUIRE_DB: "1"
```

- [ ] **Step 9: Commit**

```bash
ruff format app tests && ruff check . && mypy app evals scripts
git add requirements.txt requirements-dev.txt pyproject.toml docker-compose.yml app/db alembic.ini migrations \
  tests/conftest.py tests/test_db.py .github/workflows/ci.yml
git commit -m "feat: Postgres schema with SQLAlchemy 2.0 + Alembic, test DB fixtures, CI service

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Payer-aware R4 and per-line overcharge totals (§16 items 1–2)

**Files:**
- Modify: `app/rules/__init__.py`, `app/rules/invalid_code.py`, `tests/test_rule_code_price.py`
- Create: `app/rules/totals.py`, `tests/test_totals.py`

**Interfaces:**
- Consumes: `Claim`, `Flag`, `Severity`, `make_flag`, `money` (Plan 1); `INVALID_STATUSES`.
- Produces:
  - `app.rules.PayerType = Literal["medicare", "commercial", "unknown"]`
  - `RuleConfig(price_multiplier: Decimal = Decimal("3"), payer_type: PayerType = "medicare")` — the default keeps Plan 1's eval unchanged
  - R4: status `I` on a non-Medicare payer → `Severity.LEAD`, `est_overcharge` 0; status `D` always `ERROR`
  - `app.rules.totals.Totals(errors: Decimal, outliers: Decimal)`; `case_totals(claim: Claim, flags: Iterable[Flag]) -> Totals`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rule_code_price.py`:
```python
def test_status_i_is_a_lead_for_non_medicare_payers() -> None:
    for payer in ("commercial", "unknown"):
        (flag,) = check_invalid_code(
            claim(line("L1", code="77061", charge="80.00")), FIXTURE_REF, RuleConfig(payer_type=payer)
        )
        assert flag.severity is Severity.LEAD, payer
        assert flag.est_overcharge == Decimal("0.00")
        assert "confirm" in flag.message


def test_status_d_stays_an_error_for_any_payer() -> None:
    (flag,) = check_invalid_code(claim(line("L1", code="99201")), FIXTURE_REF, RuleConfig(payer_type="commercial"))
    assert flag.severity is Severity.ERROR
```

`tests/test_totals.py`:
```python
from decimal import Decimal

from app.models import Evidence, Flag, Severity, make_flag
from app.rules.totals import case_totals
from tests.helpers import claim, line

EV = Evidence(table="t", ref_version=None, row={})


def flag(c, rule: str, sev: Severity, ids: list[str], amount: str) -> Flag:  # type: ignore[no-untyped-def]
    lines = [x for x in c.lines if x.id in ids]
    return make_flag(c, rule, sev, sorted(lines, key=lambda x: ids.index(x.id)), EV, Decimal(amount), "m")


def test_error_and_outlier_on_same_line_are_capped_at_its_charge() -> None:
    c = claim(line("L1", code="93000", charge="50.00"), line("L2", code="93005", charge="30.00"))
    flags = [flag(c, "R2", Severity.ERROR, ["L1", "L2"], "30.00"), flag(c, "R5", Severity.OUTLIER, ["L2"], "20.00")]
    t = case_totals(c, flags)
    assert (t.errors, t.outliers) == (Decimal("30.00"), Decimal("0.00"))


def test_two_errors_on_one_line_never_exceed_the_line() -> None:
    c = claim(line("L1", code="99201", charge="100.00"), line("L2", code="93005", charge="100.00"))
    flags = [flag(c, "R4", Severity.ERROR, ["L2"], "100.00"), flag(c, "R2", Severity.ERROR, ["L1", "L2"], "100.00")]
    assert case_totals(c, flags).errors == Decimal("100.00")


def test_duplicates_count_each_extra_line() -> None:
    c = claim(line("L1", charge="100.00"), line("L2", charge="140.00"), line("L3", charge="160.00"))
    assert case_totals(c, [flag(c, "R1", Severity.ERROR, ["L1", "L2", "L3"], "300.00")]).errors == Decimal("300.00")


def test_day_level_mue_spreads_by_charge() -> None:
    c = claim(line("L1", code="97110", units=4, charge="120.00"), line("L2", code="97110", units=3, charge="90.00"))
    assert case_totals(c, [flag(c, "R3", Severity.ERROR, ["L1", "L2"], "30.00")]).errors == Decimal("30.00")


def test_outliers_counted_separately_and_leads_notices_ignored() -> None:
    c = claim(line("L1", charge="300.00"), line("L2", code="77061", charge="80.00"))
    flags = [
        flag(c, "R5", Severity.OUTLIER, ["L1"], "23.55"),
        flag(c, "R4", Severity.LEAD, ["L2"], "0"),
        flag(c, "R0", Severity.NOTICE, ["L2"], "0"),
    ]
    t = case_totals(c, flags)
    assert (t.errors, t.outliers) == (Decimal("0.00"), Decimal("23.55"))
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_totals.py tests/test_rule_code_price.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rules.totals'` and `TypeError` on `RuleConfig(payer_type=...)`.

- [ ] **Step 3: Implement**

In `app/rules/__init__.py` add `from typing import Literal`, define `PayerType = Literal["medicare", "commercial", "unknown"]` above `RuleConfig`, and add the field `payer_type: PayerType = "medicare"` to `RuleConfig`.

Replace `app/rules/invalid_code.py` with:
```python
from decimal import Decimal

from app.models import Claim, Evidence, Flag, Severity, make_flag
from app.reference.base import INVALID_STATUSES, Reference
from app.rules import RuleConfig


def check_invalid_code(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    flags = []
    for line in claim.lines:
        if not ref.covers(line.date_of_service):
            continue
        status = ref.code_status(line.code, line.date_of_service)
        if status is None or status.status not in INVALID_STATUSES:
            continue
        dos = line.date_of_service.isoformat()
        severity, amount = Severity.ERROR, line.charge
        if status.status == "D":
            message = f"{line.code} is a deleted code and was not billable on {dos}"
        elif config.payer_type == "medicare":
            message = f"{line.code} is not valid for Medicare billing on {dos}; Medicare requires a different code"
        else:
            # Status I is Medicare-specific: commercial plans often accept these CPT codes.
            severity, amount = Severity.LEAD, Decimal("0")
            message = (
                f"{line.code} is not valid for Medicare billing on {dos}; a {config.payer_type} plan may still "
                "accept it, so confirm with the plan before disputing"
            )
        flags.append(make_flag(
            claim, "R4", severity, [line],
            Evidence(table="pfs_status", ref_version=status.ref_version, row={"code": line.code, "status": status.status}),
            amount, message,
        ))
    return flags
```
(Check the current file first: keep any existing behavior the brief's tests rely on — the D and Medicare-I messages above are the ones Plan 1 tests assert.)

`app/rules/totals.py`:
```python
"""Case-level overcharge totals that never count more than a line was billed."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.models import Claim, Flag, Severity, money


@dataclass(frozen=True)
class Totals:
    errors: Decimal
    outliers: Decimal


def _allocate(flag: Flag, charges: dict[str, Decimal]) -> dict[str, Decimal]:
    ids = [i for i in flag.line_ids if i in charges]
    if not ids:
        return {}
    if flag.rule_id == "R1":  # every line after the first is the extra copy
        return {i: charges[i] for i in ids[1:]}
    if flag.rule_id == "R2":  # the column-2 (last) line is the one that shouldn't be billed
        return {ids[-1]: flag.est_overcharge}
    total = sum((charges[i] for i in ids), Decimal("0"))
    if total == 0:
        return {}
    return {i: flag.est_overcharge * charges[i] / total for i in ids}


def case_totals(claim: Claim, flags: Iterable[Flag]) -> Totals:
    charges = {line.id: line.charge for line in claim.lines}
    errors: defaultdict[str, Decimal] = defaultdict(Decimal)
    outliers: defaultdict[str, Decimal] = defaultdict(Decimal)
    for f in flags:
        bucket = errors if f.severity is Severity.ERROR else outliers if f.severity is Severity.OUTLIER else None
        if bucket is None:
            continue
        for line_id, amount in _allocate(f, charges).items():
            bucket[line_id] += amount
    total_errors = total_outliers = Decimal("0")
    for line_id, charge in charges.items():
        e = min(errors[line_id], charge)
        total_errors += e
        total_outliers += min(outliers[line_id], charge - e)
    return Totals(money(total_errors), money(total_outliers))
```

- [ ] **Step 4: Run tests and the eval**

Run: `pytest -q && python -m evals.run --n 300 --seed 7 && git diff --exit-code evals/results`
Expected: all pass; eval still 1.000 everywhere (default payer is Medicare); results file unchanged.

- [ ] **Step 5: Commit**

```bash
ruff format app tests && ruff check . && mypy app evals scripts
git add app/rules tests/test_totals.py tests/test_rule_code_price.py
git commit -m "feat: payer-aware R4 (status I is a lead off Medicare) and per-line capped case totals

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Signed-cookie workspaces and shared API dependencies

**Files:**
- Create: `app/api/__init__.py` (empty), `app/api/deps.py`, `app/api/workspace.py`, `tests/test_workspace.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `get_session`, `Workspace`, `load_normalized`, `InMemoryReference`.
- Produces (in `app/api/deps.py`): `COOKIE_NAME = "pp_ws"`, `get_reference() -> InMemoryReference` (moved from `app/main.py`), `current_workspace(request, response, session, ref) -> Workspace`, and `Annotated` aliases `SessionDep`, `RefDep`, `WorkspaceDep`. `GET /api/workspace -> {"id": str, "created_at": str}`.

- [ ] **Step 1: Write the failing tests** — `tests/test_workspace.py`

```python
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from app.db.models import Workspace
from app.main import app


def test_first_request_creates_workspace_and_sets_cookie(db: Engine) -> None:
    c = TestClient(app)
    r = c.get("/api/workspace")
    assert r.status_code == 200
    assert "pp_ws" in c.cookies
    assert c.get("/api/workspace").json()["id"] == r.json()["id"]


def test_tampered_cookie_gets_a_new_workspace(db: Engine) -> None:
    c = TestClient(app)
    first = c.get("/api/workspace").json()["id"]
    c.cookies.clear()
    c.cookies.set("pp_ws", "garbage")
    assert c.get("/api/workspace").json()["id"] != first


def test_cookie_for_deleted_workspace_gets_a_new_one(db: Engine) -> None:
    c = TestClient(app)
    first = c.get("/api/workspace").json()["id"]
    with Session(db) as s:
        s.execute(delete(Workspace).where(Workspace.id == uuid.UUID(first)))
        s.commit()
    assert c.get("/api/workspace").json()["id"] != first


def test_two_clients_get_different_workspaces(db: Engine) -> None:
    assert TestClient(app).get("/api/workspace").json()["id"] != TestClient(app).get("/api/workspace").json()["id"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_workspace.py -v`
Expected: FAIL — 404 on `/api/workspace` (or import error for `app.api`).

- [ ] **Step 3: Implement** — `app/api/deps.py`

```python
import os
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request, Response
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from app.db.models import Workspace
from app.db.session import get_session
from app.reference.base import InMemoryReference
from app.reference.normalized import load_normalized

REF_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reference" / "subset"
COOKIE_NAME = "pp_ws"
COOKIE_MAX_AGE = 24 * 3600

SessionDep = Annotated[Session, Depends(get_session)]


@lru_cache(maxsize=1)
def get_reference() -> InMemoryReference:
    return load_normalized(REF_DIR)


RefDep = Annotated[InMemoryReference, Depends(get_reference)]


def _serializer() -> URLSafeSerializer:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError("SESSION_SECRET is not set")
    return URLSafeSerializer(secret, salt="workspace")


def _load(session: Session, raw: str) -> Workspace | None:
    try:
        ws_id = uuid.UUID(str(_serializer().loads(raw)))
    except (BadSignature, ValueError):
        return None
    return session.get(Workspace, ws_id)


def current_workspace(request: Request, response: Response, session: SessionDep, ref: RefDep) -> Workspace:
    raw = request.cookies.get(COOKIE_NAME)
    ws = _load(session, raw) if raw else None
    if ws is None:
        ws = Workspace()
        session.add(ws)
        session.commit()
        response.set_cookie(
            COOKIE_NAME, _serializer().dumps(str(ws.id)), max_age=COOKIE_MAX_AGE,
            httponly=True, samesite="lax", secure=os.environ.get("VERCEL") == "1",
        )
    return ws


WorkspaceDep = Annotated[Workspace, Depends(current_workspace)]
```
(`ref` is unused until Task 9 seeds demo cases; keep it so the signature doesn't change later. If ruff flags it as unused (ARG001 isn't in the selected rules), leave it.)

`app/api/workspace.py`:
```python
from fastapi import APIRouter

from app.api.deps import WorkspaceDep

router = APIRouter()


@router.get("/api/workspace")
def get_workspace(ws: WorkspaceDep) -> dict[str, str]:
    return {"id": str(ws.id), "created_at": ws.created_at.isoformat()}
```

In `app/main.py`: delete `REF_DIR` and `get_reference` (now in deps), import `get_reference` from `app.api.deps` for `/api/version`, and add `from app.api import workspace` + `app.include_router(workspace.router)`.

- [ ] **Step 4: Run tests**

Run: `pytest -q`
Expected: all pass (including Plan 1's `/api/version` test).

- [ ] **Step 5: Commit**

```bash
ruff format app tests && ruff check . && mypy app evals scripts
git add app/api app/main.py tests/test_workspace.py
git commit -m "feat: per-visitor workspaces via signed cookie; shared API dependencies

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Case upload, list and detail; audit log

**Files:**
- Create: `app/api/schemas.py`, `app/api/cases.py`, `app/services/__init__.py` (empty), `app/services/audit_log.py`, `app/services/cases.py`, `tests/api_helpers.py`, `tests/test_api_cases.py`
- Modify: `app/main.py` (include router)

**Interfaces:**
- Consumes: `parse_fhir`, `ParseError`, `claims_to_bundle` (Plan 1); `Claim`, `Flag`, `Evidence`, `Severity`; `case_totals`; deps from Task 3; models from Task 1.
- Produces:
  - `app.services.audit_log.record(session, workspace_id, action, *, case_id=None, actor="reviewer", detail=None, ref_versions=None) -> AuditEvent`
  - `app.services.cases`: `create_cases(session, ws, claims: list[Claim], payer_type: str, actor: str = "reviewer") -> list[Case]`; `case_flags(session, case_id) -> list[FlagRow]` (ordered by `position`); `flag_from_row(row: FlagRow, claim_id: str) -> Flag`; `to_claim(case) -> Claim`; `summarize(case, rows) -> CaseSummary`; `get_case_or_404(session, ws, case_id) -> Case`
  - `app.api.schemas`: `LineOut, FlagOut, LetterOut, CaseSummary, CaseDetail, ParseErrorOut, UploadResult, FlagUpdate, LetterEdit`
  - Routes: `POST /api/cases?payer_type=` (201/413/422), `GET /api/cases`, `GET /api/cases/{case_id}`; constant `MAX_UPLOAD_BYTES = 4_000_000` in `app/api/cases.py`
  - `tests/api_helpers.py`: `sample_claim() -> Claim`, `upload(client, claims, payer_type="unknown") -> Response`

- [ ] **Step 1: Write the failing tests**

`tests/api_helpers.py`:
```python
from typing import Any

import httpx
from fastapi.testclient import TestClient

from app.ingest.fhir import claims_to_bundle
from app.models import Claim
from tests.helpers import line


def sample_claim(claim_id: str = "EOB-1") -> Claim:
    """Fixture-reference claim: R1 duplicate (96372 x2), R5 outlier (99213 at 300), R4 status-I code (77061)."""
    return Claim(
        id=claim_id,
        patient_pseudonym="P-test",
        provider="Test Clinic",
        payer="Test Plan",
        lines=[
            line("L1", code="96372", charge="30.00"),
            line("L2", code="96372", charge="30.00"),
            line("L3", code="99213", charge="300.00"),
            line("L4", code="77061", charge="80.00"),
        ],
    )


def upload(client: TestClient, claims: list[Claim], payer_type: str = "unknown", **extra: Any) -> httpx.Response:
    return client.post(f"/api/cases?payer_type={payer_type}", json=claims_to_bundle(claims), **extra)
```

`tests/test_api_cases.py`:
```python
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.ingest.fhir import claims_to_bundle
from app.main import app
from tests.api_helpers import sample_claim, upload


def test_upload_creates_one_case_per_claim(db: Engine) -> None:
    c = TestClient(app)
    r = upload(c, [sample_claim("A"), sample_claim("B")])
    assert r.status_code == 201
    body = r.json()
    assert body["errors"] == []
    assert [x["claim_id"] for x in body["cases"]] == ["A", "B"]
    assert body["cases"][0]["status"] == "uploaded" and body["cases"][0]["line_count"] == 4
    with Session(db) as s:
        assert [e.action for e in s.scalars(select(AuditEvent))] == ["case_uploaded", "case_uploaded"]


def test_partial_bundle_keeps_valid_claims_and_reports_paths(db: Engine) -> None:
    bundle = claims_to_bundle([sample_claim("A")])
    bundle["entry"].append({"resource": {"resourceType": "ExplanationOfBenefit", "item": []}})  # type: ignore[union-attr]
    r = TestClient(app).post("/api/cases", json=bundle)
    assert r.status_code == 201
    assert len(r.json()["cases"]) == 1
    assert r.json()["errors"][0]["path"] == "$.entry[1].resource.id"


def test_no_valid_claims_is_422_with_paths(db: Engine) -> None:
    r = TestClient(app).post("/api/cases", json={"resourceType": "Patient"})
    assert r.status_code == 422
    assert r.json()["errors"][0]["path"] == "$.resourceType"


def test_non_json_body_is_422(db: Engine) -> None:
    r = TestClient(app).post("/api/cases", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert r.json()["errors"] == [{"path": "$", "message": "body is not valid JSON"}]


def test_oversized_upload_is_413(db: Engine) -> None:
    r = TestClient(app).post("/api/cases", content=b" " * 4_000_001, headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_invalid_payer_type_is_422(db: Engine) -> None:
    assert upload(TestClient(app), [sample_claim()], payer_type="martian").status_code == 422


def test_list_is_newest_first_and_detail_has_lines(db: Engine) -> None:
    c = TestClient(app)
    upload(c, [sample_claim("A")])
    upload(c, [sample_claim("B")])
    cases = c.get("/api/cases").json()
    assert [x["claim_id"] for x in cases] == ["B", "A"]
    detail = c.get(f"/api/cases/{cases[0]['id']}").json()
    assert [ln["id"] for ln in detail["lines"]] == ["L1", "L2", "L3", "L4"]
    assert detail["lines"][2]["charge"] == "300.00"
    assert detail["flags"] == [] and detail["letter"] is None


def test_other_workspace_cannot_see_case(db: Engine) -> None:
    owner, other = TestClient(app), TestClient(app)
    case_id = upload(owner, [sample_claim()]).json()["cases"][0]["id"]
    assert other.get(f"/api/cases/{case_id}").status_code == 404
    assert other.get("/api/cases").json() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api_cases.py -v`
Expected: FAIL — 404/405 on `/api/cases`.

- [ ] **Step 3: Implement**

`app/services/audit_log.py`:
```python
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def record(
    session: Session,
    workspace_id: uuid.UUID,
    action: str,
    *,
    case_id: uuid.UUID | None = None,
    actor: str = "reviewer",
    detail: dict[str, Any] | None = None,
    ref_versions: list[str] | None = None,
) -> AuditEvent:
    event = AuditEvent(workspace_id=workspace_id, case_id=case_id, actor=actor, action=action,
                       detail=detail or {}, ref_versions=ref_versions or [])
    session.add(event)
    return event
```

`app/api/schemas.py`:
```python
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class LineOut(BaseModel):
    id: str
    code: str
    modifiers: list[str]
    units: int
    charge: Decimal
    date_of_service: date
    place_of_service: str | None


class FlagOut(BaseModel):
    id: uuid.UUID
    rule_id: str
    severity: str
    line_ids: list[str]
    evidence: dict[str, Any]
    est_overcharge: Decimal
    message: str
    explanation: str | None
    explanation_status: str
    status: str
    reject_reason: str | None


class LetterOut(BaseModel):
    id: uuid.UUID
    status: str
    body: str
    flag_ids: list[str]
    created_at: datetime
    approved_at: datetime | None


class CaseSummary(BaseModel):
    id: uuid.UUID
    claim_id: str
    provider: str | None
    payer: str | None
    payer_type: str
    source: str
    status: str
    line_count: int
    error_count: int
    est_overcharge: Decimal
    outlier_amount: Decimal
    created_at: datetime


class CaseDetail(CaseSummary):
    lines: list[LineOut]
    flags: list[FlagOut]
    letter: LetterOut | None


class ParseErrorOut(BaseModel):
    path: str
    message: str


class UploadResult(BaseModel):
    cases: list[CaseSummary]
    errors: list[ParseErrorOut]


class FlagUpdate(BaseModel):
    status: Literal["open", "accepted", "rejected"]
    reject_reason: str | None = Field(default=None, max_length=500)


class LetterEdit(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
```

`app/services/cases.py`:
```python
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CaseSummary
from app.db.models import Case, FlagRow, Workspace
from app.models import Claim, Evidence, Flag, Severity
from app.rules.totals import case_totals
from app.services.audit_log import record


def create_cases(
    session: Session, ws: Workspace, claims: list[Claim], payer_type: str, actor: str = "reviewer"
) -> list[Case]:
    cases = []
    for claim in claims:
        case = Case(workspace_id=ws.id, source=claim.source, payer_type=payer_type, claim=claim.model_dump(mode="json"))
        session.add(case)
        session.flush()
        record(session, ws.id, "case_uploaded", case_id=case.id, actor=actor,
               detail={"claim_id": claim.id, "lines": len(claim.lines), "payer_type": payer_type})
        cases.append(case)
    return cases


def to_claim(case: Case) -> Claim:
    return Claim.model_validate(case.claim)


def case_flags(session: Session, case_id: uuid.UUID) -> list[FlagRow]:
    return list(session.scalars(select(FlagRow).where(FlagRow.case_id == case_id).order_by(FlagRow.position)))


def flag_from_row(row: FlagRow, claim_id: str) -> Flag:
    return Flag(
        id=row.flag_key, claim_id=claim_id, rule_id=row.rule_id, severity=Severity(row.severity),
        line_ids=row.line_ids, evidence=Evidence.model_validate(row.evidence), est_overcharge=row.est_overcharge,
        message=row.message, explanation=row.explanation, status=row.status,  # type: ignore[arg-type]
        reject_reason=row.reject_reason,
    )


def summarize(case: Case, rows: list[FlagRow]) -> CaseSummary:
    claim = to_claim(case)
    live = [r for r in rows if r.status != "rejected"]
    totals = case_totals(claim, [flag_from_row(r, claim.id) for r in live])
    return CaseSummary(
        id=case.id, claim_id=claim.id, provider=claim.provider, payer=claim.payer, payer_type=case.payer_type,
        source=case.source, status=case.status, line_count=len(claim.lines),
        error_count=sum(r.severity == Severity.ERROR.value for r in live),
        est_overcharge=totals.errors, outlier_amount=totals.outliers, created_at=case.created_at,
    )


def get_case_or_404(session: Session, ws: Workspace, case_id: uuid.UUID) -> Case:
    case = session.get(Case, case_id)
    if case is None or case.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="case not found")
    return case
```

`app/api/cases.py`:
```python
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.deps import SessionDep, WorkspaceDep
from app.api.schemas import CaseDetail, CaseSummary, FlagOut, LetterOut, LineOut, ParseErrorOut, UploadResult
from app.db.models import Case, Letter
from app.ingest.fhir import parse_fhir
from app.rules import PayerType
from app.services.cases import case_flags, create_cases, get_case_or_404, summarize, to_claim

router = APIRouter()
MAX_UPLOAD_BYTES = 4_000_000


async def read_upload(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"upload larger than {MAX_UPLOAD_BYTES} bytes")
    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"upload larger than {MAX_UPLOAD_BYTES} bytes")
    return body


@router.post("/api/cases", status_code=201, response_model=UploadResult)
def upload_cases(
    body: Annotated[bytes, Depends(read_upload)], ws: WorkspaceDep, session: SessionDep,
    payer_type: PayerType = "unknown",
) -> UploadResult | JSONResponse:
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return JSONResponse(status_code=422, content={"cases": [], "errors": [
            {"path": "$", "message": "body is not valid JSON"}]})
    result = parse_fhir(data)
    errors = [ParseErrorOut(path=e.path, message=e.message) for e in result.errors]
    if not result.claims:
        return JSONResponse(status_code=422, content=UploadResult(cases=[], errors=errors).model_dump(mode="json"))
    cases = create_cases(session, ws, result.claims, payer_type)
    session.commit()
    return UploadResult(cases=[summarize(c, []) for c in cases], errors=errors)


@router.get("/api/cases", response_model=list[CaseSummary])
def list_cases(ws: WorkspaceDep, session: SessionDep) -> list[CaseSummary]:
    cases = session.scalars(
        select(Case).where(Case.workspace_id == ws.id).order_by(Case.created_at.desc(), Case.id.desc())
    )
    # ponytail: one flags query per case; fine for demo-sized workspaces, batch it if lists grow past ~100.
    return [summarize(c, case_flags(session, c.id)) for c in cases]


def case_detail(session: SessionDep, case: Case) -> CaseDetail:
    rows = case_flags(session, case.id)
    letter = session.scalars(
        select(Letter).where(Letter.case_id == case.id).order_by(Letter.created_at.desc())
    ).first()
    return CaseDetail(
        **summarize(case, rows).model_dump(),
        lines=[LineOut.model_validate(ln.model_dump()) for ln in to_claim(case).lines],
        flags=[FlagOut.model_validate(r, from_attributes=True) for r in rows],
        letter=LetterOut.model_validate(letter, from_attributes=True) if letter else None,
    )


@router.get("/api/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep) -> CaseDetail:
    return case_detail(session, get_case_or_404(session, ws, case_id))
```
Note: `created_at` is set by the database; `create_cases` flushes, and `summarize` reads `case.created_at` — after `session.commit()` with `expire_on_commit=False` the server default may not be loaded. Call `session.refresh(c)` for each case after commit before summarizing (add it in `upload_cases`).

Include the router in `app/main.py`: `from app.api import cases` and `app.include_router(cases.router)`.

- [ ] **Step 4: Run tests**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ruff format app tests && ruff check . && mypy app evals scripts
git add app/api app/services app/main.py tests/api_helpers.py tests/test_api_cases.py
git commit -m "feat: case upload (partial success, 413/422), list and detail APIs with audit log

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Run the audit and review flags

**Files:**
- Create: `app/services/audit_run.py`, `app/api/flags.py`, `tests/test_api_audit.py`
- Modify: `app/api/cases.py` (add audit route), `app/main.py` (include flags router)

**Interfaces:**
- Consumes: `run_rules`, `RuleConfig`, `Severity`; services from Task 4; `RefDep`.
- Produces:
  - `app.services.audit_run.run_audit(session, case, ref) -> list[FlagRow]` — deletes the case's existing flags and letters, runs rules with `RuleConfig(payer_type=case.payer_type)`, stores rows (`explanation_status` = `"pending"` for error/outlier/lead, `"none"` for notice), sets `case.status = "needs_review"`, records `audit_run` with ref versions
  - `POST /api/cases/{case_id}/audit -> CaseDetail`
  - `PATCH /api/flags/{flag_id}` (body `FlagUpdate`) `-> FlagOut`; rejecting needs a non-blank reason (422); notices can't be accepted/rejected (422); other workspace → 404

- [ ] **Step 1: Write the failing tests** — `tests/test_api_audit.py`

```python
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.api.deps import get_reference
from app.db.models import AuditEvent
from app.main import app
from tests.api_helpers import sample_claim, upload
from tests.helpers import FIXTURE_REF, line


def client() -> TestClient:
    app.dependency_overrides[get_reference] = lambda: FIXTURE_REF
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def audited(c: TestClient, payer_type: str = "unknown") -> dict:  # type: ignore[type-arg]
    case_id = upload(c, [sample_claim()], payer_type=payer_type).json()["cases"][0]["id"]
    r = c.post(f"/api/cases/{case_id}/audit")
    assert r.status_code == 200
    return r.json()  # type: ignore[no-any-return]


def test_audit_stores_flags_and_totals(db: Engine) -> None:
    detail = audited(client())
    by_rule = {f["rule_id"]: f for f in detail["flags"]}
    assert by_rule["R1"]["severity"] == "error" and by_rule["R1"]["line_ids"] == ["L1", "L2"]
    assert by_rule["R5"]["severity"] == "outlier"
    assert by_rule["R4"]["severity"] == "lead"  # 77061 is status I and the payer isn't Medicare
    assert all(f["explanation_status"] == "pending" and f["status"] == "open" for f in detail["flags"])
    assert detail["status"] == "needs_review"
    assert detail["est_overcharge"] == "30.00" and detail["outlier_amount"] == "23.55"
    with Session(db) as s:
        event = s.scalars(select(AuditEvent).where(AuditEvent.action == "audit_run")).one()
        assert event.ref_versions == ["NCCI-TEST", "MUE-TEST", "PFS-TEST"]


def test_medicare_payer_makes_status_i_an_error(db: Engine) -> None:
    by_rule = {f["rule_id"]: f for f in audited(client(), payer_type="medicare")["flags"]}
    assert by_rule["R4"]["severity"] == "error"


def test_reaudit_replaces_flags(db: Engine) -> None:
    c = client()
    detail = audited(c)
    again = c.post(f"/api/cases/{detail['id']}/audit").json()
    assert len(again["flags"]) == len(detail["flags"])
    assert {f["id"] for f in again["flags"]}.isdisjoint({f["id"] for f in detail["flags"]})


def test_accept_and_reject_flags(db: Engine) -> None:
    c = client()
    detail = audited(c)
    flags = {f["rule_id"]: f for f in detail["flags"]}
    assert c.patch(f"/api/flags/{flags['R1']['id']}", json={"status": "accepted"}).json()["status"] == "accepted"
    assert c.patch(f"/api/flags/{flags['R5']['id']}", json={"status": "rejected"}).status_code == 422
    blank = {"status": "rejected", "reject_reason": "  "}
    assert c.patch(f"/api/flags/{flags['R5']['id']}", json=blank).status_code == 422
    r = c.patch(f"/api/flags/{flags['R5']['id']}", json={"status": "rejected", "reject_reason": "contracted rate"})
    assert r.json()["status"] == "rejected" and r.json()["reject_reason"] == "contracted rate"
    after = {f["rule_id"]: f for f in c.get(f"/api/cases/{detail['id']}").json()["flags"]}
    assert after["R1"]["status"] == "accepted" and after["R5"]["status"] == "rejected"


def test_rejected_flags_drop_out_of_totals(db: Engine) -> None:
    c = client()
    detail = audited(c)
    r5 = next(f for f in detail["flags"] if f["rule_id"] == "R5")
    c.patch(f"/api/flags/{r5['id']}", json={"status": "rejected", "reject_reason": "contracted rate"})
    assert c.get(f"/api/cases/{detail['id']}").json()["outlier_amount"] == "0.00"


def test_notice_cannot_be_reviewed(db: Engine) -> None:
    c = client()
    claim = sample_claim()
    claim.lines.append(line("L5", code="97110", dos=date(2027, 1, 5)))
    case_id = upload(c, [claim]).json()["cases"][0]["id"]
    notice = next(f for f in c.post(f"/api/cases/{case_id}/audit").json()["flags"] if f["rule_id"] == "R0")
    assert c.patch(f"/api/flags/{notice['id']}", json={"status": "accepted"}).status_code == 422


def test_other_workspace_cannot_audit_or_review(db: Engine) -> None:
    owner, other = client(), client()
    detail = audited(owner)
    assert other.post(f"/api/cases/{detail['id']}/audit").status_code == 404
    flag_id = detail["flags"][0]["id"]
    assert other.patch(f"/api/flags/{flag_id}", json={"status": "accepted"}).status_code == 404
```
- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api_audit.py -v`
Expected: FAIL — 404/405 on `/audit` and `/api/flags/...`.

- [ ] **Step 3: Implement**

`app/services/audit_run.py`:
```python
from collections import Counter
from typing import cast

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Case, FlagRow, Letter
from app.models import Severity
from app.reference.base import InMemoryReference
from app.rules import PayerType, RuleConfig, run_rules
from app.services.audit_log import record
from app.services.cases import to_claim

EXPLAINED = {Severity.ERROR, Severity.OUTLIER, Severity.LEAD}


def run_audit(session: Session, case: Case, ref: InMemoryReference, actor: str = "reviewer") -> list[FlagRow]:
    claim = to_claim(case)
    session.execute(delete(FlagRow).where(FlagRow.case_id == case.id))
    session.execute(delete(Letter).where(Letter.case_id == case.id))
    flags = run_rules(claim, ref, RuleConfig(payer_type=cast(PayerType, case.payer_type)))
    rows = [
        FlagRow(
            case_id=case.id, position=i, flag_key=f.id, rule_id=f.rule_id, severity=f.severity.value,
            line_ids=f.line_ids, evidence=f.evidence.model_dump(mode="json"), est_overcharge=f.est_overcharge,
            message=f.message, explanation_status="pending" if f.severity in EXPLAINED else "none",
        )
        for i, f in enumerate(flags)
    ]
    session.add_all(rows)
    case.status = "needs_review"
    record(session, case.workspace_id, "audit_run", case_id=case.id, actor=actor,
           detail={"flags": len(rows), "by_rule": dict(Counter(f.rule_id for f in flags))},
           ref_versions=[v.ref_version for v in ref.versions])
    session.flush()
    return rows
```

Add to `app/api/cases.py`:
```python
from app.api.deps import RefDep
from app.services.audit_run import run_audit


@router.post("/api/cases/{case_id}/audit", response_model=CaseDetail)
def audit_case(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep, ref: RefDep) -> CaseDetail:
    case = get_case_or_404(session, ws, case_id)
    run_audit(session, case, ref)
    session.commit()
    return case_detail(session, case)
```

`app/api/flags.py`:
```python
import uuid

from fastapi import APIRouter, HTTPException

from app.api.deps import SessionDep, WorkspaceDep
from app.api.schemas import FlagOut, FlagUpdate
from app.db.models import Case, FlagRow
from app.services.audit_log import record

router = APIRouter()


@router.patch("/api/flags/{flag_id}", response_model=FlagOut)
def review_flag(flag_id: uuid.UUID, update: FlagUpdate, ws: WorkspaceDep, session: SessionDep) -> FlagOut:
    row = session.get(FlagRow, flag_id)
    case = session.get(Case, row.case_id) if row else None
    if row is None or case is None or case.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="flag not found")
    if row.severity == "notice":
        raise HTTPException(status_code=422, detail="notices are informational and can't be reviewed")
    reason = (update.reject_reason or "").strip()
    if update.status == "rejected" and not reason:
        raise HTTPException(status_code=422, detail="rejecting a flag needs a reason")
    before = row.status
    row.status = update.status
    row.reject_reason = reason if update.status == "rejected" else None
    record(session, ws.id, "flag_reviewed", case_id=case.id,
           detail={"flag_id": str(row.id), "rule_id": row.rule_id, "from": before, "to": row.status,
                   "reason": row.reject_reason})
    session.commit()
    return FlagOut.model_validate(row, from_attributes=True)
```
Include `flags.router` in `app/main.py`.

- [ ] **Step 4: Run tests**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ruff format app tests && ruff check . && mypy app evals scripts
git add app/services/audit_run.py app/api tests/test_api_audit.py app/main.py
git commit -m "feat: run the rule engine on a case and review flags (accept/reject with reason)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: LLM client, numeric grounding check, LangGraph explanation loop, budget

**Files:**
- Create: `app/llm/__init__.py` (empty), `app/llm/client.py`, `app/llm/grounding.py`, `app/llm/rule_text.py`, `app/llm/explain.py`, `app/services/llm_budget.py`, `tests/fakes.py`, `tests/test_explain.py`, `tests/test_llm_budget.py`

**Interfaces:**
- Consumes: `Flag`, `Severity`; `LlmUsage`; `Session`.
- Produces:
  - `app.llm.client`: `LLMClient` (Protocol: `complete(system: str, user: str) -> str`), `OpenRouterClient(api_key, model, timeout=30.0)`, `DEFAULT_EXPLAIN_MODEL = "deepseek/deepseek-v4-pro"`, `default_client() -> LLMClient | None` (None when `OPENROUTER_API_KEY` is unset)
  - `app.llm.grounding`: `numbers_in(text) -> set[str]`, `unsupported_numbers(text, sources: Iterable[str]) -> list[str]`
  - `app.llm.rule_text.RULE_TEXT: dict[str, str]` (R0–R5)
  - `app.llm.explain`: `SYSTEM_PROMPT`, `MAX_ATTEMPTS = 2`, `build_prompt(flag) -> tuple[str, list[str]]`, `build_explain_graph(llm)`, `explain_flag(flag, llm) -> str | None`
  - `app.services.llm_budget`: `EXPLANATIONS_PER_HOUR = 20`, `try_consume(session, workspace_id, now=None) -> bool`
  - `tests.fakes.FakeLLM(replies=None, error=None)` with `.calls: list[str]`

- [ ] **Step 1: Check the installed SDKs**

Run: `python -c "import openai, langgraph; from openai import OpenAI; print(openai.__version__); from langgraph.graph import StateGraph, START, END; print('ok')"` and `python -c "from openai import OpenAI; import inspect; print(inspect.signature(OpenAI(api_key='x').chat.completions.create).parameters.keys())"`.
Expected: prints versions, `ok`, and a parameter list containing `model`, `messages`, `max_tokens` (or `max_completion_tokens`), `temperature`. If `max_tokens` is absent, use `max_completion_tokens` in `OpenRouterClient` and say so in your report.

- [ ] **Step 2: Write the failing tests**

`tests/fakes.py`:
```python
class FakeLLM:
    def __init__(self, replies: list[str] | None = None, error: Exception | None = None) -> None:
        self.replies = list(replies or [])
        self.error = error
        self.calls: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append(user)
        if self.error is not None:
            raise self.error
        return self.replies.pop(0) if self.replies else ""
```

`tests/test_explain.py`:
```python
from decimal import Decimal

from app.llm.explain import build_prompt, explain_flag
from app.llm.grounding import numbers_in, unsupported_numbers
from app.models import Evidence, Severity, make_flag
from tests.fakes import FakeLLM
from tests.helpers import claim, line

C = claim(line("L1", code="99213", charge="300.00"))
FLAG = make_flag(
    C, "R5", Severity.OUTLIER, C.lines,
    Evidence(table="pfs_rates", ref_version="PFS-TEST", row={"code": "99213", "medicare_rate": "92.15",
                                                              "multiplier": "3", "units": "1"}),
    Decimal("23.55"), "99213 charged 300.00 for 1 unit(s); that is more than 3x the Medicare national rate of 92.15 per unit",
)
GROUNDED = "This visit was billed at $300.00, more than 3 times Medicare's $92.15 rate, about $23.55 above that benchmark."


def test_numbers_are_normalized() -> None:
    assert numbers_in("$1,200.50 and 92.150 on 2026-10-15") == {"1200.5", "92.15", "2026", "10", "15"}


def test_unsupported_numbers() -> None:
    sources = ["rate 92.15", "overcharge 23.55"]
    assert unsupported_numbers("about $23.55 over 92.15", sources) == []
    assert unsupported_numbers("about $999 over 92.15", sources) == ["999"]
    assert unsupported_numbers("billed 2 times", sources) == []  # small integers are allowed


def test_prompt_contains_no_patient_or_provider_identity() -> None:
    prompt, sources = build_prompt(FLAG)
    assert "P-1" not in prompt and "C1" not in prompt
    assert "92.15" in prompt and any("23.55" in s for s in sources)


def test_grounded_first_draft_is_returned() -> None:
    llm = FakeLLM([GROUNDED])
    assert explain_flag(FLAG, llm) == GROUNDED
    assert len(llm.calls) == 1


def test_ungrounded_draft_is_retried_with_feedback() -> None:
    llm = FakeLLM(["The overcharge is $4,000.", GROUNDED])
    assert explain_flag(FLAG, llm) == GROUNDED
    assert len(llm.calls) == 2 and "4000" in llm.calls[1]


def test_still_ungrounded_after_retry_returns_none() -> None:
    llm = FakeLLM(["The overcharge is $4,000.", "It is $5,000."])
    assert explain_flag(FLAG, llm) is None
    assert len(llm.calls) == 2


def test_empty_reply_and_llm_errors_return_none() -> None:
    assert explain_flag(FLAG, FakeLLM(["", ""])) is None
    assert explain_flag(FLAG, FakeLLM(error=TimeoutError("slow"))) is None
```

`tests/test_llm_budget.py`:
```python
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
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_explain.py tests/test_llm_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm'`.

- [ ] **Step 4: Implement**

`app/llm/client.py`:
```python
import os
from typing import Protocol

from openai import OpenAI

DEFAULT_EXPLAIN_MODEL = "deepseek/deepseek-v4-pro"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenRouterClient:
    def __init__(self, api_key: str, model: str, timeout: float = 30.0) -> None:
        self._client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=timeout, max_retries=1)
        self._model = model

    def complete(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=300,
            temperature=0,
        )
        return (response.choices[0].message.content or "").strip()


def default_client() -> LLMClient | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    return OpenRouterClient(key, os.environ.get("EXPLAIN_MODEL", DEFAULT_EXPLAIN_MODEL))
```

`app/llm/grounding.py`:
```python
"""Numbers an explanation may use must come from the evidence it explains."""

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SMALL_INTEGERS = {str(i) for i in range(11)}  # counts like "2 lines" or "3 times" are allowed


def numbers_in(text: str) -> set[str]:
    out = set()
    for token in _NUMBER.findall(text):
        try:
            value = Decimal(token.replace(",", ""))
        except InvalidOperation:
            continue
        out.add(format(value.normalize(), "f"))
    return out


def unsupported_numbers(text: str, sources: Iterable[str]) -> list[str]:
    allowed = set(SMALL_INTEGERS)
    for source in sources:
        allowed |= numbers_in(source)
    return sorted(numbers_in(text) - allowed)
```

`app/llm/rule_text.py`:
```python
RULE_TEXT = {
    "R0": "PriorPath can only audit dates of service covered by the CMS reference releases it has loaded; "
          "lines outside those dates are not checked.",
    "R1": "Billing the same service, with the same modifiers and units, more than once on the same date is a "
          "duplicate charge unless a repeat-procedure modifier explains it.",
    "R2": "CMS National Correct Coding Initiative (NCCI) procedure-to-procedure edits list code pairs where the "
          "column 2 code is part of the column 1 code and should not be billed separately on the same date. "
          "Modifier indicator 0 means never billed together; indicator 1 means allowed only with an appropriate "
          "modifier.",
    "R3": "CMS Medically Unlikely Edits (MUE) set the maximum units of a service that one provider would "
          "normally report for one patient, per claim line or per date of service.",
    "R4": "Physician Fee Schedule status D codes were deleted, and status I codes are not valid for Medicare, "
          "which requires a different code.",
    "R5": "A charge far above the Medicare national payment rate for the same service is not an error by itself, "
          "but it justifies asking the provider for an itemized justification.",
}
```

`app/llm/explain.py`:
```python
"""Draft → check numbers → retry once. The model explains a flag; it never decides one."""

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.llm.client import LLMClient
from app.llm.grounding import unsupported_numbers
from app.llm.rule_text import RULE_TEXT
from app.models import Flag

MAX_ATTEMPTS = 2
SYSTEM_PROMPT = (
    "You explain one medical-billing finding to a claims auditor in plain English, in at most three sentences. "
    "Use only facts from the RULE, FINDING and EVIDENCE sections. Do not introduce any number, dollar amount, "
    "date or code that is not written there. Do not call anything fraud or illegal. If the severity is 'lead', "
    "say the finding needs confirmation before it is disputed."
)


class ExplainState(TypedDict):
    prompt: str
    sources: list[str]
    attempt: int
    draft: str
    problems: list[str]
    explanation: str | None


def build_prompt(flag: Flag) -> tuple[str, list[str]]:
    rule = RULE_TEXT.get(flag.rule_id, "")
    evidence = json.dumps(flag.evidence.row, sort_keys=True)
    prompt = (
        f"RULE ({flag.rule_id}): {rule}\nSEVERITY: {flag.severity.value}\nFINDING: {flag.message}\n"
        f"EVIDENCE ({flag.evidence.table}, {flag.evidence.ref_version}): {evidence}\n"
        f"ESTIMATED OVERCHARGE: ${flag.est_overcharge}"
    )
    sources = [rule, flag.message, evidence, str(flag.est_overcharge), " ".join(flag.line_ids),
               flag.evidence.ref_version or ""]
    return prompt, sources


def build_explain_graph(llm: LLMClient) -> Any:
    def draft(state: ExplainState) -> dict[str, Any]:
        note = ""
        if state["problems"]:
            note = (f"\n\nYour previous answer used numbers that are not in the evidence: "
                    f"{', '.join(state['problems'])}. Rewrite it using only numbers from the evidence.")
        return {"draft": llm.complete(SYSTEM_PROMPT, state["prompt"] + note), "attempt": state["attempt"] + 1}

    def check(state: ExplainState) -> dict[str, Any]:
        problems = unsupported_numbers(state["draft"], state["sources"])
        ok = bool(state["draft"].strip()) and not problems
        return {"problems": problems, "explanation": state["draft"].strip() if ok else None}

    def route(state: ExplainState) -> str:
        return "done" if state["explanation"] is not None or state["attempt"] >= MAX_ATTEMPTS else "retry"

    graph = StateGraph(ExplainState)
    graph.add_node("draft", draft)
    graph.add_node("check", check)
    graph.add_edge(START, "draft")
    graph.add_edge("draft", "check")
    graph.add_conditional_edges("check", route, {"retry": "draft", "done": END})
    return graph.compile()


def explain_flag(flag: Flag, llm: LLMClient) -> str | None:
    prompt, sources = build_prompt(flag)
    try:
        result = build_explain_graph(llm).invoke(
            {"prompt": prompt, "sources": sources, "attempt": 0, "draft": "", "problems": [], "explanation": None}
        )
    except Exception:  # noqa: BLE001 — an LLM or network failure must never break an audit
        return None
    explanation = result.get("explanation")
    return explanation if isinstance(explanation, str) else None
```
Note: when the first draft is empty, `problems` is empty, so the retry prompt carries no note; that is intended.

`app/services/llm_budget.py`:
```python
import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import LlmUsage

EXPLANATIONS_PER_HOUR = 20


def try_consume(session: Session, workspace_id: uuid.UUID, now: datetime | None = None) -> bool:
    hour = (now or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    stmt = (
        insert(LlmUsage)
        .values(workspace_id=workspace_id, hour_start=hour, calls=1)
        .on_conflict_do_update(
            index_elements=[LlmUsage.workspace_id, LlmUsage.hour_start], set_={"calls": LlmUsage.calls + 1}
        )
        .returning(LlmUsage.calls)
    )
    return session.execute(stmt).scalar_one() <= EXPLANATIONS_PER_HOUR
```

- [ ] **Step 5: Run tests**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
ruff format app tests && ruff check . && mypy app evals scripts
git add app/llm app/services/llm_budget.py tests/fakes.py tests/test_explain.py tests/test_llm_budget.py
git commit -m "feat: grounded flag explanations — LangGraph draft/check/retry over OpenRouter, hourly budget

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Stream explanations for a case (SSE)

**Files:**
- Create: `app/llm/cache.py`, `app/services/explanations.py`, `tests/test_api_explain.py`
- Modify: `app/api/deps.py` (add `get_llm`, `LLMDep`), `app/api/cases.py` (explain route)

**Interfaces:**
- Consumes: `explain_flag`, `default_client`, `LLMClient`, `try_consume`, `flag_from_row`, `case_flags`, `get_case_or_404`, `record`, `get_engine`.
- Produces:
  - `app.llm.cache`: `DEMO_EXPLANATIONS: Path` (`data/demo/explanations.json`), `explanation_cache_key(flag) -> str`, `cached_explanation(flag) -> str | None`
  - `app.api.deps`: `get_llm() -> LLMClient | None`, `LLMDep`
  - `app.services.explanations.explain_row(session, workspace_id, row, claim_id, llm) -> tuple[str, str | None]` returning `(status, reason)` and updating the row (`ready` | `unavailable`)
  - `POST /api/cases/{case_id}/explain` → `text/event-stream` with events `start` `{pending}`, `explanation` `{index, total, flag_id, status, explanation, reason}`, `done` `{ready, unavailable}`; processes flags whose `explanation_status` is `pending` or `unavailable`

- [ ] **Step 1: Write the failing tests** — `tests/test_api_explain.py`

```python
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
    assert events(c.post(f"/api/cases/{case_id}/explain").text)[-1] == ("done", {"ready": 3, "unavailable": 0})


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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api_explain.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_llm'`.

- [ ] **Step 3: Implement**

`app/llm/cache.py`:
```python
"""Precomputed, grounded explanations for the demo cases (built by scripts/build_demo.py --explain)."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from app.models import Flag

DEMO_EXPLANATIONS = Path(__file__).resolve().parent.parent.parent / "data" / "demo" / "explanations.json"


def explanation_cache_key(flag: Flag) -> str:
    payload = {"rule": flag.rule_id, "severity": flag.severity.value, "message": flag.message,
               "evidence": flag.evidence.row, "overcharge": str(flag.est_overcharge)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    if not DEMO_EXPLANATIONS.exists():
        return {}
    data = json.loads(DEMO_EXPLANATIONS.read_text())
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def cached_explanation(flag: Flag) -> str | None:
    return _load().get(explanation_cache_key(flag))
```

`app/services/explanations.py`:
```python
import uuid

from sqlalchemy.orm import Session

from app.db.models import FlagRow
from app.llm.cache import cached_explanation
from app.llm.client import LLMClient
from app.llm.explain import explain_flag
from app.services import llm_budget
from app.services.cases import flag_from_row

NOT_CONFIGURED = "explanations are not configured on this server"
OVER_BUDGET = "hourly explanation limit reached; try again later"
UNGROUNDED = "could not produce an explanation grounded in the evidence"


def explain_row(
    session: Session, workspace_id: uuid.UUID, row: FlagRow, claim_id: str, llm: LLMClient | None
) -> tuple[str, str | None]:
    flag = flag_from_row(row, claim_id)
    text = cached_explanation(flag)
    reason: str | None = None
    if text is None:
        if llm is None:
            reason = NOT_CONFIGURED
        elif not llm_budget.try_consume(session, workspace_id):
            reason = OVER_BUDGET
        else:
            text = explain_flag(flag, llm)
            reason = None if text else UNGROUNDED
    row.explanation = text
    row.explanation_status = "ready" if text else "unavailable"
    return row.explanation_status, reason
```

Add to `app/api/deps.py`:
```python
from app.llm.client import LLMClient, default_client


def get_llm() -> LLMClient | None:
    return default_client()


LLMDep = Annotated[LLMClient | None, Depends(get_llm)]
```

Add to `app/api/cases.py`:
```python
import json as jsonlib
from collections.abc import Iterator

from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import LLMDep
from app.db.models import FlagRow
from app.db.session import get_engine
from app.services.audit_log import record
from app.services.explanations import explain_row

EXPLAINABLE = ("pending", "unavailable")


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {jsonlib.dumps(data)}\n\n"


@router.post("/api/cases/{case_id}/explain")
def explain_case(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep, llm: LLMDep) -> StreamingResponse:
    case = get_case_or_404(session, ws, case_id)
    claim_id = str(case.claim["id"])
    workspace_id = ws.id
    flag_ids = [r.id for r in case_flags(session, case.id) if r.explanation_status in EXPLAINABLE]

    def stream() -> Iterator[str]:
        # The request's session closes when the response starts streaming, so use our own.
        with Session(get_engine(), expire_on_commit=False) as s:
            yield _sse("start", {"pending": len(flag_ids)})
            ready = unavailable = 0
            for index, flag_id in enumerate(flag_ids, start=1):
                row = s.get(FlagRow, flag_id)
                if row is None:
                    continue
                status, reason = explain_row(s, workspace_id, row, claim_id, llm)
                s.commit()
                ready += status == "ready"
                unavailable += status == "unavailable"
                yield _sse("explanation", {"index": index, "total": len(flag_ids), "flag_id": str(flag_id),
                                           "status": status, "explanation": row.explanation, "reason": reason})
            record(s, workspace_id, "explanations_generated", case_id=case_id,
                   detail={"ready": ready, "unavailable": unavailable})
            s.commit()
            yield _sse("done", {"ready": ready, "unavailable": unavailable})

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
```
(Merge imports at the top of the file.)

- [ ] **Step 4: Run tests**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ruff format app tests && ruff check . && mypy app evals scripts
git add app/llm/cache.py app/services/explanations.py app/api tests/test_api_explain.py
git commit -m "feat: stream grounded explanations per case over SSE; failures degrade to 'unavailable'

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Dispute letters — draft, edit (numbers locked), approve, export

**Files:**
- Create: `app/services/letters.py`, `app/api/letters.py`, `tests/test_api_letters.py`
- Modify: `app/main.py` (include router)

**Interfaces:**
- Consumes: `case_totals`, `flag_from_row`, `case_flags`, `to_claim`, `get_case_or_404`, `unsupported_numbers`, `record`, `RefDep`, `LetterOut`, `LetterEdit`.
- Produces:
  - `app.services.letters`: `LETTER_SEVERITIES = ("error", "outlier")`, `build_letter(claim, rows, ref_versions, today) -> str`
  - Routes: `POST /api/cases/{case_id}/letter` (201 `LetterOut`; 409 when no accepted error/outlier), `PATCH /api/letters/{letter_id}` (`LetterEdit`; 409 if approved; 422 if new numbers), `POST /api/letters/{letter_id}/approve` (409 if already approved), `GET /api/letters/{letter_id}/export?format=txt|docx` (409 unless approved). Case status: `letter_ready` on draft, `approved` on approve, `exported` on export.

- [ ] **Step 1: Write the failing tests** — `tests/test_api_letters.py`

```python
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.deps import get_reference
from app.main import app
from tests.api_helpers import sample_claim, upload
from tests.helpers import FIXTURE_REF


def reviewed(accept: tuple[str, ...] = ("R1", "R5")) -> tuple[TestClient, str]:
    app.dependency_overrides[get_reference] = lambda: FIXTURE_REF
    c = TestClient(app)
    case_id = upload(c, [sample_claim()]).json()["cases"][0]["id"]
    for f in c.post(f"/api/cases/{case_id}/audit").json()["flags"]:
        if f["rule_id"] in accept:
            c.patch(f"/api/flags/{f['id']}", json={"status": "accepted"})
    return c, case_id


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_letter_needs_an_accepted_error_or_outlier(db: Engine) -> None:
    c, case_id = reviewed(accept=("R4",))  # R4 is only a lead for this payer
    assert c.post(f"/api/cases/{case_id}/letter").status_code == 409


def test_letter_lists_accepted_findings_with_totals(db: Engine) -> None:
    c, case_id = reviewed()
    r = c.post(f"/api/cases/{case_id}/letter")
    assert r.status_code == 201
    body = r.json()["body"]
    assert "96372 billed 2 times" in body
    assert "itemized justification" in body and "99213" in body
    assert "77061" not in body  # the lead was not accepted
    assert "$30.00" in body and "NCCI-TEST" in body
    assert c.get(f"/api/cases/{case_id}").json()["status"] == "letter_ready"


def test_edit_cannot_add_numbers(db: Engine) -> None:
    c, case_id = reviewed()
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    bad = c.patch(f"/api/letters/{letter['id']}", json={"body": letter["body"] + "\nPlease refund $999."})
    assert bad.status_code == 422 and "999" in bad.json()["detail"]
    ok = c.patch(f"/api/letters/{letter['id']}", json={"body": "Dear billing team,\n" + letter["body"]})
    assert ok.status_code == 200 and ok.json()["body"].startswith("Dear billing team")


def test_approve_then_export(db: Engine) -> None:
    c, case_id = reviewed()
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    assert c.get(f"/api/letters/{letter['id']}/export?format=txt").status_code == 409
    assert c.post(f"/api/letters/{letter['id']}/approve").json()["status"] == "approved"
    assert c.post(f"/api/letters/{letter['id']}/approve").status_code == 409
    assert c.patch(f"/api/letters/{letter['id']}", json={"body": "x"}).status_code == 409
    txt = c.get(f"/api/letters/{letter['id']}/export?format=txt")
    assert txt.status_code == 200 and "attachment" in txt.headers["content-disposition"]
    docx = c.get(f"/api/letters/{letter['id']}/export?format=docx")
    assert docx.content[:2] == b"PK"
    assert c.get(f"/api/cases/{case_id}").json()["status"] == "exported"


def test_other_workspace_cannot_touch_letters(db: Engine) -> None:
    c, case_id = reviewed()
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    other = TestClient(app)
    assert other.post(f"/api/cases/{case_id}/letter").status_code == 404
    assert other.post(f"/api/letters/{letter['id']}/approve").status_code == 404
    assert other.get(f"/api/letters/{letter['id']}/export?format=txt").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_api_letters.py -v`
Expected: FAIL — 404/405 on the letter routes.

- [ ] **Step 3: Implement**

`app/services/letters.py`:
```python
"""Dispute letters are templates over accepted flags: the reviewer, not a model, decides what goes in."""

from datetime import date

from app.db.models import FlagRow
from app.models import Claim
from app.rules.totals import case_totals
from app.services.cases import flag_from_row

LETTER_SEVERITIES = ("error", "outlier")


def build_letter(claim: Claim, rows: list[FlagRow], ref_versions: list[str], today: date) -> str:
    accepted = [r for r in rows if r.status == "accepted" and r.severity in LETTER_SEVERITIES]
    errors = [r for r in accepted if r.severity == "error"]
    outliers = [r for r in accepted if r.severity == "outlier"]
    totals = case_totals(claim, [flag_from_row(r, claim.id) for r in accepted])
    parts = [
        today.isoformat(),
        f"To: {claim.provider or 'Provider'} billing department",
        f"Re: review of itemized charges, claim {claim.id}",
        "",
        "We reviewed the itemized charges on this claim against Medicare's public billing rules "
        f"({', '.join(ref_versions)}) and found the following.",
    ]
    if errors:
        parts += ["", "Billing errors:"]
        for i, r in enumerate(errors, start=1):
            parts.append(f"{i}. {r.message}. Estimated overcharge: ${r.est_overcharge}.")
            if r.explanation:
                parts.append(f"   {r.explanation}")
    if outliers:
        parts += ["", "Charges well above the Medicare benchmark (please provide an itemized justification):"]
        for i, r in enumerate(outliers, start=1):
            parts.append(f"{i}. {r.message}.")
    parts += ["", "Requested action: correct the billing errors above and send a revised itemized bill."]
    if errors:
        parts.append(f"Total estimated overcharge from billing errors: ${totals.errors}.")
    parts += ["", "These findings come from automated rule checks and were reviewed by a person before sending."]
    return "\n".join(parts)
```

`app/api/letters.py`:
```python
import io
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from docx import Document
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import delete

from app.api.deps import RefDep, SessionDep, WorkspaceDep
from app.api.schemas import LetterEdit, LetterOut
from app.db.models import Case, Letter
from app.llm.grounding import unsupported_numbers
from app.services.audit_log import record
from app.services.cases import case_flags, get_case_or_404, to_claim
from app.services.letters import LETTER_SEVERITIES, build_letter

router = APIRouter()
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _letter_or_404(session: SessionDep, ws: WorkspaceDep, letter_id: uuid.UUID) -> tuple[Letter, Case]:
    letter = session.get(Letter, letter_id)
    case = session.get(Case, letter.case_id) if letter else None
    if letter is None or case is None or case.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="letter not found")
    return letter, case


def _generated_body(session: SessionDep, case: Case, ref: RefDep, today: date) -> str:
    return build_letter(to_claim(case), case_flags(session, case.id), [v.ref_version for v in ref.versions], today)


@router.post("/api/cases/{case_id}/letter", status_code=201, response_model=LetterOut)
def draft_letter(case_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep, ref: RefDep) -> LetterOut:
    case = get_case_or_404(session, ws, case_id)
    rows = case_flags(session, case.id)
    accepted = [r for r in rows if r.status == "accepted" and r.severity in LETTER_SEVERITIES]
    if not accepted:
        raise HTTPException(status_code=409, detail="accept at least one billing error or price outlier first")
    session.execute(delete(Letter).where(Letter.case_id == case.id, Letter.status == "draft"))
    letter = Letter(case_id=case.id, flag_ids=[str(r.id) for r in accepted],
                    body=_generated_body(session, case, ref, datetime.now(UTC).date()))
    session.add(letter)
    case.status = "letter_ready"
    session.flush()
    record(session, ws.id, "letter_drafted", case_id=case.id, detail={"letter_id": str(letter.id)})
    session.commit()
    session.refresh(letter)
    return LetterOut.model_validate(letter, from_attributes=True)


@router.patch("/api/letters/{letter_id}", response_model=LetterOut)
def edit_letter(letter_id: uuid.UUID, edit: LetterEdit, ws: WorkspaceDep, session: SessionDep, ref: RefDep) -> LetterOut:
    letter, case = _letter_or_404(session, ws, letter_id)
    if letter.status != "draft":
        raise HTTPException(status_code=409, detail="approved letters can't be edited")
    generated = _generated_body(session, case, ref, letter.created_at.date())
    added = unsupported_numbers(edit.body, [generated])
    if added:
        raise HTTPException(status_code=422, detail=f"edit adds numbers not in the findings: {', '.join(added)}")
    letter.body = edit.body
    record(session, ws.id, "letter_edited", case_id=case.id, detail={"letter_id": str(letter.id)})
    session.commit()
    return LetterOut.model_validate(letter, from_attributes=True)


@router.post("/api/letters/{letter_id}/approve", response_model=LetterOut)
def approve_letter(letter_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep) -> LetterOut:
    letter, case = _letter_or_404(session, ws, letter_id)
    if letter.status != "draft":
        raise HTTPException(status_code=409, detail="letter is already approved")
    letter.status = "approved"
    letter.approved_at = datetime.now(UTC)
    case.status = "approved"
    record(session, ws.id, "letter_approved", case_id=case.id, detail={"letter_id": str(letter.id)})
    session.commit()
    return LetterOut.model_validate(letter, from_attributes=True)


@router.get("/api/letters/{letter_id}/export")
def export_letter(
    letter_id: uuid.UUID, ws: WorkspaceDep, session: SessionDep, format: Literal["txt", "docx"] = "txt"
) -> Response:
    letter, case = _letter_or_404(session, ws, letter_id)
    if letter.status != "approved":
        raise HTTPException(status_code=409, detail="approve the letter before exporting it")
    case.status = "exported"
    record(session, ws.id, "letter_exported", case_id=case.id, detail={"letter_id": str(letter.id), "format": format})
    session.commit()
    filename = f"dispute-letter-{letter.id}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if format == "txt":
        return PlainTextResponse(letter.body, headers=headers)
    doc = Document()
    for paragraph in letter.body.split("\n"):
        doc.add_paragraph(paragraph)
    buf = io.BytesIO()
    doc.save(buf)
    return Response(buf.getvalue(), media_type=DOCX_TYPE, headers=headers)
```
Include `letters.router` in `app/main.py`.

- [ ] **Step 4: Run tests**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ruff format app tests && ruff check . && mypy app evals scripts
git add app/services/letters.py app/api/letters.py app/main.py tests/test_api_letters.py
git commit -m "feat: dispute letters from accepted flags — numbers locked on edit, approve, TXT/DOCX export

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Demo cases, reset, and daily cleanup cron

**Files:**
- Create: `scripts/build_demo.py`, `data/demo/cases.json` (generated), `app/services/demo.py`, `app/api/demo.py`, `tests/test_demo.py`
- Modify: `app/api/deps.py` (seed on new workspace), `app/main.py` (include router), `vercel.json`

**Interfaces:**
- Consumes: `generate` (Plan 1), `claims_to_bundle`, `parse_fhir`, `run_rules`, `RuleConfig`, `explain_flag`, `default_client`, `explanation_cache_key`, `cached_explanation`, `create_cases`, `run_audit`, `flag_from_row`, `record`, `Workspace`.
- Produces:
  - `app.services.demo`: `DEMO_CASES: Path`, `demo_enabled() -> bool` (env `PRIORPATH_DEMO` != `"0"`), `seed_demo(session, ws, ref) -> int`, `reset_demo(session, ws, ref) -> int`, `cleanup_old_workspaces(session, now=None) -> int` (older than 24 h)
  - Routes: `POST /api/demo/reset -> {"cases": int}`; `GET /api/internal/cleanup` (Bearer `CRON_SECRET`, else 401) `-> {"deleted": int}`
  - New workspaces are seeded with the demo cases (already audited, cached explanations applied) when `demo_enabled()`

- [ ] **Step 1: Build script** — `scripts/build_demo.py`

```python
"""Build the demo cases (and, with --explain, their grounded explanations).

  PYTHONPATH=. python scripts/build_demo.py            # data/demo/cases.json
  PYTHONPATH=. python scripts/build_demo.py --explain  # also data/demo/explanations.json (needs OPENROUTER_API_KEY)
"""

import argparse
import json
import sys
from pathlib import Path

from app.ingest.fhir import claims_to_bundle
from app.llm.cache import DEMO_EXPLANATIONS, explanation_cache_key
from app.llm.client import default_client
from app.llm.explain import explain_flag
from app.models import Severity
from app.reference.normalized import load_normalized
from app.rules import RuleConfig, run_rules
from evals.generate import generate

OUT = Path("data/demo/cases.json")
PROVIDERS = ["Riverside Family Medicine", "Lakeview Orthopedics", "Northgate Imaging Center",
             "Harbor Physical Therapy", "Summit Cardiology"]
EXPLAINED = {Severity.ERROR, Severity.OUTLIER, Severity.LEAD}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--explain", action="store_true")
    a = p.parse_args()
    ref = load_normalized(Path("data/reference/subset"))
    claims = [
        lc.claim.model_copy(update={"provider": PROVIDERS[i % len(PROVIDERS)], "payer": "Medicare (synthetic)"})
        for i, lc in enumerate(generate(ref, 10, seed=2026, error_rate=0.9))
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(claims_to_bundle(claims), indent=1) + "\n")
    print(f"wrote {OUT} ({len(claims)} claims)")
    if not a.explain:
        return 0
    llm = default_client()
    if llm is None:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    cache: dict[str, str] = {}
    flags = [f for c in claims for f in run_rules(c, ref, RuleConfig()) if f.severity in EXPLAINED]
    for f in flags:
        text = explain_flag(f, llm)
        if text:
            cache[explanation_cache_key(f)] = text
    DEMO_EXPLANATIONS.write_text(json.dumps(dict(sorted(cache.items())), indent=1) + "\n")
    print(f"wrote {DEMO_EXPLANATIONS} ({len(cache)}/{len(flags)} grounded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
Run: `PYTHONPATH=. python scripts/build_demo.py` → `wrote data/demo/cases.json (10 claims)`.

- [ ] **Step 2: Write the failing tests** — `tests/test_demo.py`

```python
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app.db.models import Workspace
from app.ingest.fhir import parse_fhir
from app.main import app
from app.services.demo import DEMO_CASES, cleanup_old_workspaces


def test_demo_file_parses_cleanly() -> None:
    result = parse_fhir(json.loads(DEMO_CASES.read_text()))
    assert result.errors == [] and len(result.claims) == 10


def test_new_workspace_gets_audited_demo_cases(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIORPATH_DEMO", "1")
    c = TestClient(app)
    cases = c.get("/api/cases").json()
    assert len(cases) == 10
    assert all(x["status"] == "needs_review" and x["payer_type"] == "medicare" for x in cases)
    assert sum(x["error_count"] > 0 or x["outlier_amount"] != "0.00" for x in cases) >= 7


def test_reset_restores_demo(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIORPATH_DEMO", "1")
    c = TestClient(app)
    first_ids = {x["id"] for x in c.get("/api/cases").json()}
    assert c.post("/api/demo/reset").json() == {"cases": 10}
    assert {x["id"] for x in c.get("/api/cases").json()}.isdisjoint(first_ids)


def test_cleanup_requires_cron_secret_and_deletes_old_workspaces(db: Engine) -> None:
    c = TestClient(app)
    assert c.get("/api/internal/cleanup").status_code == 401
    assert c.get("/api/internal/cleanup", headers={"Authorization": "Bearer wrong"}).status_code == 401
    c.get("/api/workspace")
    with Session(db) as s:
        s.execute(update(Workspace).values(created_at=datetime.now(UTC) - timedelta(hours=25)))
        s.add(Workspace())
        s.commit()
    r = c.get("/api/internal/cleanup", headers={"Authorization": "Bearer test-cron-secret"})
    assert r.json() == {"deleted": 1}
    with Session(db) as s:
        assert len(s.scalars(select(Workspace)).all()) == 1
        assert cleanup_old_workspaces(s) == 0
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_demo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.demo'`.

- [ ] **Step 4: Implement** — `app/services/demo.py`

```python
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Case, Workspace
from app.ingest.fhir import parse_fhir
from app.llm.cache import cached_explanation
from app.reference.base import InMemoryReference
from app.services.audit_log import record
from app.services.audit_run import run_audit
from app.services.cases import create_cases, flag_from_row

DEMO_CASES = Path(__file__).resolve().parent.parent.parent / "data" / "demo" / "cases.json"
WORKSPACE_TTL = timedelta(hours=24)


def demo_enabled() -> bool:
    return os.environ.get("PRIORPATH_DEMO", "1") != "0"


def seed_demo(session: Session, ws: Workspace, ref: InMemoryReference) -> int:
    if not DEMO_CASES.exists():
        return 0
    result = parse_fhir(json.loads(DEMO_CASES.read_text()))
    cases = create_cases(session, ws, result.claims, payer_type="medicare", actor="system")
    for case in cases:
        for row in run_audit(session, case, ref, actor="system"):
            text = cached_explanation(flag_from_row(row, str(case.claim["id"])))
            if text:
                row.explanation, row.explanation_status = text, "ready"
    return len(cases)


def reset_demo(session: Session, ws: Workspace, ref: InMemoryReference) -> int:
    session.execute(delete(Case).where(Case.workspace_id == ws.id))
    count = seed_demo(session, ws, ref)
    record(session, ws.id, "demo_reset", detail={"cases": count})
    return count


def cleanup_old_workspaces(session: Session, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - WORKSPACE_TTL
    deleted = session.execute(delete(Workspace).where(Workspace.created_at < cutoff)).rowcount
    session.commit()
    return int(deleted or 0)
```

In `app/api/deps.py` `current_workspace`, after creating the workspace and before `session.commit()`:
```python
        session.flush()
        if demo_enabled():
            seed_demo(session, ws, ref)
```
(import `demo_enabled, seed_demo` from `app.services.demo` inside the function body to avoid an import cycle: `app.services.cases` imports `app.api.schemas`, which is fine, but keep the deps module import-light.)

`app/api/demo.py`:
```python
import hmac
import os

from fastapi import APIRouter, Header, HTTPException

from app.api.deps import RefDep, SessionDep, WorkspaceDep
from app.services.demo import cleanup_old_workspaces, reset_demo

router = APIRouter()


@router.post("/api/demo/reset")
def demo_reset(ws: WorkspaceDep, session: SessionDep, ref: RefDep) -> dict[str, int]:
    count = reset_demo(session, ws, ref)
    session.commit()
    return {"cases": count}


@router.get("/api/internal/cleanup")
def cleanup(session: SessionDep, authorization: str | None = Header(default=None)) -> dict[str, int]:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret or not hmac.compare_digest(authorization or "", f"Bearer {secret}"):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"deleted": cleanup_old_workspaces(session)}
```
Include `demo.router` in `app/main.py`.

`vercel.json` becomes:
```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "functions": {
    "app/main.py": { "includeFiles": "data/**" }
  },
  "crons": [{ "path": "/api/internal/cleanup", "schedule": "0 5 * * *" }]
}
```
(`.vercelignore` already excludes `data/reference/raw`.)

- [ ] **Step 5: Run tests**

Run: `pytest -q`
Expected: all pass (other test files keep `PRIORPATH_DEMO=0` from `conftest.py`, so their workspaces start empty).

- [ ] **Step 6: Commit**

```bash
ruff format app tests scripts && ruff check . && mypy app evals scripts
git add scripts/build_demo.py data/demo/cases.json app/services/demo.py app/api app/main.py vercel.json tests/test_demo.py
git commit -m "feat: seeded demo workspaces, demo reset, and daily cleanup cron

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Production — Neon, secrets, migrations, demo explanations, deploy, smoke test

**Files:**
- Create: `scripts/smoke.py`, `data/demo/explanations.json` (generated)
- Modify: `README.md` (one "Try the API" section under the v2 banner)

**Interfaces:**
- Consumes: everything above.
- Produces: production at `https://priorpath.vercel.app` with a working API flow; `scripts/smoke.py BASE_URL` exits 0.

- [ ] **Step 1: Smoke script** — `scripts/smoke.py`

```python
"""End-to-end check against a deployed PriorPath: demo → explain → review → letter → export.

  python scripts/smoke.py https://priorpath.vercel.app
"""

import json
import sys

import httpx


def main() -> int:
    base = sys.argv[1].rstrip("/")
    with httpx.Client(base_url=base, timeout=120) as c:
        assert c.get("/api/health").json() == {"status": "ok"}
        cases = c.get("/api/cases").json()
        assert len(cases) == 10, f"expected 10 demo cases, got {len(cases)}"
        case = next(x for x in cases if x["error_count"] > 0)
        stream = c.post(f"/api/cases/{case['id']}/explain").text
        done = json.loads(stream.strip().split("\n\n")[-1].split("data: ", 1)[1])
        detail = c.get(f"/api/cases/{case['id']}").json()
        error_flag = next(f for f in detail["flags"] if f["severity"] == "error")
        assert c.patch(f"/api/flags/{error_flag['id']}", json={"status": "accepted"}).status_code == 200
        letter = c.post(f"/api/cases/{case['id']}/letter").json()
        assert c.post(f"/api/letters/{letter['id']}/approve").json()["status"] == "approved"
        exported = c.get(f"/api/letters/{letter['id']}/export?format=txt")
        assert exported.status_code == 200 and error_flag["message"] in exported.text
        print(f"ok: case {case['claim_id']}, explanations {done}, letter {len(exported.text)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Provision Neon (human)** — in the Vercel dashboard for project `priorpath`: Storage → Create Database → Neon (free) → connect to Production and Preview. This adds `DATABASE_URL` (pooled) and `DATABASE_URL_UNPOOLED` env vars. (CLI alternative: `vercel integration add neon`.)

- [ ] **Step 3: Secrets (human enters the OpenRouter key)**

```bash
vercel env add OPENROUTER_API_KEY production preview      # paste the key when prompted
python -c "import secrets; print(secrets.token_urlsafe(32))" | vercel env add SESSION_SECRET production preview
python -c "import secrets; print(secrets.token_urlsafe(32))" | vercel env add CRON_SECRET production preview
```
Never echo the values into the transcript.

- [ ] **Step 4: Migrate Neon**

```bash
vercel env pull .env.production.local --environment=production
set -a; source .env.production.local; set +a
DATABASE_URL="${DATABASE_URL_UNPOOLED:-$DATABASE_URL}" alembic upgrade head
rm .env.production.local
```
Expected: `Running upgrade -> <rev>, initial schema`.

- [ ] **Step 5: Pick the explain model on our own data, then build demo explanations**

Compare two cheap candidates by grounding-pass rate on the demo flags (key from the human, never echoed):
```bash
for m in deepseek/deepseek-v4-pro deepseek/deepseek-v4-flash; do
  EXPLAIN_MODEL=$m PYTHONPATH=. python scripts/build_demo.py --explain | tail -1
  cp data/demo/explanations.json "/tmp/expl-$(echo $m | tr / _).json"
done
```
Keep the model with the higher `N/M grounded` (ties → the cheaper one); read 5 of its explanations for tone and accuracy. If it isn't `deepseek/deepseek-v4-pro`, set `vercel env add EXPLAIN_MODEL production preview` to the winner and note it in the README. Restore that model's file to `data/demo/explanations.json`.
Expected: N close to M for the winner. Read a few entries: plain English, no made-up numbers. `git diff data/demo/cases.json` must be empty (same seed).

- [ ] **Step 6: README section** — under the existing v2 banner in `README.md` add:

```markdown
> **Try the v2 API:** open https://priorpath.vercel.app/api/docs. Your browser gets its own demo workspace with
> 10 synthetic claims. Try `GET /api/cases`, then `POST /api/cases/{id}/explain`, `PATCH /api/flags/{id}`,
> `POST /api/cases/{id}/letter`, `POST /api/letters/{id}/approve`, and `GET /api/letters/{id}/export`.
```

- [ ] **Step 7: Commit, push (preview deploy), verify, promote**

```bash
git add scripts/smoke.py data/demo/explanations.json README.md
git commit -m "feat: production smoke test, demo explanations, API try-it notes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
```
Wait for the GitHub Actions `ci` run to be green and the Vercel preview to be Ready. Then:
```bash
vercel deploy --prod
python scripts/smoke.py https://priorpath.vercel.app
```
Expected: `ok: case ..., explanations {...}, letter ... chars`. If the bundle exceeds the size limit, check `.vercelignore` first; if the function can't reach Neon, check that `DATABASE_URL` is set for Production.

---

## Self-Review (done while writing)

- **Spec coverage:** §6 explanation (Tasks 6–7) and letters (Task 8); §7 lifecycle (uploaded → needs_review → letter_ready → approved → exported; PDF-only states deferred), queue/detail data (Task 4), accept/reject (Task 5), letter review/export (Task 8), audit log (every mutating route records an event), API list (Tasks 4–9 minus `PATCH /lines`, deferred), demo workspaces + reset + cron + rate limit (Tasks 3, 6, 9); §9 error table: LLM down/budget (Task 7), invalid FHIR partial success (Task 4), coverage notice (Plan 1), upload >4 MB (Task 4); §10 secrets env-only, retention 24 h (Task 9), LLM sees flags only (Task 6 test); §11 integration tests on real Postgres (Task 1 + every API test), CI service (Task 1); §16 items 1–2 (Task 2).
- **Deviations** are listed at the top for approval.
- **Type consistency:** `create_cases(session, ws, claims, payer_type, actor)` used in Tasks 4 and 9; `run_audit(session, case, ref, actor)` in Tasks 5 and 9; `explain_row` returns `(status, reason)` in Task 7; `flag_from_row(row, claim_id)` everywhere; `LETTER_SEVERITIES` shared by service and route.
- **Review Focus:** items 1–5 have tests in Tasks 4, 4/5/8, 6, 7 and 8 respectively.
