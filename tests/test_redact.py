import io
import subprocess
import sys
from typing import Any

import pypdfium2 as pdfium
import pytest
from fpdf import FPDF
from PIL import Image
from sqlalchemy import Engine

from app.ingest.pdf import RENDER_DPI, pdf_page_images, pdf_page_images_with_redaction, render_page
from app.ingest.redact import REDACT_ENTITIES, analyzer, find_phi_boxes, mask
from evals.pdf_render import LAYOUTS, add_scan_noise, render_bill
from tests.api_helpers import sample_claim
from tests.helpers import line
from tests.test_api_pdf import client, fv, post_pdf

SCALE = RENDER_DPI / 72
NAME = "Alex Example"
PHI = (NAME, "1200 Maple Avenue, Springfield, IL 62704", "(217) 555-0143", "XQH4471029")
Box = tuple[float, float, float, float]


def _claim() -> Any:
    c = sample_claim()
    c.lines.append(line("L5", code="20610", units=2, charge="125.50", modifiers=["59", "RT"], pos="22"))
    return c


def _boxes_of(tp: pdfium.PdfTextPage, start: int, end: int) -> list[Box]:
    return [tp.get_charbox(i) for i in range(start, end) if not tp.get_text_range(i, 1).isspace()]


def _find(tp: pdfium.PdfTextPage, needle: str) -> Box:
    """Union of the char boxes of a single-line needle."""
    i = tp.get_text_range().index(needle)
    boxes = _boxes_of(tp, i, i + len(needle))
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _covers(outer: Box, inner: Box) -> bool:
    e = 0.01
    return (
        outer[0] <= inner[0] + e
        and outer[1] <= inner[1] + e
        and outer[2] >= inner[2] - e
        and outer[3] >= inner[3] - e
    )


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _px(box: Box, height_pt: float) -> tuple[int, int, int, int]:
    left, bottom, right, top = box
    return (
        int(left * SCALE),
        int((height_pt - top) * SCALE),
        int(right * SCALE),
        int((height_pt - bottom) * SCALE),
    )


def _darkness(jpeg: bytes, box: Box, height_pt: float) -> float:
    """Mean gray level inside the box: ~0 when masked, mostly white for printed text."""
    region = Image.open(io.BytesIO(jpeg)).convert("L").crop(_px(box, height_pt))
    return sum(region.tobytes()) / (region.width * region.height)


def test_text_indices_align_with_char_boxes() -> None:
    page = pdfium.PdfDocument(render_bill(_claim(), "table"))[0]
    tp = page.get_textpage()
    text = tp.get_text_range()
    assert len(text) == tp.count_chars()  # generated \r\n are counted as chars too
    assert all(tp.get_text_range(i, 1) == text[i] for i in range(len(text)))
    for needle in PHI:  # the boxes at a needle's indices hold exactly that text
        assert tp.get_text_bounded(*_find(tp, needle)).strip() == needle


def test_identifiers_are_found_and_masked() -> None:
    pdf = render_bill(_claim(), "table")
    page = pdfium.PdfDocument(pdf)[0]
    r = find_phi_boxes(page)
    assert r.redactable
    assert {"PERSON", "PHONE_NUMBER", "LOCATION", "MEMBER_ID"} <= set(r.entities)
    tp = page.get_textpage()
    height = page.get_size()[1]
    (original,), (masked,) = pdf_page_images(pdf), pdf_page_images(pdf, redact=True)
    for needle in PHI:
        box = _find(tp, needle)
        assert any(_covers(rb, box) for rb in r.boxes), needle
        assert _darkness(original, box, height) > 150  # printed text on white
        assert _darkness(masked, box, height) < 30, needle


@pytest.mark.parametrize("layout", LAYOUTS)
def test_billing_fields_are_never_masked(layout: str) -> None:
    claim = _claim()
    for page in pdfium.PdfDocument(render_bill(claim, layout)):
        r = find_phi_boxes(page)
        assert r.redactable and r.boxes
        tp = page.get_textpage()
        text = tp.get_text_range()
        fields = {"Claim / account number: EOB-1", "Total:"}
        for ln in claim.lines:
            fields |= {ln.code, f"${ln.charge:.2f}", ln.date_of_service.strftime("%m/%d/%Y")}
        # every printed row holding a billing field: code, modifiers, units, POS, charge, date, claim id
        rows = [row for row in text.split("\r\n") if any(f in row for f in fields)]
        assert len(rows) >= len(claim.lines)
        for row in rows:
            start = text.index(row)
            for box in _boxes_of(tp, start, start + len(row)):
                assert not any(_overlaps(box, rb) for rb in r.boxes), row


def test_a_label_with_no_value_does_not_reach_into_the_next_row() -> None:
    text = "Member ID:\r\n96372 1 11 $30.00\r\nPatient:\r\nQty 2 POS 11\r\nAddress:\r\n10/15/2026 99213\r\n"
    assert analyzer().analyze(text, language="en", entities=list(REDACT_ENTITIES)) == []


def test_scanned_page_is_not_redactable() -> None:
    noisy = add_scan_noise(render_bill(_claim(), "table"), seed=1)
    r = find_phi_boxes(pdfium.PdfDocument(noisy)[0])
    assert not r.redactable and r.boxes == [] and r.entities == {}
    images, redactions = pdf_page_images_with_redaction(noisy)
    assert images == pdf_page_images(noisy) and [x.redactable for x in redactions] == [False]


def test_rotated_page_is_not_redactable() -> None:
    doc = pdfium.PdfDocument(render_bill(_claim(), "table"))
    doc[0].set_rotation(90)
    buf = io.BytesIO()
    doc.save(buf)
    r = find_phi_boxes(pdfium.PdfDocument(buf.getvalue())[0])
    assert not r.redactable and r.boxes == []


def test_identifier_outside_the_page_is_not_redactable() -> None:
    pdf = FPDF(unit="mm", format="Letter")
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)
    pdf.text(20, 20, "Itemized statement for services rendered")
    pdf.text(230, 40, "Member ID: XQH4471029")  # Letter is 215.9 mm wide
    r = find_phi_boxes(pdfium.PdfDocument(bytes(pdf.output()))[0])
    assert not r.redactable and r.boxes == []


def test_mask_converts_pdf_points_to_pixels() -> None:
    img = Image.new("RGB", (200, 100), "white")  # a 100 x 50 pt page at scale 2
    out = mask(img, [(10, 30, 20, 40)], (100, 50), 2)
    assert out.getpixel((30, 30)) == (0, 0, 0)  # x 15 pt, y 50 - 35 = 15 pt from the top
    assert out.getpixel((18, 30)) == (0, 0, 0) and out.getpixel((17, 30)) == (255, 255, 255)  # 2 px padding
    assert out.getpixel((30, 60)) == (255, 255, 255) and img.getpixel((30, 30)) == (255, 255, 255)


class RecordingVision:
    def __init__(self) -> None:
        self.images: list[bytes] = []

    def extract(
        self, image_jpeg: bytes, schema: dict[str, Any], prompt: str, page_no: int | None = None
    ) -> Any:
        self.images.append(image_jpeg)
        return fv(_claim())


def test_model_sees_masked_page_and_reviewer_sees_original(db: Engine) -> None:
    pdf = render_bill(_claim(), "table")
    page = pdfium.PdfDocument(pdf)[0]
    name = _find(page.get_textpage(), NAME)
    height = page.get_size()[1]
    v = RecordingVision()
    c = client(v)  # type: ignore[arg-type]
    r = post_pdf(c, pdf)
    assert r.status_code == 201, r.text
    red = r.json()["redaction"]
    assert red["pages_redacted"] == 1 and red["pages_not_redactable"] == 0
    assert red["entities"]["PERSON"] >= 1 and red["entities"]["MEMBER_ID"] == 1
    assert _darkness(v.images[0], name, height) < 30
    stored = render_page(pdf, 1)
    case_id = r.json()["cases"][0]["id"]
    assert c.get(f"/api/cases/{case_id}/pages/1").content == stored
    assert _darkness(stored, name, height) > 150
    (event,) = [e for e in c.get("/api/audit-log").json() if e["action"] == "case_uploaded"]
    assert event["detail"]["redaction"] == red


def test_scanned_upload_reports_not_redactable(db: Engine) -> None:
    r = post_pdf(client(RecordingVision()), add_scan_noise(render_bill(_claim(), "table"), seed=1))  # type: ignore[arg-type]
    assert r.status_code == 201, r.text
    assert r.json()["redaction"] == {"pages_redacted": 0, "pages_not_redactable": 1, "entities": {}}


def test_fhir_routes_never_import_presidio(db: Engine) -> None:
    code = """
import sys
from fastapi.testclient import TestClient
from app.main import app
from tests.api_helpers import sample_claim, upload
r = upload(TestClient(app), [sample_claim()])
assert r.status_code == 201, r.text
assert r.json()["redaction"] is None
loaded = [m for m in sys.modules if m.split(".")[0] in ("presidio_analyzer", "spacy")]
assert not loaded, loaded
"""
    subprocess.run([sys.executable, "-c", code], check=True)
