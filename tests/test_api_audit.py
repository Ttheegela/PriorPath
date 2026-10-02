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
    assert (
        c.patch(f"/api/flags/{flags['R1']['id']}", json={"status": "accepted"}).json()["status"] == "accepted"
    )
    assert c.patch(f"/api/flags/{flags['R5']['id']}", json={"status": "rejected"}).status_code == 422
    blank = {"status": "rejected", "reject_reason": "  "}
    assert c.patch(f"/api/flags/{flags['R5']['id']}", json=blank).status_code == 422
    r = c.patch(
        f"/api/flags/{flags['R5']['id']}", json={"status": "rejected", "reject_reason": "contracted rate"}
    )
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
