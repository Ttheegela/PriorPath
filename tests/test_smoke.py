from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.main import app
from scripts import smoke
from tests.api_helpers import sample_claim, upload


def test_smoke_passes_with_more_than_ten_demo_cases(
    db: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A workspace with the 10 FHIR demo cases plus extra ones (e.g. seeded PDF bills) still passes."""
    monkeypatch.setenv("PRIORPATH_DEMO", "1")

    def in_process(**_: Any) -> TestClient:
        c = TestClient(app)
        assert upload(c, [sample_claim("EXTRA-1")]).status_code == 201
        return c

    monkeypatch.setattr(smoke.httpx, "Client", in_process)
    monkeypatch.setattr("sys.argv", ["smoke.py", "http://testserver"])
    assert smoke.main() == 0
    assert capsys.readouterr().out.startswith("ok: case ")
