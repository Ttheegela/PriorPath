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


def _flag_id(c: TestClient, case_id: str, rule: str) -> str:
    flags = c.get(f"/api/cases/{case_id}").json()["flags"]
    return next(f["id"] for f in flags if f["rule_id"] == rule)


def test_edit_baseline_is_the_drafted_text(db: Engine) -> None:
    c, case_id = reviewed()
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    c.patch(f"/api/flags/{_flag_id(c, case_id, 'R5')}", json={"status": "rejected", "reject_reason": "ok"})
    ok = c.patch(f"/api/letters/{letter['id']}", json={"body": "Dear billing team,\n" + letter["body"]})
    assert ok.status_code == 200


def test_edit_cannot_add_amount_of_later_accepted_flag(db: Engine) -> None:
    c, case_id = reviewed(accept=("R1",))
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    c.patch(f"/api/flags/{_flag_id(c, case_id, 'R5')}", json={"status": "accepted"})
    bad = c.patch(f"/api/letters/{letter['id']}", json={"body": letter["body"] + "\nAlso $23.55."})
    assert bad.status_code == 422


def test_edit_may_delete_an_amount(db: Engine) -> None:
    c, case_id = reviewed()
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    body = "\n".join(ln for ln in letter["body"].split("\n") if "Total estimated overcharge" not in ln)
    assert c.patch(f"/api/letters/{letter['id']}", json={"body": body}).status_code == 200


def test_approve_requires_unchanged_findings(db: Engine) -> None:
    c, case_id = reviewed()
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    c.patch(f"/api/flags/{_flag_id(c, case_id, 'R5')}", json={"status": "rejected", "reject_reason": "ok"})
    assert c.post(f"/api/letters/{letter['id']}/approve").status_code == 409
    again = c.post(f"/api/cases/{case_id}/letter").json()
    assert c.post(f"/api/letters/{again['id']}/approve").status_code == 200


def test_no_new_draft_after_approval(db: Engine) -> None:
    c, case_id = reviewed()
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    c.post(f"/api/letters/{letter['id']}/approve")
    assert c.post(f"/api/cases/{case_id}/letter").status_code == 409


def test_edit_rejects_control_characters(db: Engine) -> None:
    c, case_id = reviewed()
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    for bad in ("a\x00b", "a\x0bb"):
        assert c.patch(f"/api/letters/{letter['id']}", json={"body": bad}).status_code == 422
