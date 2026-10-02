from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.deps import get_reference, get_vision
from app.llm.vision import VisionError
from app.main import app
from app.models import Claim
from app.services import llm_budget, pdf_cases
from evals.pdf_render import render_bill
from tests.api_helpers import sample_claim
from tests.fakes import FakeVision
from tests.helpers import FIXTURE_REF, line

PDF = {"content-type": "application/pdf"}


def teardown_function() -> None:
    app.dependency_overrides.clear()


def fv(claim: Claim, conf: float = 0.99) -> dict[str, Any]:
    def f(v: Any) -> dict[str, Any]:
        return {"value": v, "confidence": conf}

    return {
        "claim_id": "BILL-9",
        "provider": "Test Clinic",
        "payer": "Test Plan",
        "lines": [
            {
                "code": f(ln.code),
                "modifiers": f(ln.modifiers),
                "units": f(ln.units),
                "charge": f(str(ln.charge)),
                "date_of_service": f(ln.date_of_service.isoformat()),
                "place_of_service": f(ln.place_of_service or ""),
            }
            for ln in claim.lines
        ],
    }


def client(vision: FakeVision | None) -> TestClient:
    app.dependency_overrides[get_reference] = lambda: FIXTURE_REF
    app.dependency_overrides[get_vision] = lambda: vision
    return TestClient(app)


def post_pdf(c: TestClient, data: bytes, confirm: bool = True) -> Any:
    return c.post(f"/api/cases?confirm_synthetic={'true' if confirm else 'false'}", content=data, headers=PDF)


def pdf_case(conf: float = 0.99) -> tuple[TestClient, str]:
    c = client(FakeVision([fv(sample_claim(), conf)]))
    r = post_pdf(c, render_bill(sample_claim(), "table"))
    assert r.status_code == 201, r.text
    return c, r.json()["cases"][0]["id"]


def test_pdf_upload_extracts_audits_and_serves_pages(db: Engine) -> None:
    c, case_id = pdf_case()
    detail = c.get(f"/api/cases/{case_id}").json()
    assert detail["source"] == "pdf" and detail["status"] == "needs_review" and detail["page_count"] == 1
    assert any(f["rule_id"] == "R1" for f in detail["flags"])
    assert detail["lines"][0]["source"] == "extracted" and detail["lines"][0]["confidence"] == 0.99
    page = c.get(f"/api/cases/{case_id}/pages/1")
    assert page.status_code == 200 and page.headers["content-type"] == "image/jpeg"
    assert page.content.startswith(b"\xff\xd8\xff")
    assert c.get(f"/api/cases/{case_id}/pages/2").status_code == 404
    assert c.get(f"/api/cases/{case_id}/pages/0").status_code == 404


def test_low_confidence_goes_to_line_review_without_flags(db: Engine) -> None:
    c, case_id = pdf_case(conf=0.5)
    detail = c.get(f"/api/cases/{case_id}").json()
    assert detail["status"] == "needs_line_review" and detail["flags"] == []


def test_audit_refused_until_lines_are_reviewed(db: Engine) -> None:
    c, case_id = pdf_case(conf=0.5)
    r = c.post(f"/api/cases/{case_id}/audit")
    assert r.status_code == 409 and r.json()["detail"] == "review the extracted lines first"
    assert c.get(f"/api/cases/{case_id}").json()["status"] == "needs_line_review"
    assert c.patch(f"/api/cases/{case_id}/lines", json={"lines": [edit("A")]}).status_code == 200
    assert c.post(f"/api/cases/{case_id}/audit").status_code == 200


def test_confirmation_required_and_vision_not_called(db: Engine) -> None:
    v = FakeVision([fv(sample_claim())])
    r = post_pdf(client(v), render_bill(sample_claim(), "table"), confirm=False)
    assert r.status_code == 422 and "synthetic" in r.json()["detail"]
    assert v.calls == []


def test_not_a_pdf(db: Engine) -> None:
    r = post_pdf(client(FakeVision([])), b"not a pdf")
    assert r.status_code == 422 and "not a PDF" in r.json()["detail"]


def test_no_vision_client_is_503(db: Engine) -> None:
    r = post_pdf(client(None), render_bill(sample_claim(), "table"))
    assert r.status_code == 503 and "not configured" in r.json()["detail"]


def two_page_pdf() -> bytes:
    big = Claim(id="BIG", patient_pseudonym="P", lines=[line(f"L{i}", charge="10.00") for i in range(45)])
    return render_bill(big, "table")


def ten_page_pdf() -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    for _ in range(10):
        pdf.add_page()
    return bytes(pdf.output())


def test_too_few_budget_units_fail_before_any_model_call(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_budget, "EXTRACT_PAGES_PER_HOUR", 5)
    v = FakeVision([fv(sample_claim())] * 10)
    c = client(v)
    r = post_pdf(c, ten_page_pdf())
    assert r.status_code == 429 and r.json()["detail"] == "hourly AI limit reached; try again later"
    assert v.calls == [] and c.get("/api/cases").json() == []


def test_budget_exhausted_mid_document_stores_nothing(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # A concurrent upload can use the units after the up-front check; per-page consumption still holds.
    monkeypatch.setattr(llm_budget, "EXTRACT_PAGES_PER_HOUR", 1)
    monkeypatch.setattr(llm_budget, "remaining", lambda *a, **k: 99)
    v = FakeVision([fv(sample_claim()), fv(sample_claim())])
    c = client(v)
    r = post_pdf(c, two_page_pdf())
    assert r.status_code == 429 and r.json()["detail"] == "hourly AI limit reached; try again later"
    assert len(v.calls) == 1 and c.get("/api/cases").json() == []


def test_extraction_deadline_returns_504_and_stores_nothing(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter([0.0, 0.0, pdf_cases.EXTRACT_DEADLINE_SECONDS + 1.0])  # start, page 1, page 2
    monkeypatch.setattr(pdf_cases, "monotonic", lambda: next(clock))
    v = FakeVision([fv(sample_claim()), fv(sample_claim())])
    c = client(v)
    r = post_pdf(c, two_page_pdf())
    assert r.status_code == 504 and r.json()["detail"] == "this bill took too long to read; try a shorter PDF"
    assert len(v.calls) == 1 and c.get("/api/cases").json() == []


def test_vision_error_on_page_two_stores_nothing(db: Engine) -> None:
    c = client(FakeVision([fv(sample_claim()), VisionError("boom")]))
    r = post_pdf(c, two_page_pdf())
    assert r.status_code == 502 and r.json()["detail"] == "the AI model could not read this bill; try again"
    assert c.get("/api/cases").json() == []


def test_bad_rows_are_reported_with_capped_messages(db: Engine) -> None:
    page = fv(sample_claim())
    page["lines"][0]["date_of_service"]["value"] = "x" * 5000
    c = client(FakeVision([page]))
    r = post_pdf(c, render_bill(sample_claim(), "table"))
    errs = r.json()["errors"]
    assert r.status_code == 201 and len(errs) == 1
    assert errs[0]["path"] == "page 1, row 1" and 0 < len(errs[0]["message"]) <= 160


def edit(i: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": i, "code": "99213", "modifiers": [], "units": 1, "charge": "100.00",
        "date_of_service": "2026-10-15", "place_of_service": "11",
    }  # fmt: skip
    return base | kw


def test_patch_lines_clears_flags_then_audit_recomputes(db: Engine) -> None:
    c, case_id = pdf_case()
    r = c.patch(f"/api/cases/{case_id}/lines", json={"lines": [edit("A"), edit("B", code="96372")]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "uploaded" and body["flags"] == [] and body["line_count"] == 2
    assert body["lines"][0]["confidence"] == 1.0 and body["lines"][0]["source"] == "extracted"
    audited = c.post(f"/api/cases/{case_id}/audit").json()
    assert audited["status"] == "needs_review"
    events = c.get("/api/audit-log").json()
    assert "lines_edited" in [e["action"] for e in events]


def test_patch_lines_validates(db: Engine) -> None:
    c, case_id = pdf_case()
    before = c.get(f"/api/cases/{case_id}").json()["lines"]
    r = c.patch(f"/api/cases/{case_id}/lines", json={"lines": [edit("A"), edit("B", units=0)]})
    assert r.status_code == 422 and "lines[1].units" in str(r.json()["detail"])
    r = c.patch(f"/api/cases/{case_id}/lines", json={"lines": [edit("A", place_of_service="1A")]})
    assert r.status_code == 422 and "lines[0].place_of_service" in str(r.json()["detail"])
    assert c.patch(f"/api/cases/{case_id}/lines", json={"lines": []}).status_code == 422
    assert c.patch(f"/api/cases/{case_id}/lines", json={"lines": [edit("A"), edit("A")]}).status_code == 422
    assert c.get(f"/api/cases/{case_id}").json()["lines"] == before
    r = c.patch(f"/api/cases/{case_id}/lines", json={"lines": [edit("A", place_of_service=None)]})
    assert r.status_code == 200 and r.json()["lines"][0]["place_of_service"] is None


def test_patch_lines_blocked_after_approved_letter(db: Engine) -> None:
    c, case_id = pdf_case()
    for f in c.get(f"/api/cases/{case_id}").json()["flags"]:
        if f["rule_id"] in ("R1", "R5"):
            c.patch(f"/api/flags/{f['id']}", json={"status": "accepted"})
    letter = c.post(f"/api/cases/{case_id}/letter").json()
    assert c.post(f"/api/letters/{letter['id']}/approve").status_code == 200
    r = c.patch(f"/api/cases/{case_id}/lines", json={"lines": [edit("A")]})
    assert r.status_code == 409


def test_other_workspace_gets_404(db: Engine) -> None:
    _, case_id = pdf_case()
    other = TestClient(app)
    assert other.get(f"/api/cases/{case_id}/pages/1").status_code == 404
    assert other.patch(f"/api/cases/{case_id}/lines", json={"lines": [edit("A")]}).status_code == 404


def test_page_endpoint_renders_only_the_requested_page(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    import pypdfium2 as pdfium

    c = client(FakeVision([fv(sample_claim()), fv(sample_claim())]))
    r = post_pdf(c, two_page_pdf())
    assert r.status_code == 201 and r.json()["cases"][0]["page_count"] == 2
    renders: list[int] = []
    original = pdfium.PdfPage.render
    monkeypatch.setattr(
        pdfium.PdfPage, "render", lambda self, *a, **k: (renders.append(1), original(self, *a, **k))[1]
    )
    page = c.get(f"/api/cases/{r.json()['cases'][0]['id']}/pages/2")
    assert page.status_code == 200 and renders == [1]
    assert page.headers["cache-control"] == "private, max-age=3600"
