import io
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytest
from fpdf import FPDF
from PIL import Image
from sqlalchemy import Engine

from app.ingest.pdf import RENDER_DPI, pdf_page_images, pdf_page_images_with_redaction, render_page
from app.ingest.redact import find_phi_boxes, mask, matches
from app.models import Claim
from app.reference.normalized import load_normalized
from evals import pdf_render
from evals.generate import generate
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


def _misses(pdf: bytes, fields: set[str]) -> tuple[list[str], list[str]]:
    """Billing rows (holding any of `fields`) touched by a mask, and header identifiers left visible."""
    out, unmasked = [], []
    for page in pdfium.PdfDocument(pdf):
        r = find_phi_boxes(page)
        assert r.redactable
        tp = page.get_textpage()
        text = tp.get_text_range()
        unmasked += [p for p in PHI if p in text and not any(_covers(rb, _find(tp, p)) for rb in r.boxes)]
        start = 0
        for row in text.split("\r\n"):
            boxes = _boxes_of(tp, start, start + len(row))
            start += len(row) + 2
            if any(f in row for f in fields) and any(_overlaps(b, rb) for b in boxes for rb in r.boxes):
                out.append(row)
    return out, unmasked


def _billing_fields(claim: Claim) -> set[str]:
    """Code, charge and date of every line (rows holding them also hold modifiers, units and POS)."""
    fields = {claim.id, "Total:"}
    for ln in claim.lines:
        fields |= {ln.code, f"${ln.charge:.2f}", ln.date_of_service.strftime("%m/%d/%Y")}
    return fields


@pytest.mark.parametrize("layout", LAYOUTS)
def test_billing_rows_are_not_masked(layout: str) -> None:
    claim = _claim()
    assert _misses(render_bill(claim, layout), _billing_fields(claim)) == ([], [])


@pytest.mark.parametrize("layout", LAYOUTS)
def test_billing_rows_are_not_masked_on_generated_bills(layout: str) -> None:
    # spaCy's small model once tagged dates, codes and claim numbers as names/places on these bills
    masked, unmasked = [], []
    for lc in generate(load_normalized(Path("data/reference/subset")), 50, 7):
        rows, phi = _misses(render_bill(lc.claim, layout), _billing_fields(lc.claim))
        masked += rows
        unmasked += phi
    assert (masked, unmasked) == ([], [])


@pytest.mark.parametrize("layout", LAYOUTS)
def test_generated_bills_in_courier(layout: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # A monospaced font has much wider spaces; column detection must scale with them (test-only font swap).
    monkeypatch.setattr(
        pdf_render._BillPdf,
        "set_font",
        lambda self, _family, *a, **k: FPDF.set_font(self, "Courier", *a, **k),
    )
    masked, unmasked = [], []
    for lc in generate(load_normalized(Path("data/reference/subset")), 20, 23):
        pdf = render_bill(lc.claim, layout)
        assert "Courier" in str(pdf)
        rows, phi = _misses(pdf, _billing_fields(lc.claim))
        masked += rows
        unmasked += phi
    assert (masked, unmasked) == ([], [])


# One printed row of cells: (width in mm, 0 = rest of the line, or "fit+N" = the text's own width plus N mm;
# text; optional font, else the page's).
Cells = list[tuple[Any, ...]]


def _render(rows: list[Cells], font: str = "Helvetica") -> bytes:
    pdf = FPDF(unit="mm", format="Letter")
    pdf.add_page()
    pdf.set_font(font, "", 11)
    pdf.cell(0, 6, "Itemized statement for services rendered", new_x="LMARGIN", new_y="NEXT")
    for row in rows:
        for width, text, *cell_font in row:
            pdf.set_font(cell_font[0] if cell_font else font, "", 11)
            if isinstance(width, str):  # text fills the cell: only fpdf's 1 mm margins either side apart
                width = pdf.get_string_width(text) + 2 * pdf.c_margin + float(width.removeprefix("fit+"))
            pdf.cell(width, 6, text)
        pdf.ln(6)
    return bytes(pdf.output())


@pytest.mark.parametrize(
    ("font", "row", "needle"),
    [
        ("Courier", "Patient: Maria Garcia", "Maria Garcia"),
        (
            "Courier",
            "Address: 1200 Maple Avenue, Springfield, IL 62704",
            "1200 Maple Avenue, Springfield, IL 62704",
        ),
        ("Courier", "Phone: (217) 555-0143", "(217) 555-0143"),
        ("Courier", "Member ID: XQH4471029", "XQH4471029"),
        ("Courier", "Patient: Maria  Garcia", "Maria Garcia"),
        # pdfium reports runs of spaces as one; the needles are as pdfium prints them
        (
            "Helvetica",
            "Address: 1200 Maple Avenue, Springfield, IL  62704",
            "1200 Maple Avenue, Springfield, IL 62704",
        ),
        ("Helvetica", "Patient: Maria  Garcia", "Maria Garcia"),
        ("Helvetica", "Patient: Maria   Garcia", "Maria Garcia"),  # one in-cell gap: the value carries on
    ],
)
def test_whole_labelled_values_are_masked(font: str, row: str, needle: str) -> None:
    page = pdfium.PdfDocument(_render([[(0, row)]], font))[0]
    r = find_phi_boxes(page)
    assert any(_covers(rb, _find(page.get_textpage(), needle)) for rb in r.boxes)


@pytest.mark.parametrize(
    ("rows", "masked", "kept"),
    [
        (
            [[(0, "10/15/2026 J1100 - 10 11 $12.00")], [(0, "Patient: Alex Example   J1100 1 11 $30.00")]],
            [NAME],
            ["10/15/2026 J1100 - 10 11 $12.00", "J1100 1 11 $30.00"],
        ),
        ([[(0, "Patient: Jordan Lee   G0008 1 11 $30.00")]], ["Jordan Lee"], ["G0008 1 11 $30.00"]),
        ([[(0, "Patient: Maria Garcia 2")]], ["Maria Garcia"], ["2"]),
        ([[(0, "Guarantor: Jordan Lee  Claim C0095")]], ["Jordan Lee"], ["C0095"]),
        ([[(60, "Guarantor: Jordan Lee"), (0, "Claim C0095")]], ["Jordan Lee"], ["Claim C0095"]),
        (  # pdfium joins the two cells with a single space; the column gap still ends the address
            [
                [
                    (110, "Address: 1200 Maple Avenue, Springfield, IL 62704"),
                    (0, "Date of service 10/15/2026 99213 $40.00"),
                ]
            ],
            ["1200 Maple Avenue, Springfield, IL 62704"],
            ["Date of service 10/15/2026 99213 $40.00"],
        ),
        ([[(0, "Phone: (217) 555-0143   Date of birth: 01/02/1980")]], ["(217) 555-0143"], ["01/02/1980"]),
        (  # carried across an in-cell gap, but never into a billing token
            [[(0, "Address: 1200 Maple Avenue   10/15/2026 99213 $40.00")]],
            ["1200 Maple Avenue"],
            ["10/15/2026 99213 $40.00"],
        ),
    ],
)
def test_matches_stay_in_their_column(rows: list[Cells], masked: list[str], kept: list[str]) -> None:
    _assert_masked(_render(rows), masked, kept)


ADDRESS = "Address: 1200 Maple Avenue, Springfield, IL 62704"
BILLING = "10/15/2026 99213 $40.00"


@pytest.mark.parametrize(
    ("font", "row", "masked", "kept"),
    [
        *[
            (font, [("fit+0", ADDRESS), (0, BILLING)], [PHI[1]], [BILLING])
            for font in ("Helvetica", "Times", "Courier")
        ],
        *[
            (
                font,
                [("fit+0", "Phone: (217) 555-0143"), (0, "Date of birth: 01/02/1980")],
                [PHI[2]],
                ["01/02/1980"],
            )
            for font in ("Helvetica", "Times", "Courier")
        ],
        *[  # an unlabelled date right after the phone once made Presidio's phone parser drop the number
            (font, [("fit+0", "Phone: (217) 555-0143"), (0, "01/02/1980")], [PHI[2]], ["01/02/1980"])
            for font in ("Helvetica", "Times", "Courier")
        ],
        *[("Courier", [(f"fit+{mm}", ADDRESS), (0, BILLING)], [PHI[1]], [BILLING]) for mm in (1, 2.5, 4.9)],
        ("Helvetica", [("fit+3", ADDRESS, "Courier"), (0, BILLING)], [PHI[1]], [BILLING]),
        ("Helvetica", [("fit+0", ADDRESS), (0, BILLING, "Courier")], [PHI[1]], [BILLING]),
        (
            "Helvetica",
            [("fit+0", "Patient: Maria Garcia"), (0, "J1100 1 11 $30.00")],
            ["Maria Garcia"],
            ["J1100"],
        ),
    ],
)
def test_tight_table_cells_are_separate_columns(
    font: str, row: Cells, masked: list[str], kept: list[str]
) -> None:
    # Cells whose text fills their width sit only ~5.7 pt apart, under the space-width rule; pdfium marks the
    # boundary with a generated separator.
    _assert_masked(_render([row], font), masked, kept)


@pytest.mark.parametrize(
    ("font", "gap", "header"),
    [(font, gap, True) for font in ("Helvetica", "Times", "Courier") for gap in (1.05, 1.2)]
    + [("Courier", 1.05, False), ("Courier", 1.2, False)],
)
def test_text_drawn_word_by_word_is_masked(font: str, gap: float, header: bool) -> None:
    # Every word gap is then a pdfium-generated char about one space wide; it mustn't split names or addresses
    # into per-word columns, whether or not some other line on the page has real spaces.
    pdf = FPDF(unit="pt", format="Letter")
    pdf.add_page()
    pdf.set_font(font, "", 11)
    if header:
        pdf.cell(0, 16, "Itemized statement for services rendered", new_x="LMARGIN", new_y="NEXT")
    space, y = pdf.get_string_width(" "), 120
    for row in ("Patient: Maria Garcia", f"Address: {PHI[1]}", f"Phone: {PHI[2]}", "Statement for services"):
        x = 72.0
        for word in row.split(" "):
            pdf.text(x, y, word)
            x += pdf.get_string_width(word) + space * gap
        y += 16
    _assert_masked(bytes(pdf.output()), ["Maria Garcia", PHI[1], PHI[2]], [])


@pytest.mark.parametrize("date", ["2026-10-15", "Oct 15, 2026", "15 October 2026"])
def test_labelled_carry_over_stops_at_any_date(date: str) -> None:
    _assert_masked(
        _render([[(0, f"Address: 1200 Maple Avenue   {date} 99213 $40.00")]]),
        ["1200 Maple Avenue"],
        [f"{date} 99213 $40.00"],
    )


def _assert_masked(pdf: bytes, masked: list[str], kept: list[str]) -> None:
    page = pdfium.PdfDocument(pdf)[0]
    r = find_phi_boxes(page)
    tp = page.get_textpage()
    text = tp.get_text_range()
    for needle in masked:
        assert any(_covers(rb, _find(tp, needle)) for rb in r.boxes), needle
    for needle in kept:
        i = text.rindex(needle)
        assert not any(_overlaps(b, rb) for b in _boxes_of(tp, i, i + len(needle)) for rb in r.boxes), needle


def _found(text: str) -> list[tuple[str, str]]:
    return [(m.entity_type, text[m.start : m.end]) for m in matches(text)]


def test_a_label_with_no_value_does_not_reach_into_the_next_row() -> None:
    assert (
        _found(
            "Member ID:\r\n96372 1 11 $30.00\r\nPatient:\r\nQty 2 POS 11\r\nAddress:\r\n10/15/2026 99213\r\n"
        )
        == []
    )


def test_digit_runs_are_not_phone_numbers() -> None:
    assert _found("99213 25 1 11 $150.00\r\nNPI 1234567893\r\nAccount 412345678\r\n") == []
    assert _found("Billing office phone 217-555-0143") == [("PHONE_NUMBER", "217-555-0143")]
    # a date or amount elsewhere in the column no longer hides the phone
    assert _found("Phone: (217) 555-0143 Date of birth: 01/02/1980") == [("PHONE_NUMBER", "(217) 555-0143")]
    assert _found("Phone (217) 555-0143 Balance due $40.00") == [("PHONE_NUMBER", "(217) 555-0143")]


def test_member_ids() -> None:
    assert _found("MRN: 1234567\r\nMRN 7654321\r\n") == [("MEMBER_ID", "1234567"), ("MEMBER_ID", "7654321")]
    assert _found("Member ID: ABCDEFGHIJKLMNOPQRSTUVWXYZ\r\n") == []  # too long: no half mask


def test_email_detection_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*a: Any, **k: Any) -> None:
        raise AssertionError("network call")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert _found("Questions: billing.help@example-clinic.org") == [
        ("EMAIL_ADDRESS", "billing.help@example-clinic.org")
    ]


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


def test_identifier_running_off_the_page_is_masked_to_the_edge() -> None:
    pdf = FPDF(unit="mm", format="Letter")
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)
    pdf.text(20, 20, "Itemized statement for services rendered")
    pdf.text(190, 40, "Member ID: XQH4471029")  # Letter is 215.9 mm wide: the value runs off the edge
    page = pdfium.PdfDocument(bytes(pdf.output()))[0]
    r = find_phi_boxes(page)
    assert r.redactable and r.clipped and r.entities == {"MEMBER_ID": 1}
    ((left, _, right, _),) = r.boxes
    assert left < page.get_size()[0] == right


def test_annotation_text_is_not_sent_to_the_model_but_reviewer_sees_it() -> None:
    pdf = FPDF(unit="pt", format="Letter")
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)
    pdf.text(72, 72, "Itemized statement for services rendered")
    pdf.free_text_annotation("Patient: Jane Q Doe  Member ID: XQH4471029", x=72, y=200, w=300, h=30)
    data = bytes(pdf.output())
    (model,), _ = pdf_page_images_with_redaction(data)
    box = (int(72 * SCALE), int(200 * SCALE), int(372 * SCALE), int(230 * SCALE))  # fpdf y is from the top

    def darkest(jpeg: bytes) -> int:
        return min(Image.open(io.BytesIO(jpeg)).convert("L").crop(box).tobytes())

    assert darkest(model) > 200  # annotation not drawn: no dark text pixels
    assert darkest(render_page(data, 1)) < 50  # the /pages original still shows it


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
    assert red["pages_partially_redacted"] == 0
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
    assert r.json()["redaction"] == {
        "pages_redacted": 0,
        "pages_not_redactable": 1,
        "pages_partially_redacted": 0,
        "entities": {},
    }


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
