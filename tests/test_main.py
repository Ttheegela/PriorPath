from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_docs_served_under_api() -> None:
    assert client.get("/api/docs").status_code == 200


def test_version_reports_loaded_reference() -> None:
    body = client.get("/api/version").json()
    assert body["version"].startswith("2.")
    kinds = {r["kind"] for r in body["reference"]}
    assert kinds == {"ncci", "mue", "pfs"}
    assert all(r["valid_from"] <= r["valid_to"] for r in body["reference"])
