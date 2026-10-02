# PriorPath v2 — Plan 1: Core Engine, Evals, First Deploy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tested rule engine (R0–R5) over versioned CMS reference data, FHIR EOB input, a synthetic-claim eval suite enforced in CI, and a hello-world FastAPI deploy on Vercel.

**Architecture:** Pure Python core. `app/models.py` defines claims, lines and flags. `app/reference/` loads versioned CMS tables (normalized CSVs committed as a small subset) behind a `Reference` protocol. `app/rules/` holds one pure function per rule. `app/ingest/fhir.py` parses and writes FHIR `ExplanationOfBenefit`. `evals/` generates labeled synthetic claims, runs them through FHIR → rules, and gates CI on precision/recall. `app/main.py` is the FastAPI entrypoint Vercel deploys.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, ruff, mypy (strict), uv (local env), GitHub Actions, Vercel (Python on Fluid Compute). openpyxl only in the offline subset-build script.

**Spec:** `docs/superpowers/specs/2026-10-01-priorpath-v2-bill-audit-design.md` (this plan implements §4.1–4.3, §5, §8 rows 1–2, §11 CI, and the week-1 row of §13). Plans 2 (API, Postgres, UI, explanations, letters) and 3 (PDF, PHI, hardening) are written after this plan lands.

## Global Constraints

- Python `3.12` (`.python-version`); no other runtime version.
- Money is `decimal.Decimal`, quantized to `0.01`; never `float` inside the engine.
- Rules are pure: no network, no LLM, no database, no global state.
- Every flag carries `rule_id`, `ref_version` (or `None` for claim-only rules), evidence row, `est_overcharge`, and `severity` ∈ `error | outlier | lead | notice`.
- Errors, outliers, leads and notices are counted separately; eval gates use `error` and `outlier` only.
- CPT descriptors are AMA-licensed: committed data contains code numbers only, never descriptors.
- Committed reference data lives in `data/reference/subset/`; raw CMS downloads live in `data/reference/raw/` and are git-ignored.
- `.env` is never committed. No secrets are needed in this plan.
- Vercel Hobby limits: 300 s per request, 500 MB Python bundle, 4.5 MB request body.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Zero/negative units or negative charges on EOB items** (adjustment lines are common): that item is rejected with its JSON path; the rest of the claim still parses. → test in Task 8.
2. **Lowercase or padded codes and modifiers** (`" 99213 "`, `"xs"`): normalized to uppercase/trimmed, so rules match. → test in Task 1 (model) and Task 5 (NCCI modifier).
3. **Repeat-procedure modifiers 76, 77, 91**: a legitimately repeated service is not a duplicate. → test in Task 4.
4. **Date of service exactly on an NCCI deletion date**: the edit no longer applies on that date (deletion date is exclusive). → tests in Tasks 2 and 5.
5. **A claim spanning covered and uncovered dates**: only uncovered lines get the R0 "cannot audit" notice; covered lines are still audited. → test in Task 4.

---

## File Structure

```
PriorPath/                         (branch v2-bill-audit)
├── .python-version                3.12
├── .gitignore                     + .vercel, data/reference/raw/
├── pyproject.toml                 tool config only (ruff, mypy, pytest) — no [project] table
├── requirements.txt               runtime deps (what Vercel installs)
├── requirements-dev.txt           dev/test deps
├── vercel.json                    bundle data/reference/subset with the function
├── app/
│   ├── __init__.py                __version__
│   ├── main.py                    FastAPI: /api/health, /api/version
│   ├── models.py                  LineItem, Claim, Evidence, Flag, Severity, make_flag, money
│   ├── reference/
│   │   ├── __init__.py
│   │   ├── base.py                dataclasses, Reference protocol, InMemoryReference
│   │   ├── normalized.py          load_normalized / write_normalized (CSV)
│   │   └── cms_adapters.py        raw CMS rows → dataclasses (find_header, parse_ptp/mue/pfs)
│   ├── rules/
│   │   ├── __init__.py            RuleConfig, Rule, ALL_RULES, run_rules
│   │   ├── coverage.py            R0
│   │   ├── duplicates.py          R1
│   │   ├── ncci.py                R2
│   │   ├── mue.py                 R3
│   │   ├── invalid_code.py        R4
│   │   └── price.py               R5
│   └── ingest/
│       ├── __init__.py
│       └── fhir.py                parse_fhir, claim_to_eob, claims_to_bundle
├── scripts/
│   └── build_reference_subset.py  offline: raw CMS files → data/reference/subset
├── data/reference/
│   ├── codes.txt                  code list for the subset
│   ├── SOURCES.md                 CMS URLs, release names, download dates
│   └── subset/                    versions.csv ptp.csv mue.csv fees.csv codes.csv
├── evals/
│   ├── __init__.py
│   ├── generate.py                LabeledClaim, generate
│   ├── run.py                     Report, evaluate, CLI with gates
│   └── results/latest.md          committed eval table
├── tests/
│   ├── __init__.py
│   ├── helpers.py                 line(), claim(), FIXTURE_REF
│   ├── fixtures/reference/        tiny synthetic normalized CSVs
│   └── test_*.py
└── .github/workflows/ci.yml
```

v1 files removed in Task 0 (kept in git history): `agents/ graph/ tools/ models/ api/ frontend/ scripts/ingest_guidelines.py data/cms_guidelines/ render.yaml railway.toml Procfile Dockerfile docker-compose.yml .github/workflows/keep-warm.yml tests/test_graph.py`.

---

### Task 0: Repo restructure, tooling, FastAPI health endpoint, CI

**Files:**
- Delete: v1 files listed above
- Create: `.python-version`, `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `app/__init__.py`, `app/main.py`, `tests/test_main.py`, `.github/workflows/ci.yml`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `app.__version__: str`; FastAPI `app.main.app` with `GET /api/health -> {"status": "ok"}`; OpenAPI at `/api/docs`.

- [ ] **Step 1: Remove v1 code**

Check `.mcp.json` first: `cat .mcp.json`. If it only configures v1 services (Qdrant/Supabase), delete it too; otherwise keep it.

```bash
git rm -r -q agents graph tools models api frontend data/cms_guidelines \
  scripts/ingest_guidelines.py render.yaml railway.toml Procfile Dockerfile \
  docker-compose.yml .github/workflows/keep-warm.yml tests/test_graph.py
```

- [ ] **Step 2: Tooling files**

`.python-version`:
```
3.12
```

`requirements.txt`:
```
fastapi>=0.115,<1
pydantic>=2.9,<3
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.3
httpx>=0.27
ruff>=0.6
mypy>=1.11
uvicorn>=0.30
openpyxl>=3.1
```

`pyproject.toml`:
```toml
[tool.ruff]
line-length = 110
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]

[[tool.mypy.overrides]]
module = ["openpyxl", "openpyxl.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

Append to `.gitignore`:
```
.vercel
data/reference/raw/
```

- [ ] **Step 3: Create the local environment**

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install -r requirements-dev.txt
```
Expected: installs without errors; `python --version` → `Python 3.12.x`.

- [ ] **Step 4: Write the failing test** — `tests/test_main.py`

```python
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_docs_served_under_api() -> None:
    assert client.get("/api/docs").status_code == 200
```

- [ ] **Step 5: Run it to verify it fails**

Run: `pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 6: Implement**

`app/__init__.py`:
```python
__version__ = "2.0.0.dev0"
```

`app/main.py`:
```python
from fastapi import FastAPI

from app import __version__

app = FastAPI(
    title="PriorPath",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 7: Run tests, lint, types**

Run: `pytest -q && ruff check . && ruff format --check . && mypy app`
Expected: 2 passed; ruff and mypy clean. (Run `ruff format .` first if the format check complains.)

- [ ] **Step 8: CI workflow** — `.github/workflows/ci.yml`

```yaml
name: ci
on:
  push:
  pull_request:
jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: requirements-dev.txt
      - run: pip install -r requirements-dev.txt
      - run: ruff check .
      - run: ruff format --check .
      - run: mypy app
      - run: pytest -q
```

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "chore: retire v1 prior-auth code, add v2 tooling and health endpoint

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Core models

**Files:**
- Create: `app/models.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `money(v: Decimal) -> Decimal` (quantize to 0.01)
  - `class Severity(StrEnum)`: `ERROR="error"`, `OUTLIER="outlier"`, `LEAD="lead"`, `NOTICE="notice"`
  - `class LineSource(StrEnum)`: `STRUCTURED`, `EXTRACTED`
  - `class LineItem(BaseModel)`: `id: str, code: str, modifiers: list[str], units: int (>0), charge: Decimal (>=0), date_of_service: date, place_of_service: str | None, diagnosis_codes: list[str], source: LineSource, confidence: float | None`; property `unit_price -> Decimal`
  - `class Claim(BaseModel)`: `id, patient_pseudonym, provider: str | None, payer: str | None, lines: list[LineItem], source: Literal["fhir","pdf"]`
  - `class Evidence(BaseModel)`: `table: str, ref_version: str | None, row: dict[str, str]`
  - `class Flag(BaseModel)`: `id, claim_id, rule_id, severity, line_ids: list[str], evidence, est_overcharge: Decimal, message: str, explanation: str | None, status: Literal["open","accepted","rejected"], reject_reason: str | None`
  - `make_flag(claim, rule_id, severity, lines, evidence, overcharge, message) -> Flag` with `id = f"{claim.id}:{rule_id}:{'+'.join(line ids)}"`

- [ ] **Step 1: Write the failing tests** — `tests/test_models.py`

```python
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import Claim, Evidence, LineItem, Severity, make_flag, money


def _line(**kw: object) -> LineItem:
    base: dict[str, object] = {
        "id": "L1",
        "code": "99213",
        "units": 1,
        "charge": Decimal("150.00"),
        "date_of_service": date(2026, 10, 15),
    }
    base.update(kw)
    return LineItem(**base)  # type: ignore[arg-type]


def test_code_and_modifiers_are_normalized() -> None:
    line = _line(code=" g0008 ", modifiers=[" xs", "rt ", ""])
    assert line.code == "G0008"
    assert line.modifiers == ["XS", "RT"]


def test_units_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _line(units=0)


def test_charge_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        _line(charge=Decimal("-1.00"))


def test_empty_code_rejected() -> None:
    with pytest.raises(ValidationError):
        _line(code="   ")


def test_unit_price() -> None:
    assert _line(units=4, charge=Decimal("90.00")).unit_price == Decimal("22.5")


def test_money_rounds_to_cents() -> None:
    assert money(Decimal("10.005")) == Decimal("10.01")


def test_make_flag_builds_deterministic_id_and_rounds() -> None:
    l1, l2 = _line(id="L1"), _line(id="L2")
    claim = Claim(id="C9", patient_pseudonym="P-1", lines=[l1, l2])
    flag = make_flag(
        claim,
        "R1",
        Severity.ERROR,
        [l1, l2],
        Evidence(table="claim_lines", ref_version=None, row={"code": "99213"}),
        Decimal("150.004"),
        "Line L2 duplicates line L1",
    )
    assert flag.id == "C9:R1:L1+L2"
    assert flag.line_ids == ["L1", "L2"]
    assert flag.est_overcharge == Decimal("150.00")
    assert flag.status == "open"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`.

- [ ] **Step 3: Implement** — `app/models.py`

```python
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CENT = Decimal("0.01")


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


class Severity(StrEnum):
    ERROR = "error"
    OUTLIER = "outlier"
    LEAD = "lead"
    NOTICE = "notice"


class LineSource(StrEnum):
    STRUCTURED = "structured"
    EXTRACTED = "extracted"


class LineItem(BaseModel):
    id: str
    code: str
    modifiers: list[str] = Field(default_factory=list)
    units: int = Field(gt=0)
    charge: Decimal = Field(ge=0)
    date_of_service: date
    place_of_service: str | None = None
    diagnosis_codes: list[str] = Field(default_factory=list)
    source: LineSource = LineSource.STRUCTURED
    confidence: float | None = None

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("code is empty")
        return v

    @field_validator("modifiers")
    @classmethod
    def _normalize_modifiers(cls, v: list[str]) -> list[str]:
        return [m.strip().upper() for m in v if m.strip()]

    @property
    def unit_price(self) -> Decimal:
        return self.charge / self.units


class Claim(BaseModel):
    id: str
    patient_pseudonym: str
    provider: str | None = None
    payer: str | None = None
    lines: list[LineItem]
    source: Literal["fhir", "pdf"] = "fhir"


class Evidence(BaseModel):
    table: str
    ref_version: str | None
    row: dict[str, str]


class Flag(BaseModel):
    id: str
    claim_id: str
    rule_id: str
    severity: Severity
    line_ids: list[str]
    evidence: Evidence
    est_overcharge: Decimal
    message: str
    explanation: str | None = None
    status: Literal["open", "accepted", "rejected"] = "open"
    reject_reason: str | None = None


def make_flag(
    claim: Claim,
    rule_id: str,
    severity: Severity,
    lines: Sequence[LineItem],
    evidence: Evidence,
    overcharge: Decimal,
    message: str,
) -> Flag:
    ids = [line.id for line in lines]
    return Flag(
        id=f"{claim.id}:{rule_id}:{'+'.join(ids)}",
        claim_id=claim.id,
        rule_id=rule_id,
        severity=severity,
        line_ids=ids,
        evidence=evidence,
        est_overcharge=money(overcharge),
        message=message,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_models.py -v && mypy app`
Expected: 7 passed; mypy clean.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: core claim, line and flag models

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Versioned reference data (protocol, in-memory store, normalized CSV)

**Files:**
- Create: `app/reference/__init__.py` (empty), `app/reference/base.py`, `app/reference/normalized.py`
- Create: `tests/fixtures/reference/{versions,ptp,mue,fees,codes}.csv`, `tests/helpers.py`, `tests/test_reference.py`

**Interfaces:**
- Consumes: `money` from Task 1.
- Produces (in `app/reference/base.py`):
  - `KINDS = ("ncci", "mue", "pfs")`
  - `@dataclass(frozen=True) RefVersion(ref_version: str, kind: str, valid_from: date, valid_to: date)`
  - `@dataclass(frozen=True) PtpEdit(col1: str, col2: str, effective: date, deleted: date | None, modifier_indicator: str, ref_version: str)` with `active_on(dos: date) -> bool` (deleted date exclusive)
  - `@dataclass(frozen=True) MueLimit(code: str, max_units: int, mai: int, ref_version: str)`
  - `@dataclass(frozen=True) FeeRate(code: str, nonfacility: Decimal, facility: Decimal, ref_version: str)`
  - `@dataclass(frozen=True) CodeStatus(code: str, status: str, ref_version: str)`
  - `class Reference(Protocol)`: `version_for(kind, dos) -> RefVersion | None`, `covers(dos) -> bool`, `ptp(col1, col2, dos) -> PtpEdit | None`, `mue(code, dos) -> MueLimit | None`, `fee(code, dos) -> FeeRate | None`, `code_status(code, dos) -> CodeStatus | None`
  - `class InMemoryReference` implementing it; public lists `versions, ptp_edits, mue_limits, fees, code_statuses`
- Produces (in `app/reference/normalized.py`): `load_normalized(directory: Path) -> InMemoryReference`, `write_normalized(ref: InMemoryReference, directory: Path) -> None`
- Produces (in `tests/helpers.py`): `FIXTURE_REF: InMemoryReference`, `DOS = date(2026, 10, 15)`, `line(...) -> LineItem`, `claim(*lines) -> Claim`

- [ ] **Step 1: Fixture CSVs** (synthetic values; real code numbers, made-up edits)

`tests/fixtures/reference/versions.csv`:
```
ref_version,kind,valid_from,valid_to
NCCI-TEST,ncci,2026-10-01,2026-12-31
MUE-TEST,mue,2026-10-01,2026-12-31
PFS-TEST,pfs,2026-01-01,2026-12-31
```

`tests/fixtures/reference/ptp.csv`:
```
col1,col2,effective,deleted,modifier_indicator,ref_version
93000,93005,2000-01-01,,0,NCCI-TEST
45380,45378,2000-01-01,,1,NCCI-TEST
29881,29880,2000-01-01,2026-11-15,0,NCCI-TEST
20610,20611,2000-01-01,,9,NCCI-TEST
```

`tests/fixtures/reference/mue.csv`:
```
code,max_units,mai,ref_version
99213,1,2,MUE-TEST
96372,4,1,MUE-TEST
97110,6,3,MUE-TEST
93000,1,2,MUE-TEST
```

`tests/fixtures/reference/fees.csv`:
```
code,nonfacility_rate,facility_rate,ref_version
99213,92.15,65.40,PFS-TEST
97110,30.00,28.00,PFS-TEST
96372,15.00,15.00,PFS-TEST
93000,17.00,17.00,PFS-TEST
```

`tests/fixtures/reference/codes.csv`:
```
code,status,ref_version
99213,A,PFS-TEST
97110,A,PFS-TEST
96372,A,PFS-TEST
93000,A,PFS-TEST
99201,D,PFS-TEST
```

- [ ] **Step 2: Write the failing tests** — `tests/test_reference.py`

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.reference.normalized import load_normalized, write_normalized
from tests.helpers import FIXTURE_DIR, FIXTURE_REF

D = date(2026, 10, 15)


def test_coverage_requires_all_three_kinds() -> None:
    assert FIXTURE_REF.covers(D)
    assert not FIXTURE_REF.covers(date(2026, 9, 30))  # PFS covers it, NCCI/MUE don't
    assert FIXTURE_REF.version_for("pfs", date(2026, 9, 30)) is not None


def test_ptp_lookup_is_directional() -> None:
    assert FIXTURE_REF.ptp("93000", "93005", D) is not None
    assert FIXTURE_REF.ptp("93005", "93000", D) is None


def test_ptp_deletion_date_is_exclusive() -> None:
    assert FIXTURE_REF.ptp("29881", "29880", date(2026, 11, 14)) is not None
    assert FIXTURE_REF.ptp("29881", "29880", date(2026, 11, 15)) is None


def test_ptp_outside_coverage_returns_none() -> None:
    assert FIXTURE_REF.ptp("93000", "93005", date(2027, 1, 5)) is None


def test_mue_fee_status_lookups() -> None:
    mue = FIXTURE_REF.mue("96372", D)
    assert mue is not None and mue.max_units == 4 and mue.mai == 1
    fee = FIXTURE_REF.fee("99213", D)
    assert fee is not None and fee.nonfacility == Decimal("92.15") and fee.facility == Decimal("65.40")
    status = FIXTURE_REF.code_status("99201", D)
    assert status is not None and status.status == "D"
    assert FIXTURE_REF.code_status("0001U", D) is None


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    write_normalized(FIXTURE_REF, tmp_path)
    again = load_normalized(tmp_path)
    assert again.versions == FIXTURE_REF.versions
    assert again.ptp_edits == FIXTURE_REF.ptp_edits
    assert again.mue_limits == FIXTURE_REF.mue_limits
    assert again.fees == FIXTURE_REF.fees
    assert again.code_statuses == FIXTURE_REF.code_statuses
    assert {p.name for p in tmp_path.iterdir()} == {p.name for p in FIXTURE_DIR.iterdir()}
```

`tests/helpers.py`:
```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models import Claim, LineItem
from app.reference.normalized import load_normalized

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "reference"
FIXTURE_REF = load_normalized(FIXTURE_DIR)
DOS = date(2026, 10, 15)


def line(
    id: str = "L1",
    code: str = "99213",
    units: int = 1,
    charge: str = "150.00",
    dos: date = DOS,
    modifiers: list[str] | None = None,
    pos: str | None = "11",
) -> LineItem:
    return LineItem(
        id=id,
        code=code,
        units=units,
        charge=Decimal(charge),
        date_of_service=dos,
        modifiers=modifiers or [],
        place_of_service=pos,
    )


def claim(*lines: LineItem) -> Claim:
    return Claim(id="C1", patient_pseudonym="P-1", lines=list(lines))
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_reference.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.reference'`.

- [ ] **Step 4: Implement** — `app/reference/base.py`

```python
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

KINDS = ("ncci", "mue", "pfs")


@dataclass(frozen=True)
class RefVersion:
    ref_version: str
    kind: str
    valid_from: date
    valid_to: date


@dataclass(frozen=True)
class PtpEdit:
    col1: str
    col2: str
    effective: date
    deleted: date | None
    modifier_indicator: str  # "0" never allowed, "1" allowed with modifier, "9" not applicable
    ref_version: str

    def active_on(self, dos: date) -> bool:
        return self.effective <= dos and (self.deleted is None or dos < self.deleted)


@dataclass(frozen=True)
class MueLimit:
    code: str
    max_units: int
    mai: int  # 1 = per line, 2/3 = per date of service
    ref_version: str


@dataclass(frozen=True)
class FeeRate:
    code: str
    nonfacility: Decimal
    facility: Decimal
    ref_version: str


@dataclass(frozen=True)
class CodeStatus:
    code: str
    status: str  # PFS status code; "D" = deleted
    ref_version: str


class Reference(Protocol):
    def version_for(self, kind: str, dos: date) -> RefVersion | None: ...
    def covers(self, dos: date) -> bool: ...
    def ptp(self, col1: str, col2: str, dos: date) -> PtpEdit | None: ...
    def mue(self, code: str, dos: date) -> MueLimit | None: ...
    def fee(self, code: str, dos: date) -> FeeRate | None: ...
    def code_status(self, code: str, dos: date) -> CodeStatus | None: ...


class InMemoryReference:
    def __init__(
        self,
        versions: Iterable[RefVersion],
        ptp_edits: Iterable[PtpEdit],
        mue_limits: Iterable[MueLimit],
        fees: Iterable[FeeRate],
        code_statuses: Iterable[CodeStatus],
    ) -> None:
        self.versions = list(versions)
        self.ptp_edits = list(ptp_edits)
        self.mue_limits = list(mue_limits)
        self.fees = list(fees)
        self.code_statuses = list(code_statuses)
        self._ptp: dict[tuple[str, str, str], list[PtpEdit]] = defaultdict(list)
        for e in self.ptp_edits:
            self._ptp[(e.ref_version, e.col1, e.col2)].append(e)
        self._mue = {(m.ref_version, m.code): m for m in self.mue_limits}
        self._fee = {(f.ref_version, f.code): f for f in self.fees}
        self._status = {(c.ref_version, c.code): c for c in self.code_statuses}

    def version_for(self, kind: str, dos: date) -> RefVersion | None:
        for v in self.versions:
            if v.kind == kind and v.valid_from <= dos <= v.valid_to:
                return v
        return None

    def covers(self, dos: date) -> bool:
        return all(self.version_for(kind, dos) is not None for kind in KINDS)

    def ptp(self, col1: str, col2: str, dos: date) -> PtpEdit | None:
        v = self.version_for("ncci", dos)
        if v is None:
            return None
        for edit in self._ptp.get((v.ref_version, col1, col2), []):
            if edit.active_on(dos):
                return edit
        return None

    def mue(self, code: str, dos: date) -> MueLimit | None:
        v = self.version_for("mue", dos)
        return None if v is None else self._mue.get((v.ref_version, code))

    def fee(self, code: str, dos: date) -> FeeRate | None:
        v = self.version_for("pfs", dos)
        return None if v is None else self._fee.get((v.ref_version, code))

    def code_status(self, code: str, dos: date) -> CodeStatus | None:
        v = self.version_for("pfs", dos)
        return None if v is None else self._status.get((v.ref_version, code))
```

`app/reference/normalized.py`:
```python
import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.reference.base import CodeStatus, FeeRate, InMemoryReference, MueLimit, PtpEdit, RefVersion


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _date_or_none(v: str) -> date | None:
    return date.fromisoformat(v) if v else None


def load_normalized(directory: Path) -> InMemoryReference:
    return InMemoryReference(
        versions=[
            RefVersion(
                r["ref_version"],
                r["kind"],
                date.fromisoformat(r["valid_from"]),
                date.fromisoformat(r["valid_to"]),
            )
            for r in _rows(directory / "versions.csv")
        ],
        ptp_edits=[
            PtpEdit(
                r["col1"],
                r["col2"],
                date.fromisoformat(r["effective"]),
                _date_or_none(r["deleted"]),
                r["modifier_indicator"],
                r["ref_version"],
            )
            for r in _rows(directory / "ptp.csv")
        ],
        mue_limits=[
            MueLimit(r["code"], int(r["max_units"]), int(r["mai"]), r["ref_version"])
            for r in _rows(directory / "mue.csv")
        ],
        fees=[
            FeeRate(r["code"], Decimal(r["nonfacility_rate"]), Decimal(r["facility_rate"]), r["ref_version"])
            for r in _rows(directory / "fees.csv")
        ],
        code_statuses=[
            CodeStatus(r["code"], r["status"], r["ref_version"]) for r in _rows(directory / "codes.csv")
        ],
    )


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_normalized(ref: InMemoryReference, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _write(
        directory / "versions.csv",
        ["ref_version", "kind", "valid_from", "valid_to"],
        [[v.ref_version, v.kind, v.valid_from.isoformat(), v.valid_to.isoformat()] for v in ref.versions],
    )
    _write(
        directory / "ptp.csv",
        ["col1", "col2", "effective", "deleted", "modifier_indicator", "ref_version"],
        [
            [
                e.col1,
                e.col2,
                e.effective.isoformat(),
                e.deleted.isoformat() if e.deleted else "",
                e.modifier_indicator,
                e.ref_version,
            ]
            for e in ref.ptp_edits
        ],
    )
    _write(
        directory / "mue.csv",
        ["code", "max_units", "mai", "ref_version"],
        [[m.code, str(m.max_units), str(m.mai), m.ref_version] for m in ref.mue_limits],
    )
    _write(
        directory / "fees.csv",
        ["code", "nonfacility_rate", "facility_rate", "ref_version"],
        [[f.code, str(f.nonfacility), str(f.facility), f.ref_version] for f in ref.fees],
    )
    _write(
        directory / "codes.csv",
        ["code", "status", "ref_version"],
        [[c.code, c.status, c.ref_version] for c in ref.code_statuses],
    )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_reference.py -v && ruff format . && ruff check . && mypy app`
Expected: 6 passed; clean.

- [ ] **Step 6: Commit**

```bash
git add app/reference tests/helpers.py tests/fixtures tests/test_reference.py
git commit -m "feat: versioned CMS reference store with normalized CSV load/write

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: CMS adapters and the committed reference subset

**Files:**
- Create: `app/reference/cms_adapters.py`, `tests/test_cms_adapters.py`, `scripts/build_reference_subset.py`, `data/reference/codes.txt`, `data/reference/SOURCES.md`, `data/reference/subset/*.csv` (generated)

**Interfaces:**
- Consumes: dataclasses from Task 2, `money` from Task 1, `write_normalized`.
- Produces:
  - `Matcher = Callable[[str], bool]`
  - `find_header(rows: list[list[str]], matchers: dict[str, Matcher]) -> tuple[int, dict[str, int]]` — returns (first data row index, column index per key); header may span 1–3 rows; raises `ValueError` if not found
  - `parse_ptp(rows, ref_version) -> list[PtpEdit]`
  - `parse_mue(rows, ref_version) -> list[MueLimit]`
  - `parse_pfs(rows, ref_version) -> tuple[list[FeeRate], list[CodeStatus]]`
  - Committed `data/reference/subset/` loadable by `load_normalized`

- [ ] **Step 1: Write the failing tests** — `tests/test_cms_adapters.py`

```python
import csv
import io
from datetime import date
from decimal import Decimal

import pytest

from app.reference.cms_adapters import find_header, parse_mue, parse_pfs, parse_ptp


def rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text.strip())))


PTP = """
"CPT only copyright 2025 American Medical Association. All rights reserved."
Column 1,Column 2,"*=in existence prior to 1996",Effective Date,Deletion Date *=no data,Modifier 0=not allowed 1=allowed 9=not applicable,PTP Edit Rationale
93000,93005,,20000101,*,0,Misuse of column two code with column one code
29881,29880,,20000101,20261115,1,Standards of medical / surgical practice
bad,row
"""

MUE = """
HCPCS/CPT Code,Practitioner Services MUE Values,MUE Adjudication Indicator,MUE Rationale
99213,1,2 Date of Service Edit: Policy,Code Descriptor / CPT Instruction
96372,4,1 Line Edit,Clinical: Data
"""

PFS = """
"2026 National Physician Fee Schedule Relative Value File"
,,,,,,NON-FAC,,FACILITY,,,NON-FACILITY,FACILITY,,,,CONV
HCPCS,MOD,DESCRIPTION,STATUS CODE,NOT USED FOR MEDICARE PAYMENT,WORK RVU,PE RVU,NA INDICATOR,PE RVU,NA INDICATOR,MP RVU,TOTAL,TOTAL,PCTC IND,GLOB DAYS,MULT PROC,FACTOR
99213,,Office o/p est low,A,,1.30,1.33,,0.53,,0.10,2.73,1.93,0,XXX,0,33.7575
93000,26,Electrocardiogram,A,,0.17,0.06,,0.06,,0.01,0.24,0.24,1,XXX,0,33.7575
99201,,Office/outpatient visit new,D,,0,0,,0,,0,0,0,0,XXX,0,33.7575
"""


def test_find_header_single_row() -> None:
    start, cols = find_header(
        rows(MUE), {"code": lambda h: "HCPCS" in h, "mai": lambda h: "ADJUDICATION" in h}
    )
    assert start == 1
    assert cols == {"code": 0, "mai": 2}


def test_find_header_two_row_header() -> None:
    start, cols = find_header(
        rows(PFS),
        {
            "nonfac": lambda h: h.startswith("NON-FACILITY") and "TOTAL" in h,
            "fac": lambda h: h.startswith("FACILITY") and "TOTAL" in h,
        },
    )
    assert start == 3
    assert cols == {"nonfac": 11, "fac": 12}


def test_find_header_missing_raises() -> None:
    with pytest.raises(ValueError):
        find_header(rows(MUE), {"x": lambda h: h == "NOPE"})


def test_parse_ptp() -> None:
    edits = parse_ptp(rows(PTP), "NCCI-T")
    assert len(edits) == 2  # malformed row skipped
    first, second = edits
    assert (first.col1, first.col2, first.effective, first.deleted, first.modifier_indicator) == (
        "93000",
        "93005",
        date(2000, 1, 1),
        None,
        "0",
    )
    assert second.deleted == date(2026, 11, 15)
    assert second.modifier_indicator == "1"


def test_parse_mue() -> None:
    limits = parse_mue(rows(MUE), "MUE-T")
    assert [(m.code, m.max_units, m.mai) for m in limits] == [("99213", 1, 2), ("96372", 4, 1)]


def test_parse_pfs_global_rows_only_and_rates() -> None:
    fees, statuses = parse_pfs(rows(PFS), "PFS-T")
    assert [(s.code, s.status) for s in statuses] == [("99213", "A"), ("99201", "D")]
    assert len(fees) == 1  # 93000-26 is a modifier row; 99201 is deleted with zero RVUs
    assert fees[0].code == "99213"
    assert fees[0].nonfacility == Decimal("92.16")  # 2.73 * 33.7575 = 92.157975
    assert fees[0].facility == Decimal("65.15")  # 1.93 * 33.7575 = 65.151975
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_cms_adapters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.reference.cms_adapters'`.

- [ ] **Step 3: Implement** — `app/reference/cms_adapters.py`

```python
"""Turn raw CMS rows (already read from CSV/XLSX) into reference dataclasses.

CMS files carry preambles and sometimes multi-row headers, so headers are located by
matching column titles rather than by fixed positions. Malformed data rows are skipped.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

from app.models import money
from app.reference.base import CodeStatus, FeeRate, MueLimit, PtpEdit

Matcher = Callable[[str], bool]


def _combined(rows: list[list[str]], start: int, width: int) -> list[str]:
    ncols = max(len(rows[start + k]) for k in range(width))
    out = []
    for c in range(ncols):
        parts = [rows[start + k][c].strip() for k in range(width) if c < len(rows[start + k])]
        out.append(" ".join(p for p in parts if p).upper())
    return out


def find_header(rows: list[list[str]], matchers: dict[str, Matcher]) -> tuple[int, dict[str, int]]:
    for width in (1, 2, 3):
        for start in range(len(rows) - width + 1):
            header = _combined(rows, start, width)
            cols: dict[str, int] = {}
            for key, match in matchers.items():
                hits = [i for i, h in enumerate(header) if match(h)]
                if len(hits) != 1:
                    break
                cols[key] = hits[0]
            else:
                return start + width, cols
    raise ValueError(f"header not found for columns {sorted(matchers)}")


def _cell(row: list[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


def _yyyymmdd(v: str) -> date:
    if len(v) != 8 or not v.isdigit():
        raise ValueError(f"bad date {v!r}")
    return date(int(v[:4]), int(v[4:6]), int(v[6:]))


def parse_ptp(rows: list[list[str]], ref_version: str) -> list[PtpEdit]:
    start, c = find_header(
        rows,
        {
            "col1": lambda h: h.startswith("COLUMN 1"),
            "col2": lambda h: h.startswith("COLUMN 2"),
            "effective": lambda h: "EFFECTIVE" in h,
            "deletion": lambda h: "DELETION" in h,
            "modifier": lambda h: h.startswith("MODIFIER"),
        },
    )
    edits = []
    for row in rows[start:]:
        try:
            deletion = _cell(row, c["deletion"])
            indicator = _cell(row, c["modifier"])[:1]
            if indicator not in {"0", "1", "9"}:
                raise ValueError(f"bad modifier indicator {indicator!r}")
            edits.append(
                PtpEdit(
                    col1=_cell(row, c["col1"]).upper(),
                    col2=_cell(row, c["col2"]).upper(),
                    effective=_yyyymmdd(_cell(row, c["effective"])),
                    deleted=None if deletion in {"", "*"} else _yyyymmdd(deletion),
                    modifier_indicator=indicator,
                    ref_version=ref_version,
                )
            )
        except ValueError:
            continue
    return edits


def parse_mue(rows: list[list[str]], ref_version: str) -> list[MueLimit]:
    start, c = find_header(
        rows,
        {
            "code": lambda h: "HCPCS" in h,
            "value": lambda h: "MUE VALUE" in h,
            "mai": lambda h: "ADJUDICATION INDICATOR" in h,
        },
    )
    limits = []
    for row in rows[start:]:
        try:
            code = _cell(row, c["code"]).upper()
            if not code:
                raise ValueError("empty code")
            limits.append(
                MueLimit(
                    code=code,
                    max_units=int(float(_cell(row, c["value"]))),
                    mai=int(_cell(row, c["mai"])[:1]),
                    ref_version=ref_version,
                )
            )
        except ValueError:
            continue
    return limits


def parse_pfs(rows: list[list[str]], ref_version: str) -> tuple[list[FeeRate], list[CodeStatus]]:
    start, c = find_header(
        rows,
        {
            "code": lambda h: h == "HCPCS",
            "mod": lambda h: h == "MOD",
            "status": lambda h: "STATUS" in h,
            "nonfac": lambda h: h.startswith("NON-FACILITY") and "TOTAL" in h,
            "fac": lambda h: h.startswith("FACILITY") and "TOTAL" in h,
            "cf": lambda h: "CONV" in h or "CONVERSION" in h,
        },
    )
    fees: list[FeeRate] = []
    statuses: list[CodeStatus] = []
    for row in rows[start:]:
        code = _cell(row, c["code"]).upper()
        if not code or _cell(row, c["mod"]):
            continue
        status = _cell(row, c["status"]).upper()
        statuses.append(CodeStatus(code, status, ref_version))
        try:
            nonfac = Decimal(_cell(row, c["nonfac"]) or "0")
            fac = Decimal(_cell(row, c["fac"]) or "0")
            cf = Decimal(_cell(row, c["cf"]))
        except InvalidOperation:
            continue
        if status != "D" and nonfac > 0:
            fees.append(FeeRate(code, money(nonfac * cf), money(fac * cf), ref_version))
    return fees, statuses
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cms_adapters.py -v && ruff format . && ruff check . && mypy app`
Expected: 6 passed; clean.

- [ ] **Step 5: Subset build script** — `scripts/build_reference_subset.py`

```python
"""Build data/reference/subset from raw CMS downloads (run locally, not in CI).

Example:
  python scripts/build_reference_subset.py \
    --ptp data/reference/raw/ptp_practitioner_f1.xlsx --ptp data/reference/raw/ptp_practitioner_f2.xlsx \
    --mue data/reference/raw/mue_practitioner.xlsx --pfs data/reference/raw/PPRRVU26.csv \
    --codes data/reference/codes.txt \
    --ncci NCCI-2026Q4:2026-10-01:2026-12-31 --mue-version MUE-2026Q4:2026-10-01:2026-12-31 \
    --pfs-version PFS-2026:2026-01-01:2026-12-31 --out data/reference/subset
"""

import argparse
import csv
import io
import sys
from datetime import date
from pathlib import Path

from app.reference.base import InMemoryReference, RefVersion
from app.reference.cms_adapters import parse_mue, parse_pfs, parse_ptp
from app.reference.normalized import write_normalized


def _cell(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def read_table(path: Path) -> list[list[str]]:
    if path.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        return [[_cell(v) for v in r] for ws in wb.worksheets for r in ws.iter_rows(values_only=True)]
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:5000], delimiters=",\t|")
    return list(csv.reader(io.StringIO(text), dialect))


def _version(spec: str, kind: str) -> RefVersion:
    name, start, end = spec.split(":")
    return RefVersion(name, kind, date.fromisoformat(start), date.fromisoformat(end))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ptp", type=Path, action="append", required=True)
    p.add_argument("--mue", type=Path, required=True)
    p.add_argument("--pfs", type=Path, required=True)
    p.add_argument("--codes", type=Path, required=True)
    p.add_argument("--ncci", required=True, help="NAME:valid_from:valid_to")
    p.add_argument("--mue-version", required=True)
    p.add_argument("--pfs-version", required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()

    codes = {c.strip().upper() for c in a.codes.read_text().split() if c.strip()}
    ncci, mue_v, pfs_v = (
        _version(a.ncci, "ncci"),
        _version(a.mue_version, "mue"),
        _version(a.pfs_version, "pfs"),
    )

    ptp = [
        e
        for f in a.ptp
        for e in parse_ptp(read_table(f), ncci.ref_version)
        if e.col1 in codes and e.col2 in codes
    ]
    mue = [m for m in parse_mue(read_table(a.mue), mue_v.ref_version) if m.code in codes]
    fees, statuses = parse_pfs(read_table(a.pfs), pfs_v.ref_version)
    fees = [f for f in fees if f.code in codes]
    statuses = [s for s in statuses if s.code in codes]

    write_normalized(InMemoryReference([ncci, mue_v, pfs_v], ptp, mue, fees, statuses), a.out)
    by_ind = {i: sum(e.modifier_indicator == i for e in ptp) for i in "019"}
    print(
        f"ptp={len(ptp)} {by_ind} mue={len(mue)} fees={len(fees)} codes={len(statuses)} "
        f"deleted={sum(s.status == 'D' for s in statuses)}"
    )
    if by_ind["0"] < 10 or by_ind["1"] < 10 or not any(s.status == "D" for s in statuses):
        print(
            "Subset too thin: need >=10 PTP pairs with indicator 0 and 1, and >=1 deleted code. Add codes.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Code list** — `data/reference/codes.txt` (one per line; families chosen so NCCI pairs exist)

```
99202 99203 99204 99205 99211 99212 99213 99214 99215 99201
99221 99222 99223 99231 99232 99233 99281 99282 99283 99284 99285
93000 93005 93010 93306 93307 93308 93320 93321 93325
45378 45380 45381 45384 45385 43235 43239 43249
29880 29881 29875 29876 27447 20610 20611 76942
11042 11043 97597 97598 12001 12002 12004 12011 17000 17003 17004 11721 11720 10060 10061
36415 96372 96374 96375 90471 90472 G0008 90686
97110 97112 97140 97530 97161 97162 97163 97116
71045 71046 71047 72148 73721 74177 74176 70450 76700 77067
64483 64484 62323 66984 66982 67028 92012 92014 92004
```

- [ ] **Step 7: Download CMS files and record sources** (needs a human click-through if CMS pages require license acceptance)

Download the current releases into `data/reference/raw/` (git-ignored):
- NCCI PTP practitioner edits, current quarter: https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits
- NCCI MUE practitioner services, current quarter: https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-medically-unlikely-edits
- PFS relative value file (PPRRVU), current year: https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files

Unzip; note exact file names and release labels. Write `data/reference/SOURCES.md`:

```markdown
# Reference data sources

| Table | Release | File(s) | URL | Downloaded |
|---|---|---|---|---|
| NCCI PTP (practitioner) | <e.g. v32.3 2026Q4> | <file names> | <page URL> | <YYYY-MM-DD> |
| NCCI MUE (practitioner) | <release> | <file name> | <page URL> | <YYYY-MM-DD> |
| PFS RVU | <e.g. RVU26D> | <file name> | <page URL> | <YYYY-MM-DD> |

Only code numbers, edit dates, indicators, unit limits and computed national rates are committed.
CPT descriptors (AMA copyright) are not committed.
```
(The `<...>` cells are filled with the actual values seen on download — this is a record, not a placeholder in code.)

- [ ] **Step 8: Build the subset**

```bash
python scripts/build_reference_subset.py --ptp data/reference/raw/<ptp file 1> [--ptp <ptp file 2> ...] \
  --mue data/reference/raw/<mue file> --pfs data/reference/raw/<PPRRVU file> --codes data/reference/codes.txt \
  --ncci NCCI-2026Q4:2026-10-01:2026-12-31 --mue-version MUE-2026Q4:2026-10-01:2026-12-31 \
  --pfs-version PFS-2026:2026-01-01:2026-12-31 --out data/reference/subset
```
Expected: a line like `ptp=… {'0': ≥10, '1': ≥10, '9': …} mue=… fees=… codes=… deleted=≥1`, exit 0.
If it raises `ValueError: header not found`, open the raw file, read the header titles, and adjust the matcher lambdas in `cms_adapters.py` (then add a test row mirroring the real header to `tests/test_cms_adapters.py`). If the subset is too thin, add codes from the same families to `codes.txt` and rerun. If `deleted=0` (99201 was deleted in 2021 and may be absent from the current file), list status-`D` codes in the raw PFS file — e.g. `python -c "from pathlib import Path; from scripts.build_reference_subset import read_table; from app.reference.cms_adapters import parse_pfs; _, s = parse_pfs(read_table(Path('data/reference/raw/<PPRRVU file>')), 'x'); print([x.code for x in s if x.status == 'D'][:10])"` — and add 3–5 of them to `codes.txt`. Use the quarter names and dates that match the actual releases.

- [ ] **Step 9: Sanity check the subset**

Run: `python -c "from pathlib import Path; from app.reference.normalized import load_normalized as l; r=l(Path('data/reference/subset')); print(len(r.ptp_edits), len(r.mue_limits), len(r.fees), r.fee('99213', __import__('datetime').date(2026,10,15)))"`
Expected: non-zero counts; 99213 fee roughly $90–$100 non-facility.

- [ ] **Step 10: Commit**

```bash
git add app/reference/cms_adapters.py tests/test_cms_adapters.py scripts/build_reference_subset.py \
  data/reference/codes.txt data/reference/SOURCES.md data/reference/subset
git commit -m "feat: CMS NCCI/MUE/PFS adapters and committed reference subset

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Rule framework, R0 coverage notice, R1 duplicates

**Files:**
- Create: `app/rules/__init__.py`, `app/rules/coverage.py`, `app/rules/duplicates.py`, `tests/test_rules_core.py`

**Interfaces:**
- Consumes: `Claim, LineItem, Flag, Evidence, Severity, make_flag`; `Reference`.
- Produces:
  - `@dataclass(frozen=True) RuleConfig(price_multiplier: Decimal = Decimal("3"))`
  - `Rule = Callable[[Claim, Reference, RuleConfig], list[Flag]]`
  - `check_coverage(claim, ref, config) -> list[Flag]` (rule_id `"R0"`, severity NOTICE, one flag listing all uncovered lines)
  - `check_duplicates(claim, ref, config) -> list[Flag]` (rule_id `"R1"`)
  - `ALL_RULES: list[Rule]` (R0, R1 now; R2–R5 appended in Tasks 5–7)
  - `run_rules(claim, ref, config=RuleConfig()) -> list[Flag]`
  - `REPEAT_MODIFIERS = frozenset({"76", "77", "91"})`

- [ ] **Step 1: Write the failing tests** — `tests/test_rules_core.py`

```python
from datetime import date
from decimal import Decimal

from app.models import Severity
from app.rules import RuleConfig, run_rules
from app.rules.coverage import check_coverage
from app.rules.duplicates import check_duplicates
from tests.helpers import FIXTURE_REF, claim, line

CFG = RuleConfig()


def test_identical_lines_flagged_once_with_extra_charge() -> None:
    c = claim(line("L1"), line("L2"), line("L3", code="97110", charge="40.00"))
    flags = check_duplicates(c, FIXTURE_REF, CFG)
    assert len(flags) == 1
    assert flags[0].rule_id == "R1" and flags[0].severity is Severity.ERROR
    assert flags[0].line_ids == ["L1", "L2"]
    assert flags[0].est_overcharge == Decimal("150.00")


def test_three_copies_overcharge_is_two_extra_lines() -> None:
    c = claim(line("L1"), line("L2", charge="140.00"), line("L3", charge="160.00"))
    (flag,) = check_duplicates(c, FIXTURE_REF, CFG)
    assert flag.line_ids == ["L1", "L2", "L3"]
    assert flag.est_overcharge == Decimal("300.00")


def test_different_units_or_dates_are_not_duplicates() -> None:
    c = claim(line("L1"), line("L2", units=2), line("L3", dos=date(2026, 10, 16)))
    assert check_duplicates(c, FIXTURE_REF, CFG) == []


def test_modifier_order_does_not_matter() -> None:
    c = claim(line("L1", modifiers=["RT", "LT"]), line("L2", modifiers=["lt", "rt"]))
    assert len(check_duplicates(c, FIXTURE_REF, CFG)) == 1


def test_repeat_procedure_modifiers_are_not_duplicates() -> None:
    for mod in ("76", "77", "91"):
        c = claim(line("L1"), line("L2", modifiers=[mod]))
        assert check_duplicates(c, FIXTURE_REF, CFG) == [], mod


def test_coverage_notice_only_for_uncovered_lines() -> None:
    c = claim(line("L1"), line("L2", dos=date(2027, 1, 5)), line("L3", dos=date(2027, 1, 6)))
    (flag,) = check_coverage(c, FIXTURE_REF, CFG)
    assert flag.rule_id == "R0" and flag.severity is Severity.NOTICE
    assert flag.line_ids == ["L2", "L3"]
    assert flag.est_overcharge == Decimal("0.00")
    assert flag.evidence.row["dates"] == "2027-01-05,2027-01-06"


def test_run_rules_mixed_coverage_still_audits_covered_lines() -> None:
    c = claim(line("L1"), line("L2"), line("L3", dos=date(2027, 1, 5)))
    rule_ids = [f.rule_id for f in run_rules(c, FIXTURE_REF)]
    assert rule_ids[0] == "R0"
    assert "R1" in rule_ids


def test_empty_claim_produces_no_flags() -> None:
    assert run_rules(claim(), FIXTURE_REF) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_rules_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rules'`.

- [ ] **Step 3: Implement**

`app/rules/__init__.py`:
```python
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.models import Claim, Flag
from app.reference.base import Reference


@dataclass(frozen=True)
class RuleConfig:
    price_multiplier: Decimal = Decimal("3")


Rule = Callable[[Claim, Reference, RuleConfig], list[Flag]]


def _all_rules() -> list[Rule]:
    from app.rules.coverage import check_coverage
    from app.rules.duplicates import check_duplicates

    return [check_coverage, check_duplicates]


ALL_RULES: list[Rule] = _all_rules()


def run_rules(claim: Claim, ref: Reference, config: RuleConfig | None = None) -> list[Flag]:
    cfg = config or RuleConfig()
    return [flag for rule in ALL_RULES for flag in rule(claim, ref, cfg)]
```

`app/rules/coverage.py`:
```python
from decimal import Decimal

from app.models import Claim, Evidence, Flag, Severity, make_flag
from app.reference.base import Reference
from app.rules import RuleConfig


def check_coverage(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    uncovered = [line for line in claim.lines if not ref.covers(line.date_of_service)]
    if not uncovered:
        return []
    dates = sorted({line.date_of_service.isoformat() for line in uncovered})
    return [
        make_flag(
            claim,
            "R0",
            Severity.NOTICE,
            uncovered,
            Evidence(table="reference_versions", ref_version=None, row={"dates": ",".join(dates)}),
            Decimal("0"),
            f"Cannot audit {len(uncovered)} line(s): no reference data loaded for {', '.join(dates)}",
        )
    ]
```

`app/rules/duplicates.py`:
```python
from collections import defaultdict
from datetime import date

from app.models import Claim, Evidence, Flag, LineItem, Severity, make_flag
from app.reference.base import Reference
from app.rules import RuleConfig

REPEAT_MODIFIERS = frozenset({"76", "77", "91"})


def check_duplicates(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    groups: dict[tuple[str, tuple[str, ...], date, int], list[LineItem]] = defaultdict(list)
    for line in claim.lines:
        if REPEAT_MODIFIERS & set(line.modifiers):
            continue
        groups[(line.code, tuple(sorted(line.modifiers)), line.date_of_service, line.units)].append(line)
    flags = []
    for (code, mods, dos, units), lines in groups.items():
        if len(lines) < 2:
            continue
        lines = sorted(lines, key=lambda x: x.id)
        extra = sum((x.charge for x in lines[1:]), start=lines[0].charge * 0)
        flags.append(
            make_flag(
                claim,
                "R1",
                Severity.ERROR,
                lines,
                Evidence(
                    table="claim_lines",
                    ref_version=None,
                    row={
                        "code": code,
                        "modifiers": "+".join(mods),
                        "date_of_service": dos.isoformat(),
                        "units": str(units),
                    },
                ),
                extra,
                f"{code} billed {len(lines)} times on {dos.isoformat()} with identical units and modifiers",
            )
        )
    return flags
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_rules_core.py -v && ruff format . && ruff check . && mypy app`
Expected: 8 passed; clean.

- [ ] **Step 5: Commit**

```bash
git add app/rules tests/test_rules_core.py
git commit -m "feat: rule framework with R0 coverage notice and R1 duplicate detection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: R2 NCCI procedure-to-procedure edits

**Files:**
- Create: `app/rules/ncci.py`, `tests/test_rule_ncci.py`
- Modify: `app/rules/__init__.py` (`_all_rules` adds `check_ncci`)

**Interfaces:**
- Consumes: `Reference.ptp`, `Reference.covers`, `RuleConfig`, `make_flag`.
- Produces: `check_ncci(claim, ref, config) -> list[Flag]` (rule_id `"R2"`; `line_ids == [column-1 line, column-2 line]`; overcharge = column-2 line charge); `NCCI_MODIFIERS: frozenset[str]`.

- [ ] **Step 1: Write the failing tests** — `tests/test_rule_ncci.py`

```python
from datetime import date
from decimal import Decimal

from app.rules import RuleConfig
from app.rules.ncci import check_ncci
from tests.helpers import FIXTURE_REF, claim, line

CFG = RuleConfig()


def test_indicator_zero_pair_flagged_with_column_two_charge() -> None:
    c = claim(line("L1", code="93000", charge="50.00"), line("L2", code="93005", charge="30.00"))
    (flag,) = check_ncci(c, FIXTURE_REF, CFG)
    assert flag.rule_id == "R2"
    assert flag.line_ids == ["L1", "L2"]
    assert flag.est_overcharge == Decimal("30.00")
    assert flag.evidence.ref_version == "NCCI-TEST"
    assert flag.evidence.row["modifier_indicator"] == "0"


def test_line_order_does_not_matter() -> None:
    c = claim(line("L1", code="93005", charge="30.00"), line("L2", code="93000", charge="50.00"))
    (flag,) = check_ncci(c, FIXTURE_REF, CFG)
    assert flag.line_ids == ["L2", "L1"]  # column 1 first


def test_indicator_one_needs_ncci_modifier() -> None:
    assert len(check_ncci(claim(line("L1", code="45380"), line("L2", code="45378")), FIXTURE_REF, CFG)) == 1
    with_59 = claim(line("L1", code="45380"), line("L2", code="45378", modifiers=["59"]))
    assert check_ncci(with_59, FIXTURE_REF, CFG) == []
    lowercase_xs = claim(line("L1", code="45380"), line("L2", code="45378", modifiers=["xs"]))
    assert check_ncci(lowercase_xs, FIXTURE_REF, CFG) == []


def test_indicator_zero_ignores_modifiers() -> None:
    c = claim(line("L1", code="93000"), line("L2", code="93005", modifiers=["59"]))
    assert len(check_ncci(c, FIXTURE_REF, CFG)) == 1


def test_indicator_nine_is_not_applicable() -> None:
    assert check_ncci(claim(line("L1", code="20610"), line("L2", code="20611")), FIXTURE_REF, CFG) == []


def test_deleted_edit_not_applied_on_deletion_date() -> None:
    def pair(d: date) -> list[object]:
        return list(
            check_ncci(
                claim(line("L1", code="29881", dos=d), line("L2", code="29880", dos=d)), FIXTURE_REF, CFG
            )
        )

    assert len(pair(date(2026, 11, 14))) == 1
    assert pair(date(2026, 11, 15)) == []


def test_different_dates_not_paired() -> None:
    c = claim(line("L1", code="93000"), line("L2", code="93005", dos=date(2026, 10, 16)))
    assert check_ncci(c, FIXTURE_REF, CFG) == []


def test_uncovered_dates_skipped() -> None:
    d = date(2027, 1, 5)
    assert (
        check_ncci(claim(line("L1", code="93000", dos=d), line("L2", code="93005", dos=d)), FIXTURE_REF, CFG)
        == []
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_rule_ncci.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rules.ncci'`.

- [ ] **Step 3: Implement** — `app/rules/ncci.py`

```python
from collections import defaultdict
from datetime import date
from itertools import combinations

from app.models import Claim, Evidence, Flag, LineItem, Severity, make_flag
from app.reference.base import Reference
from app.rules import RuleConfig

# CMS "NCCI-associated modifiers" that can bypass an edit with modifier indicator 1.
NCCI_MODIFIERS = frozenset(
    {
        "E1",
        "E2",
        "E3",
        "E4",
        "FA",
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
        "LC",
        "LD",
        "LM",
        "LT",
        "RC",
        "RI",
        "RT",
        "TA",
        "T1",
        "T2",
        "T3",
        "T4",
        "T5",
        "T6",
        "T7",
        "T8",
        "T9",
        "24",
        "25",
        "27",
        "57",
        "58",
        "59",
        "78",
        "79",
        "91",
        "XE",
        "XS",
        "XP",
        "XU",
    }
)


def check_ncci(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    by_dos: dict[date, list[LineItem]] = defaultdict(list)
    for line in claim.lines:
        if ref.covers(line.date_of_service):
            by_dos[line.date_of_service].append(line)
    flags = []
    for dos, lines in by_dos.items():
        for a, b in combinations(lines, 2):
            if a.code == b.code:
                continue
            for c1, c2 in ((a, b), (b, a)):
                edit = ref.ptp(c1.code, c2.code, dos)
                if edit is None:
                    continue
                if edit.modifier_indicator == "9":
                    break
                if edit.modifier_indicator == "1" and NCCI_MODIFIERS & (
                    set(c1.modifiers) | set(c2.modifiers)
                ):
                    break
                flags.append(
                    make_flag(
                        claim,
                        "R2",
                        Severity.ERROR,
                        [c1, c2],
                        Evidence(
                            table="ncci_ptp",
                            ref_version=edit.ref_version,
                            row={
                                "column_1": edit.col1,
                                "column_2": edit.col2,
                                "effective": edit.effective.isoformat(),
                                "deleted": edit.deleted.isoformat() if edit.deleted else "",
                                "modifier_indicator": edit.modifier_indicator,
                            },
                        ),
                        c2.charge,
                        f"{c2.code} is bundled into {c1.code} on {dos.isoformat()} "
                        f"(NCCI edit, modifier indicator {edit.modifier_indicator})",
                    )
                )
                break
    return flags
```

Modify `app/rules/__init__.py` `_all_rules`:
```python
def _all_rules() -> list[Rule]:
    from app.rules.coverage import check_coverage
    from app.rules.duplicates import check_duplicates
    from app.rules.ncci import check_ncci

    return [check_coverage, check_duplicates, check_ncci]
```

- [ ] **Step 4: Run tests**

Run: `pytest -q && ruff format . && ruff check . && mypy app`
Expected: all pass; clean.

- [ ] **Step 5: Commit**

```bash
git add app/rules tests/test_rule_ncci.py
git commit -m "feat: R2 NCCI procedure-to-procedure unbundling rule

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: R3 Medically Unlikely Edits

**Files:**
- Create: `app/rules/mue.py`, `tests/test_rule_mue.py`
- Modify: `app/rules/__init__.py` (append `check_mue`)

**Interfaces:**
- Consumes: `Reference.mue`, `Reference.covers`.
- Produces: `check_mue(claim, ref, config) -> list[Flag]` (rule_id `"R3"`). MAI 1: per line. MAI 2/3: summed per code per date of service; flag lists all those lines; overcharge = excess units × (total charge ÷ total units).

- [ ] **Step 1: Write the failing tests** — `tests/test_rule_mue.py`

```python
from datetime import date
from decimal import Decimal

from app.rules import RuleConfig
from app.rules.mue import check_mue
from tests.helpers import FIXTURE_REF, claim, line

CFG = RuleConfig()


def test_line_level_limit() -> None:
    c = claim(line("L1", code="96372", units=6, charge="90.00"))
    (flag,) = check_mue(c, FIXTURE_REF, CFG)
    assert flag.rule_id == "R3" and flag.line_ids == ["L1"]
    assert flag.est_overcharge == Decimal("30.00")  # 2 excess units x 15.00
    assert flag.evidence.row == {"code": "96372", "max_units": "4", "mai": "1", "billed_units": "6"}


def test_line_level_limit_not_summed_across_lines() -> None:
    c = claim(line("L1", code="96372", units=3), line("L2", code="96372", units=3))
    assert check_mue(c, FIXTURE_REF, CFG) == []


def test_day_level_limit_sums_lines() -> None:
    c = claim(
        line("L1", code="97110", units=4, charge="120.00"), line("L2", code="97110", units=3, charge="90.00")
    )
    (flag,) = check_mue(c, FIXTURE_REF, CFG)
    assert flag.line_ids == ["L1", "L2"]
    assert flag.est_overcharge == Decimal("30.00")  # 1 excess unit x (210 / 7)


def test_day_level_limit_separate_dates() -> None:
    c = claim(line("L1", code="97110", units=4), line("L2", code="97110", units=3, dos=date(2026, 10, 16)))
    assert check_mue(c, FIXTURE_REF, CFG) == []


def test_at_limit_is_fine_and_unknown_code_skipped() -> None:
    assert check_mue(claim(line("L1", code="96372", units=4)), FIXTURE_REF, CFG) == []
    assert check_mue(claim(line("L1", code="0001U", units=50)), FIXTURE_REF, CFG) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_rule_mue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rules.mue'`.

- [ ] **Step 3: Implement** — `app/rules/mue.py`

```python
from collections import defaultdict
from datetime import date

from app.models import Claim, Evidence, Flag, LineItem, Severity, make_flag
from app.reference.base import MueLimit, Reference
from app.rules import RuleConfig


def _flag(claim: Claim, lines: list[LineItem], lim: MueLimit, billed: int) -> Flag:
    total_charge = sum((x.charge for x in lines), start=lines[0].charge * 0)
    excess = billed - lim.max_units
    dos = lines[0].date_of_service.isoformat()
    scope = "on one line" if lim.mai == 1 else "on one date of service"
    return make_flag(
        claim,
        "R3",
        Severity.ERROR,
        lines,
        Evidence(
            table="mue",
            ref_version=lim.ref_version,
            row={
                "code": lim.code,
                "max_units": str(lim.max_units),
                "mai": str(lim.mai),
                "billed_units": str(billed),
            },
        ),
        excess * total_charge / billed,
        f"{lim.code}: {billed} units billed {scope} ({dos}); Medicare's unit limit is {lim.max_units}",
    )


def check_mue(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    groups: dict[tuple[str, date], list[LineItem]] = defaultdict(list)
    for line in claim.lines:
        if ref.covers(line.date_of_service):
            groups[(line.code, line.date_of_service)].append(line)
    flags = []
    for (code, dos), lines in groups.items():
        lim = ref.mue(code, dos)
        if lim is None:
            continue
        if lim.mai == 1:
            flags += [_flag(claim, [x], lim, x.units) for x in lines if x.units > lim.max_units]
        else:
            billed = sum(x.units for x in lines)
            if billed > lim.max_units:
                flags.append(_flag(claim, sorted(lines, key=lambda x: x.id), lim, billed))
    return flags
```

Append `check_mue` in `_all_rules` (import `from app.rules.mue import check_mue`; list becomes `[check_coverage, check_duplicates, check_ncci, check_mue]`).

- [ ] **Step 4: Run tests**

Run: `pytest -q && ruff format . && ruff check . && mypy app`
Expected: all pass; clean.

- [ ] **Step 5: Commit**

```bash
git add app/rules tests/test_rule_mue.py
git commit -m "feat: R3 medically unlikely edits (line and date-of-service limits)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: R4 deleted codes and R5 price outliers

**Files:**
- Create: `app/rules/invalid_code.py`, `app/rules/price.py`, `tests/test_rule_code_price.py`
- Modify: `app/rules/__init__.py` (append both)

**Interfaces:**
- Consumes: `Reference.code_status`, `Reference.fee`, `RuleConfig.price_multiplier`.
- Produces:
  - `check_invalid_code(claim, ref, config)` (rule_id `"R4"`, flags only codes whose PFS status is `"D"`; unknown codes are not flagged)
  - `check_price(claim, ref, config)` (rule_id `"R5"`, severity OUTLIER; facility rate when `place_of_service in FACILITY_POS`; skips lines with modifiers `26`/`TC`, missing rates, or zero rates; flags only when charge > k × rate × units)
  - `FACILITY_POS: frozenset[str]`, `PRO_TECH_MODIFIERS = frozenset({"26", "TC"})`

- [ ] **Step 1: Write the failing tests** — `tests/test_rule_code_price.py`

```python
from decimal import Decimal

from app.models import Severity
from app.rules import RuleConfig
from app.rules.invalid_code import check_invalid_code
from app.rules.price import check_price
from tests.helpers import FIXTURE_REF, claim, line

CFG = RuleConfig()


def test_deleted_code_flagged_full_charge() -> None:
    (flag,) = check_invalid_code(claim(line("L1", code="99201", charge="120.00")), FIXTURE_REF, CFG)
    assert flag.rule_id == "R4" and flag.est_overcharge == Decimal("120.00")
    assert flag.evidence.row == {"code": "99201", "status": "D"}


def test_active_and_unknown_codes_not_flagged() -> None:
    assert (
        check_invalid_code(claim(line("L1", code="99213"), line("L2", code="0001U")), FIXTURE_REF, CFG) == []
    )


def test_price_outlier_nonfacility() -> None:
    (flag,) = check_price(claim(line("L1", code="99213", charge="300.00", pos="11")), FIXTURE_REF, CFG)
    assert flag.rule_id == "R5" and flag.severity is Severity.OUTLIER
    assert flag.est_overcharge == Decimal("23.55")  # 300 - 3 x 92.15
    assert flag.evidence.row["rate_type"] == "nonfacility"


def test_price_outlier_uses_facility_rate_in_hospital() -> None:
    (flag,) = check_price(claim(line("L1", code="99213", charge="200.00", pos="22")), FIXTURE_REF, CFG)
    assert flag.est_overcharge == Decimal("3.80")  # 200 - 3 x 65.40
    assert flag.evidence.row["rate_type"] == "facility"


def test_price_at_threshold_not_flagged_and_units_scale() -> None:
    assert check_price(claim(line("L1", code="99213", charge="276.45")), FIXTURE_REF, CFG) == []
    assert check_price(claim(line("L1", code="97110", units=4, charge="360.00")), FIXTURE_REF, CFG) == []


def test_multiplier_is_configurable() -> None:
    (flag,) = check_price(
        claim(line("L1", code="99213", charge="200.00")),
        FIXTURE_REF,
        RuleConfig(price_multiplier=Decimal("2")),
    )
    assert flag.est_overcharge == Decimal("15.70")  # 200 - 2 x 92.15


def test_professional_technical_modifiers_and_missing_rates_skipped() -> None:
    assert (
        check_price(claim(line("L1", code="99213", charge="900.00", modifiers=["26"])), FIXTURE_REF, CFG)
        == []
    )
    assert check_price(claim(line("L1", code="0001U", charge="900.00")), FIXTURE_REF, CFG) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_rule_code_price.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`app/rules/invalid_code.py`:
```python
from app.models import Claim, Evidence, Flag, Severity, make_flag
from app.reference.base import Reference
from app.rules import RuleConfig


def check_invalid_code(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    flags = []
    for line in claim.lines:
        if not ref.covers(line.date_of_service):
            continue
        status = ref.code_status(line.code, line.date_of_service)
        if status is not None and status.status == "D":
            flags.append(
                make_flag(
                    claim,
                    "R4",
                    Severity.ERROR,
                    [line],
                    Evidence(
                        table="pfs_status",
                        ref_version=status.ref_version,
                        row={"code": line.code, "status": status.status},
                    ),
                    line.charge,
                    f"{line.code} is a deleted code and was not billable on {line.date_of_service.isoformat()}",
                )
            )
    return flags
```

`app/rules/price.py`:
```python
from app.models import Claim, Evidence, Flag, Severity, make_flag
from app.reference.base import Reference
from app.rules import RuleConfig

# CMS place-of-service codes paid at the facility rate.
FACILITY_POS = frozenset(
    {"19", "21", "22", "23", "24", "26", "31", "34", "41", "42", "51", "52", "53", "56", "61"}
)
PRO_TECH_MODIFIERS = frozenset({"26", "TC"})


def check_price(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    k = config.price_multiplier
    flags = []
    for line in claim.lines:
        if not ref.covers(line.date_of_service) or PRO_TECH_MODIFIERS & set(line.modifiers):
            continue
        fee = ref.fee(line.code, line.date_of_service)
        if fee is None:
            continue
        facility = line.place_of_service in FACILITY_POS
        rate = fee.facility if facility else fee.nonfacility
        if rate <= 0:
            continue
        benchmark = k * rate * line.units
        if line.charge > benchmark:
            flags.append(
                make_flag(
                    claim,
                    "R5",
                    Severity.OUTLIER,
                    [line],
                    Evidence(
                        table="pfs_rates",
                        ref_version=fee.ref_version,
                        row={
                            "code": line.code,
                            "rate_type": "facility" if facility else "nonfacility",
                            "medicare_rate": str(rate),
                            "multiplier": str(k),
                            "units": str(line.units),
                        },
                    ),
                    line.charge - benchmark,
                    f"{line.code} charged {line.charge} for {line.units} unit(s); that is more than {k}x the "
                    f"Medicare national rate of {rate} per unit",
                )
            )
    return flags
```

Update `_all_rules` to `[check_coverage, check_duplicates, check_ncci, check_mue, check_invalid_code, check_price]` with the two new imports.

- [ ] **Step 4: Run tests**

Run: `pytest -q && ruff format . && ruff check . && mypy app`
Expected: all pass; clean.

- [ ] **Step 5: Commit**

```bash
git add app/rules tests/test_rule_code_price.py
git commit -m "feat: R4 deleted-code and R5 price-outlier rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: FHIR ExplanationOfBenefit parser and writer

**Files:**
- Create: `app/ingest/__init__.py` (empty), `app/ingest/fhir.py`, `tests/test_fhir.py`

**Interfaces:**
- Consumes: `Claim`, `LineItem`.
- Produces:
  - `@dataclass(frozen=True) ParseError(path: str, message: str)`
  - `@dataclass ParseResult(claims: list[Claim], errors: list[ParseError])`
  - `parse_fhir(data: object) -> ParseResult` — accepts a `Bundle` (uses only `ExplanationOfBenefit` entries) or a single `ExplanationOfBenefit`. Line ids are `f"L{item.sequence}"`. Patient identity becomes `patient_pseudonym = "P-" + sha256(patient.reference)[:8]`.
  - `claim_to_eob(claim: Claim) -> dict[str, object]` — writes `item.sequence = index + 1` (so lines must be ordered `L1..Ln` for an exact round trip)
  - `claims_to_bundle(claims: list[Claim]) -> dict[str, object]`

- [ ] **Step 1: Write the failing tests** — `tests/test_fhir.py`

```python
from decimal import Decimal
from typing import Any

from app.ingest.fhir import claim_to_eob, claims_to_bundle, parse_fhir
from tests.helpers import claim, line


def eob(items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "resourceType": "ExplanationOfBenefit",
        "id": "EOB1",
        "patient": {"reference": "Patient/abc"},
        "provider": {"display": "Clinic A"},
        "insurer": {"display": "Plan B"},
        "diagnosis": [{"sequence": 1, "diagnosisCodeableConcept": {"coding": [{"code": "E11.9"}]}}],
        "item": items,
        **extra,
    }


def item(seq: int = 1, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sequence": seq,
        "productOrService": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": "99213"}]},
        "servicedDate": "2026-10-15",
        "quantity": {"value": 1},
        "net": {"value": 150.0, "currency": "USD"},
        "locationCodeableConcept": {"coding": [{"code": "11"}]},
        "diagnosisSequence": [1],
    }
    base.update(over)
    return base


def test_parse_single_eob() -> None:
    res = parse_fhir(eob([item(), item(2, modifier=[{"coding": [{"code": "25"}]}])]))
    assert res.errors == []
    (c,) = res.claims
    assert c.id == "EOB1" and c.provider == "Clinic A" and c.payer == "Plan B"
    assert c.patient_pseudonym.startswith("P-") and "abc" not in c.patient_pseudonym
    assert [x.id for x in c.lines] == ["L1", "L2"]
    assert c.lines[0].charge == Decimal("150.0")
    assert c.lines[0].place_of_service == "11"
    assert c.lines[0].diagnosis_codes == ["E11.9"]
    assert c.lines[1].modifiers == ["25"]


def test_bundle_skips_non_eob_entries() -> None:
    bundle = {
        "resourceType": "Bundle",
        "entry": [{"resource": {"resourceType": "Patient"}}, {"resource": eob([item()])}],
    }
    res = parse_fhir(bundle)
    assert len(res.claims) == 1 and res.errors == []


def test_submitted_adjudication_used_when_net_missing() -> None:
    it = item()
    del it["net"]
    it["adjudication"] = [{"category": {"coding": [{"code": "submitted"}]}, "amount": {"value": 99.5}}]
    (c,) = parse_fhir(eob([it])).claims
    assert c.lines[0].charge == Decimal("99.5")


def test_period_and_billable_period_fallback_for_date() -> None:
    it = item()
    del it["servicedDate"]
    (c,) = parse_fhir(eob([it], billablePeriod={"start": "2026-10-20"})).claims
    assert c.lines[0].date_of_service.isoformat() == "2026-10-20"


def test_bad_items_reported_with_path_rest_of_claim_kept() -> None:
    res = parse_fhir(
        eob(
            [
                item(1),
                item(2, quantity={"value": 0}),
                item(3, net={"value": -20.0}),
                item(4, productOrService={"coding": []}),
                item(5, quantity={"value": 1.5}),
            ]
        )
    )
    assert [x.id for x in res.claims[0].lines] == ["L1"]
    assert [e.path for e in res.errors] == ["$.item[1]", "$.item[2]", "$.item[3]", "$.item[4]"]


def test_eob_with_no_valid_items_is_an_error() -> None:
    res = parse_fhir(eob([item(1, quantity={"value": 0})]))
    assert res.claims == []
    assert res.errors[-1].path == "$.item" and res.errors[-1].message == "no valid items"


def test_wrong_resource_type_and_non_object() -> None:
    assert parse_fhir({"resourceType": "Patient"}).errors[0].path == "$.resourceType"
    assert parse_fhir([1, 2]).errors[0].path == "$"


def test_missing_id_reported_with_bundle_path() -> None:
    bad = eob([item()])
    del bad["id"]
    res = parse_fhir({"resourceType": "Bundle", "entry": [{"resource": bad}]})
    assert res.errors[0].path == "$.entry[0].resource.id"


def test_round_trip_preserves_audit_fields() -> None:
    original = claim(
        line("L1", modifiers=["25"]), line("L2", code="97110", units=3, charge="91.20", pos="22")
    )
    (back,) = parse_fhir(claim_to_eob(original)).claims
    for a, b in zip(original.lines, back.lines, strict=True):
        assert (a.id, a.code, a.modifiers, a.units, a.charge, a.date_of_service, a.place_of_service) == (
            b.id,
            b.code,
            b.modifiers,
            b.units,
            b.charge,
            b.date_of_service,
            b.place_of_service,
        )
    bundle = claims_to_bundle([original, original])
    assert len(parse_fhir(bundle).claims) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_fhir.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingest'`.

- [ ] **Step 3: Implement** — `app/ingest/fhir.py`

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.models import Claim, LineItem


@dataclass(frozen=True)
class ParseError:
    path: str
    message: str


@dataclass
class ParseResult:
    claims: list[Claim] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)


def pseudonym(patient_reference: str) -> str:
    return "P-" + hashlib.sha256(patient_reference.encode()).hexdigest()[:8]


def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    if isinstance(exc, KeyError):
        return f"missing field {exc.args[0]!r}"
    return str(exc) or exc.__class__.__name__


def _charge(item: dict[str, Any]) -> Decimal:
    net = (item.get("net") or {}).get("value")
    if net is None:
        for adj in item.get("adjudication", []):
            codes = {c.get("code") for c in (adj.get("category") or {}).get("coding", [])}
            if "submitted" in codes:
                net = adj["amount"]["value"]
                break
    if net is None:
        raise ValueError("no charge: item.net and submitted adjudication missing")
    return Decimal(str(net))


def _units(item: dict[str, Any]) -> int:
    q = Decimal(str((item.get("quantity") or {}).get("value", 1)))
    if q != q.to_integral_value():
        raise ValueError(f"quantity must be a whole number, got {q}")
    return int(q)


def _parse_item(item: dict[str, Any], index: int, diag: dict[int, str], default_dos: str | None) -> LineItem:
    seq = int(item.get("sequence", index + 1))
    codings = item["productOrService"]["coding"]
    coding = next(
        (c for c in codings if any(s in str(c.get("system", "")).lower() for s in ("cpt", "hcpcs"))),
        codings[0],
    )
    dos_raw = item.get("servicedDate") or (item.get("servicedPeriod") or {}).get("start") or default_dos
    if not dos_raw:
        raise ValueError("no date of service")
    loc = item.get("locationCodeableConcept")
    return LineItem(
        id=f"L{seq}",
        code=coding["code"],
        modifiers=[m["coding"][0]["code"] for m in item.get("modifier", [])],
        units=_units(item),
        charge=_charge(item),
        date_of_service=date.fromisoformat(str(dos_raw)[:10]),
        place_of_service=loc["coding"][0]["code"] if loc else None,
        diagnosis_codes=[diag[s] for s in item.get("diagnosisSequence", []) if s in diag],
    )


def _parse_eob(eob: dict[str, Any], path: str, errors: list[ParseError]) -> Claim | None:
    eob_id = eob.get("id")
    if not eob_id:
        errors.append(ParseError(f"{path}.id", "missing id"))
        return None
    diag: dict[int, str] = {}
    for d in eob.get("diagnosis", []):
        try:
            diag[int(d["sequence"])] = d["diagnosisCodeableConcept"]["coding"][0]["code"]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    default_dos = (eob.get("billablePeriod") or {}).get("start")
    lines = []
    for j, item in enumerate(eob.get("item", [])):
        try:
            lines.append(_parse_item(item, j, diag, default_dos))
        except (KeyError, IndexError, TypeError, ValueError, InvalidOperation) as exc:
            errors.append(ParseError(f"{path}.item[{j}]", _describe(exc)))
    if not lines:
        errors.append(ParseError(f"{path}.item", "no valid items"))
        return None
    return Claim(
        id=str(eob_id),
        patient_pseudonym=pseudonym(str((eob.get("patient") or {}).get("reference", "unknown"))),
        provider=(eob.get("provider") or {}).get("display"),
        payer=(eob.get("insurer") or {}).get("display"),
        lines=lines,
        source="fhir",
    )


def parse_fhir(data: object) -> ParseResult:
    result = ParseResult()
    if not isinstance(data, dict):
        result.errors.append(ParseError("$", "expected a JSON object"))
        return result
    rtype = data.get("resourceType")
    if rtype == "ExplanationOfBenefit":
        eobs = [("$", data)]
    elif rtype == "Bundle":
        eobs = [
            (f"$.entry[{i}].resource", e["resource"])
            for i, e in enumerate(data.get("entry", []))
            if isinstance(e, dict)
            and isinstance(e.get("resource"), dict)
            and e["resource"].get("resourceType") == "ExplanationOfBenefit"
        ]
    else:
        result.errors.append(
            ParseError("$.resourceType", f"expected Bundle or ExplanationOfBenefit, got {rtype!r}")
        )
        return result
    for path, eob in eobs:
        claim = _parse_eob(eob, path, result.errors)
        if claim is not None:
            result.claims.append(claim)
    return result


def claim_to_eob(claim: Claim) -> dict[str, object]:
    diags = sorted({d for line in claim.lines for d in line.diagnosis_codes})
    dseq = {d: i + 1 for i, d in enumerate(diags)}
    items: list[dict[str, object]] = []
    for i, line in enumerate(claim.lines):
        it: dict[str, object] = {
            "sequence": i + 1,
            "productOrService": {"coding": [{"system": "http://www.ama-assn.org/go/cpt", "code": line.code}]},
            "servicedDate": line.date_of_service.isoformat(),
            "quantity": {"value": line.units},
            "net": {"value": float(line.charge), "currency": "USD"},
        }
        if line.modifiers:
            it["modifier"] = [{"coding": [{"code": m}]} for m in line.modifiers]
        if line.place_of_service:
            it["locationCodeableConcept"] = {"coding": [{"code": line.place_of_service}]}
        if line.diagnosis_codes:
            it["diagnosisSequence"] = [dseq[d] for d in line.diagnosis_codes]
        items.append(it)
    eob: dict[str, object] = {
        "resourceType": "ExplanationOfBenefit",
        "id": claim.id,
        "status": "active",
        "use": "claim",
        "patient": {"reference": f"Patient/{claim.patient_pseudonym}"},
        "diagnosis": [
            {"sequence": dseq[d], "diagnosisCodeableConcept": {"coding": [{"code": d}]}} for d in diags
        ],
        "item": items,
    }
    if claim.provider:
        eob["provider"] = {"display": claim.provider}
    if claim.payer:
        eob["insurer"] = {"display": claim.payer}
    return eob


def claims_to_bundle(claims: list[Claim]) -> dict[str, object]:
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": claim_to_eob(c)} for c in claims],
    }
```

- [ ] **Step 4: Run tests**

Run: `pytest -q && ruff format . && ruff check . && mypy app`
Expected: all pass; clean.

- [ ] **Step 5: Commit**

```bash
git add app/ingest tests/test_fhir.py
git commit -m "feat: FHIR ExplanationOfBenefit parser with per-item errors, and EOB writer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Synthetic labeled-claim generator

**Files:**
- Create: `evals/__init__.py` (empty), `evals/generate.py`, `tests/test_generate.py`

**Interfaces:**
- Consumes: `InMemoryReference` (public lists + lookups), `KINDS`, `Claim`, `LineItem`, `money`.
- Produces:
  - `@dataclass LabeledClaim(claim: Claim, expected: set[tuple[str, frozenset[str]]], planted: list[str])`
  - `PLANTABLE = ("R1", "R2", "R3", "R4", "R5")`
  - `generate(ref: InMemoryReference, n: int, seed: int, error_rate: float = 0.6) -> list[LabeledClaim]`
  - Guarantees: line ids `L1..Ln` in order; clean lines charge 1.2–2.5× the non-facility rate at POS `11`; planted errors don't trigger other rules.

- [ ] **Step 1: Write the failing tests** — `tests/test_generate.py` (uses the fixture reference so it runs without the CMS subset)

```python
from collections import Counter

from evals.generate import PLANTABLE, generate
from tests.helpers import FIXTURE_REF


def test_deterministic_for_seed() -> None:
    a = generate(FIXTURE_REF, 20, seed=1)
    b = generate(FIXTURE_REF, 20, seed=1)
    assert [x.claim.model_dump() for x in a] == [x.claim.model_dump() for x in b]
    assert [x.expected for x in a] == [x.expected for x in b]


def test_line_ids_are_sequential() -> None:
    for lc in generate(FIXTURE_REF, 30, seed=2):
        assert [x.id for x in lc.claim.lines] == [f"L{i + 1}" for i in range(len(lc.claim.lines))]


def test_clean_claims_have_no_expected_flags_and_errors_are_labeled() -> None:
    out = generate(FIXTURE_REF, 100, seed=3, error_rate=0.5)
    clean = [x for x in out if not x.planted]
    dirty = [x for x in out if x.planted]
    assert clean and dirty
    assert all(not x.expected for x in clean)
    assert all(len(x.expected) == len(x.planted) for x in dirty)
    kinds = Counter(k for x in dirty for k in x.planted)
    assert set(kinds) <= set(PLANTABLE)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_generate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals'`.

- [ ] **Step 3: Implement** — `evals/generate.py`

```python
"""Synthetic claims with planted, labeled billing errors (real codes, fake patients)."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models import Claim, LineItem, money
from app.reference.base import KINDS, InMemoryReference

PLANTABLE = ("R1", "R2", "R3", "R4", "R5")
Expected = tuple[str, frozenset[str]]


@dataclass
class LabeledClaim:
    claim: Claim
    expected: set[Expected]
    planted: list[str]


def coverage_window(ref: InMemoryReference) -> tuple[date, date]:
    starts, ends = [], []
    for kind in KINDS:
        vs = [v for v in ref.versions if v.kind == kind]
        if not vs:
            raise ValueError(f"reference has no {kind} version")
        starts.append(min(v.valid_from for v in vs))
        ends.append(max(v.valid_to for v in vs))
    lo, hi = max(starts), min(ends)
    if lo > hi:
        raise ValueError("reference versions do not overlap")
    return lo, hi


def _pair_edit(ref: InMemoryReference, a: str, b: str, dos: date) -> bool:
    return ref.ptp(a, b, dos) is not None or ref.ptp(b, a, dos) is not None


def _conflicts(ref: InMemoryReference, code: str, lines: list[LineItem], dos: date) -> bool:
    return any(code == x.code or _pair_edit(ref, code, x.code, dos) for x in lines)


def _safe_codes(ref: InMemoryReference, dos: date) -> list[str]:
    out = []
    for f in sorted(ref.fees, key=lambda f: f.code):
        mue = ref.mue(f.code, dos)
        status = ref.code_status(f.code, dos)
        if (
            ref.fee(f.code, dos) is f
            and f.nonfacility > 0
            and mue is not None
            and mue.max_units >= 2
            and (status is None or status.status != "D")
        ):
            out.append(f.code)
    return out


def _mult(rng: random.Random, lo: float, hi: float) -> Decimal:
    return Decimal(str(round(rng.uniform(lo, hi), 2)))


def _line(
    ref: InMemoryReference,
    rng: random.Random,
    dos: date,
    code: str,
    idx: int,
    units: int = 1,
    mult: tuple[float, float] = (1.2, 2.5),
) -> LineItem:
    fee = ref.fee(code, dos)
    if fee is not None and fee.nonfacility > 0:
        charge = money(fee.nonfacility * units * _mult(rng, *mult))
    else:
        charge = Decimal("100.00") * units
    return LineItem(
        id=f"L{idx}", code=code, units=units, charge=charge, date_of_service=dos, place_of_service="11"
    )


def _plant_r1(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    src = rng.choice(lines)
    dup = src.model_copy(update={"id": f"L{len(lines) + 1}"})
    lines.append(dup)
    return ("R1", frozenset({src.id, dup.id}))


def _plantable(ref: InMemoryReference, code: str, dos: date) -> bool:
    status = ref.code_status(code, dos)
    mue = ref.mue(code, dos)
    return (status is None or status.status != "D") and (mue is None or mue.max_units >= 1)


def _plant_r2(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    edits = [
        e for e in ref.ptp_edits if e.modifier_indicator in {"0", "1"} and ref.ptp(e.col1, e.col2, dos) is e
    ]
    rng.shuffle(edits)
    for e in edits:
        if not (_plantable(ref, e.col1, dos) and _plantable(ref, e.col2, dos)):
            continue
        if _conflicts(ref, e.col1, lines, dos) or _conflicts(ref, e.col2, lines, dos):
            continue
        a = _line(ref, rng, dos, e.col1, len(lines) + 1)
        lines.append(a)
        b = _line(ref, rng, dos, e.col2, len(lines) + 1)
        lines.append(b)
        return ("R2", frozenset({a.id, b.id}))
    return None


def _plant_r3(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    cands = [c for c in safe if not _conflicts(ref, c, lines, dos)]
    if not cands:
        return None
    code = rng.choice(cands)
    mue = ref.mue(code, dos)
    assert mue is not None
    new = _line(ref, rng, dos, code, len(lines) + 1, units=mue.max_units + rng.randint(1, 3))
    lines.append(new)
    return ("R3", frozenset({new.id}))


def _plant_r4(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    deleted = sorted(
        c.code for c in ref.code_statuses if c.status == "D" and ref.code_status(c.code, dos) is c
    )
    rng.shuffle(deleted)
    for code in deleted:
        mue = ref.mue(code, dos)
        if (mue is not None and mue.max_units < 1) or _conflicts(ref, code, lines, dos):
            continue
        new = LineItem(
            id=f"L{len(lines) + 1}",
            code=code,
            units=1,
            charge=Decimal("100.00"),
            date_of_service=dos,
            place_of_service="11",
        )
        lines.append(new)
        return ("R4", frozenset({new.id}))
    return None


def _plant_r5(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    cands = [c for c in safe if not _conflicts(ref, c, lines, dos)]
    if not cands:
        return None
    new = _line(ref, rng, dos, rng.choice(cands), len(lines) + 1, mult=(3.5, 6.0))
    lines.append(new)
    return ("R5", frozenset({new.id}))


Planter = Callable[[InMemoryReference, random.Random, date, list[str], list[LineItem]], Expected | None]
PLANTERS: dict[str, Planter] = {
    "R1": _plant_r1,
    "R2": _plant_r2,
    "R3": _plant_r3,
    "R4": _plant_r4,
    "R5": _plant_r5,
}


def _clean_lines(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], count: int
) -> list[LineItem]:
    lines: list[LineItem] = []
    pool = safe[:]
    rng.shuffle(pool)
    for code in pool:
        if len(lines) == count:
            break
        if not _conflicts(ref, code, lines, dos):
            lines.append(_line(ref, rng, dos, code, len(lines) + 1))
    return lines


def generate(ref: InMemoryReference, n: int, seed: int, error_rate: float = 0.6) -> list[LabeledClaim]:
    rng = random.Random(seed)
    lo, hi = coverage_window(ref)
    out = []
    for i in range(n):
        dos = lo + timedelta(days=rng.randrange((hi - lo).days + 1))
        safe = _safe_codes(ref, dos)
        if not safe:
            raise ValueError("reference has no codes with fee + MUE >= 2; cannot generate claims")
        lines = _clean_lines(ref, rng, dos, safe, rng.randint(2, 5))
        expected: set[Expected] = set()
        planted: list[str] = []
        if rng.random() < error_rate:
            kinds = sorted(rng.sample(PLANTABLE, rng.choice([1, 1, 2])), key=PLANTABLE.index)
            for kind in kinds:
                got = PLANTERS[kind](ref, rng, dos, safe, lines)
                if got is not None:
                    expected.add(got)
                    planted.append(kind)
        out.append(
            LabeledClaim(
                claim=Claim(
                    id=f"C{i:04d}",
                    patient_pseudonym=f"P-{i:04d}",
                    provider="Synthetic Clinic",
                    payer="Synthetic Health Plan",
                    lines=lines,
                ),
                expected=expected,
                planted=planted,
            )
        )
    return out
```

Note on R1 ordering: kinds are applied in `PLANTABLE` order, so R1 always duplicates a clean line (never one planted by R3/R5).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_generate.py -v && ruff format . && ruff check . && mypy app evals`
Expected: 3 passed; clean.

- [ ] **Step 5: Commit**

```bash
git add evals tests/test_generate.py
git commit -m "feat: synthetic claim generator with planted, labeled billing errors

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Eval runner with CI gates

**Files:**
- Create: `evals/run.py`, `tests/test_eval_run.py`, `evals/results/latest.md` (generated, committed)
- Modify: `.github/workflows/ci.yml` (add eval step and `mypy app evals scripts`)

**Interfaces:**
- Consumes: `generate`, `LabeledClaim`, `parse_fhir`, `claim_to_eob`, `run_rules`, `Severity`, `load_normalized`.
- Produces:
  - `@dataclass RuleScore(rule_id: str, tp: int = 0, fp: int = 0, fn: int = 0)` with properties `precision`, `recall`, `support`
  - `@dataclass Report(scores: dict[str, RuleScore], claims: int, clean_claims: int, clean_fp: int, parse_errors: int)` with `gate_failures(min_support: int) -> list[str]` and `to_markdown() -> str`
  - `evaluate(labeled: list[LabeledClaim], ref: Reference, via_fhir: bool = True) -> Report`
  - CLI: `python -m evals.run --n 300 --seed 7 --ref data/reference/subset --out evals/results [--min-support 10]` → writes `latest.json` + `latest.md`, exit 1 on any gate failure

- [ ] **Step 1: Write the failing tests** — `tests/test_eval_run.py`

```python
from evals.generate import LabeledClaim, generate
from evals.run import evaluate
from tests.helpers import FIXTURE_REF, claim, line


def test_engine_scores_perfectly_on_fixture_generated_claims() -> None:
    report = evaluate(generate(FIXTURE_REF, 150, seed=5), FIXTURE_REF)
    assert report.parse_errors == 0
    assert report.clean_fp == 0
    for score in report.scores.values():
        assert score.precision == 1.0 and score.recall == 1.0, score


def test_false_positive_and_miss_are_counted() -> None:
    dup = claim(line("L1"), line("L2"))
    labeled = [
        LabeledClaim(
            claim=dup, expected=set(), planted=[]
        ),  # R1 will fire: false positive on a "clean" claim
        LabeledClaim(claim=claim(line("L1")), expected={("R5", frozenset({"L1"}))}, planted=["R5"]),  # miss
    ]
    report = evaluate(labeled, FIXTURE_REF, via_fhir=False)
    assert report.clean_fp == 1
    assert report.scores["R1"].fp == 1
    assert report.scores["R5"].fn == 1
    failures = report.gate_failures(min_support=0)
    assert any("R1" in f for f in failures) and any("R5" in f for f in failures)
    assert "| R1 |" in report.to_markdown()


def test_min_support_gate() -> None:
    report = evaluate([], FIXTURE_REF)
    assert any("support" in f for f in report.gate_failures(min_support=1))
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_eval_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.run'`.

- [ ] **Step 3: Implement** — `evals/run.py`

```python
"""Score the rule engine against labeled synthetic claims; exit non-zero when a CI gate fails."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.ingest.fhir import claim_to_eob, parse_fhir
from app.models import Severity
from app.reference.base import Reference
from app.reference.normalized import load_normalized
from app.rules import run_rules
from evals.generate import PLANTABLE, LabeledClaim, generate

SCORED = (Severity.ERROR, Severity.OUTLIER)


@dataclass
class RuleScore:
    rule_id: str
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def support(self) -> int:
        return self.tp + self.fn


@dataclass
class Report:
    scores: dict[str, RuleScore] = field(default_factory=lambda: {r: RuleScore(r) for r in PLANTABLE})
    claims: int = 0
    clean_claims: int = 0
    clean_fp: int = 0
    parse_errors: int = 0

    def gate_failures(self, min_support: int) -> list[str]:
        out = []
        for s in self.scores.values():
            if s.precision < 1.0 or s.recall < 1.0:
                out.append(f"{s.rule_id}: precision {s.precision:.3f}, recall {s.recall:.3f} (gate 1.000)")
            if s.support < min_support:
                out.append(f"{s.rule_id}: support {s.support} < {min_support}")
        if self.clean_fp:
            out.append(f"clean claims with flags: {self.clean_fp} (gate 0)")
        if self.parse_errors:
            out.append(f"FHIR parse errors: {self.parse_errors} (gate 0)")
        return out

    def to_markdown(self) -> str:
        rows = ["| Rule | Support | TP | FP | FN | Precision | Recall |", "|---|---|---|---|---|---|---|"]
        rows += [
            f"| {s.rule_id} | {s.support} | {s.tp} | {s.fp} | {s.fn} | {s.precision:.3f} | {s.recall:.3f} |"
            for s in self.scores.values()
        ]
        rows.append("")
        rows.append(
            f"Claims: {self.claims} (clean: {self.clean_claims}, clean with flags: {self.clean_fp}); "
            f"FHIR parse errors: {self.parse_errors}"
        )
        return "\n".join(rows)


def evaluate(labeled: list[LabeledClaim], ref: Reference, via_fhir: bool = True) -> Report:
    report = Report()
    for lc in labeled:
        report.claims += 1
        claim = lc.claim
        if via_fhir:
            parsed = parse_fhir(claim_to_eob(claim))
            report.parse_errors += len(parsed.errors)
            if not parsed.claims:
                continue
            claim = parsed.claims[0]
        got = {(f.rule_id, frozenset(f.line_ids)) for f in run_rules(claim, ref) if f.severity in SCORED}
        for rule_id, score in report.scores.items():
            exp = {e for e in lc.expected if e[0] == rule_id}
            hit = {g for g in got if g[0] == rule_id}
            score.tp += len(exp & hit)
            score.fp += len(hit - exp)
            score.fn += len(exp - hit)
        if not lc.expected:
            report.clean_claims += 1
            report.clean_fp += 1 if got else 0
    return report


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=300)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--ref", type=Path, default=Path("data/reference/subset"))
    p.add_argument("--out", type=Path, default=Path("evals/results"))
    p.add_argument("--min-support", type=int, default=10)
    a = p.parse_args()

    ref = load_normalized(a.ref)
    report = evaluate(generate(ref, a.n, a.seed), ref)
    a.out.mkdir(parents=True, exist_ok=True)
    summary = {
        "n": a.n,
        "seed": a.seed,
        "reference": [v.ref_version for v in ref.versions],
        "claims": report.claims,
        "clean_claims": report.clean_claims,
        "clean_fp": report.clean_fp,
        "parse_errors": report.parse_errors,
        "rules": {
            k: {**asdict(s), "precision": s.precision, "recall": s.recall} for k, s in report.scores.items()
        },
    }
    (a.out / "latest.json").write_text(json.dumps(summary, indent=2) + "\n")
    (a.out / "latest.md").write_text(report.to_markdown() + "\n")
    print(report.to_markdown())
    failures = report.gate_failures(a.min_support)
    for f in failures:
        print(f"GATE FAILED: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests and the real eval**

Run: `pytest -q && python -m evals.run --n 300 --seed 7`
Expected: tests pass; eval prints a table with every rule at precision/recall 1.000, support ≥ 10 each, `clean with flags: 0`, `FHIR parse errors: 0`; exit 0.
If a gate fails: read the failing rule's FP/FN, inspect one failing claim (`generate(ref, 300, 7)[i]`), decide whether the bug is in the rule or the generator, fix it, and add a regression test to that rule's test file before rerunning.

- [ ] **Step 5: Add eval gate to CI** — `.github/workflows/ci.yml`: replace `- run: mypy app` with `- run: mypy app evals scripts`, and append after pytest:

```yaml
      - run: python -m evals.run --n 300 --seed 7
```

- [ ] **Step 6: Commit**

```bash
ruff format . && ruff check . && mypy app evals scripts
git add evals tests/test_eval_run.py .github/workflows/ci.yml
git commit -m "feat: eval runner with precision/recall gates in CI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: `/api/version` with reference versions, and first Vercel deploy

**Files:**
- Modify: `app/main.py`, `tests/test_main.py`
- Create: `vercel.json`

**Interfaces:**
- Consumes: `load_normalized`, `__version__`.
- Produces: `GET /api/version -> {"version": str, "reference": [{"ref_version","kind","valid_from","valid_to"}]}`; `get_reference() -> InMemoryReference` (cached, read-only); a production URL on Vercel.

- [ ] **Step 1: Write the failing test** — append to `tests/test_main.py`

```python
def test_version_reports_loaded_reference() -> None:
    body = client.get("/api/version").json()
    assert body["version"].startswith("2.")
    kinds = {r["kind"] for r in body["reference"]}
    assert kinds == {"ncci", "mue", "pfs"}
    assert all(r["valid_from"] <= r["valid_to"] for r in body["reference"])
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_main.py -v`
Expected: FAIL — 404 on `/api/version` (KeyError on `"version"`).

- [ ] **Step 3: Implement** — add to `app/main.py`

```python
from functools import lru_cache
from pathlib import Path

from app.reference.base import InMemoryReference
from app.reference.normalized import load_normalized

REF_DIR = Path(__file__).resolve().parent.parent / "data" / "reference" / "subset"


@lru_cache(maxsize=1)
def get_reference() -> InMemoryReference:
    return load_normalized(REF_DIR)


@app.get("/api/version")
def version() -> dict[str, object]:
    ref = get_reference()
    return {
        "version": __version__,
        "reference": [
            {
                "ref_version": v.ref_version,
                "kind": v.kind,
                "valid_from": v.valid_from.isoformat(),
                "valid_to": v.valid_to.isoformat(),
            }
            for v in ref.versions
        ],
    }
```
(Merge the imports into the top of the file; ruff will order them.)

`vercel.json`:
```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "functions": {
    "app/main.py": { "includeFiles": "data/reference/subset/**" }
  }
}
```

- [ ] **Step 4: Run tests**

Run: `pytest -q && ruff format . && ruff check . && mypy app evals scripts`
Expected: all pass; clean.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main.py vercel.json
git commit -m "feat: /api/version reports loaded CMS reference releases; Vercel config

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Deploy (needs the human for login)**

```bash
npm i -g vercel
```
Human runs in the Claude Code prompt: `! vercel login`
Then:
```bash
vercel link --yes --project priorpath
vercel deploy
```
Expected: a preview URL. Verify:
```bash
curl -s <preview-url>/api/health      # {"status":"ok"}
curl -s <preview-url>/api/version     # version + three reference releases
```
If `/api/version` returns 500 with a missing-file error, the subset wasn't bundled: check the `includeFiles` path in `vercel.json` and redeploy. Then deploy production: `vercel deploy --prod` and verify the same two endpoints on the production URL.

- [ ] **Step 7: Connect GitHub for automatic deploys (needs the human's OK before pushing)**

Ask before pushing. Then:
```bash
git push -u origin v2-bill-audit
vercel git connect
```
In the Vercel dashboard confirm Production Branch = `main`. Pushes to `v2-bill-audit` now create preview deployments; merging to `main` deploys production. Confirm the GitHub Actions `ci` run on the pushed branch is green.

---

## Self-Review (done while writing)

- **Spec coverage (week-1 scope):** §4.1 reference tables + versioning → Tasks 2–3; R0 "cannot audit" (§4.1, §9) → Task 4; §4.3 models → Task 1 (case_id, Letter, AuditEvent deferred to Plan 2 with the database); §5 rules R1–R5 → Tasks 4–7; §4.2 generator → Task 9; §8 rows 1–2 gates → Task 10; §11 CI → Tasks 0, 10; §13 week-1 deploy → Task 11. Synthea (§4.2 optional) is deferred: the generator covers the eval need; Synthea's code coverage gets checked in Plan 2 if realism is still wanted.
- **Spec deviation, intentional:** `Severity.NOTICE` added for R0 so "cannot audit" never counts as an error; the spec's three severities are unchanged.
- **Type consistency:** `check_*` all take `(Claim, Reference, RuleConfig)`; `make_flag` signature matches all call sites; line ids `L{n}` match between generator, writer and parser.
- **Review Focus:** all five items have tests in Tasks 1, 2, 4, 5, 8.
