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
    assert (
        TestClient(app).get("/api/workspace").json()["id"]
        != TestClient(app).get("/api/workspace").json()["id"]
    )
