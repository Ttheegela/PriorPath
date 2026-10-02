from datetime import date
from decimal import Decimal

import pypdfium2 as pdfium
import pytest

from app.models import Claim, LineItem
from evals.pdf_render import LAYOUTS, add_scan_noise, render_bill
from tests.api_helpers import sample_claim


def _text(pdf: bytes) -> str:
    doc = pdfium.PdfDocument(pdf)
    return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))


@pytest.mark.parametrize("layout", LAYOUTS)
def test_each_layout_shows_every_line(layout: str) -> None:
    claim = sample_claim()
    text = _text(render_bill(claim, layout))
    for line in claim.lines:
        assert line.code in text
        assert f"{line.charge:.2f}" in text
        assert line.place_of_service and line.place_of_service in text
    assert claim.provider and claim.provider in text


def test_rendering_is_deterministic() -> None:
    assert render_bill(sample_claim(), "table") == render_bill(sample_claim(), "table")


def test_scan_noise_produces_an_image_only_pdf_of_same_page_count() -> None:
    pdf = render_bill(sample_claim(), "statement")
    noisy = add_scan_noise(pdf, seed=3)
    assert len(pdfium.PdfDocument(noisy)) == len(pdfium.PdfDocument(pdf))
    assert _text(noisy).strip() == ""  # no text layer: the model must read pixels
    assert add_scan_noise(pdf, seed=3) == noisy


def _big_claim(n: int) -> Claim:
    lines = [
        LineItem(
            id=f"L{i}",
            code=f"{90000 + i}",
            units=2 + i % 3,
            charge=Decimal("12.50") + i,
            date_of_service=date(2026, 3, 1 + i % 28),
            modifiers=["59", "XS"] if i % 2 else [],
        )
        for i in range(n)
    ]
    return Claim(id="EOB-BIG", patient_pseudonym="P", provider="Big Clinic", payer="Big Plan", lines=lines)


_HEADERS = {"table": "Modifiers", "compact": "Amount"}


@pytest.mark.parametrize("layout", LAYOUTS)
def test_place_of_service_is_printed_on_every_line(layout: str) -> None:
    claim = sample_claim()
    claim.lines[0].place_of_service = "22"
    text = _text(render_bill(claim, layout))
    assert "22" in text
    assert ("POS 22" in text) == (layout == "statement")
    assert "POS" in text


@pytest.mark.parametrize("layout", LAYOUTS)
def test_long_bill_paginates_cleanly(layout: str) -> None:
    claim = _big_claim(45)
    pdf = render_bill(claim, layout)
    doc = pdfium.PdfDocument(pdf)
    assert len(doc) > 1
    pages = [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    text = "\n".join(pages)
    for ln in claim.lines:
        assert text.count(ln.code) == 1
        assert ln.date_of_service.strftime("%m/%d/%Y") in text
        assert f"{ln.charge:.2f}" in text
        for m in ln.modifiers:
            assert m in text
    if layout in _HEADERS:
        assert all(_HEADERS[layout] in p for p in pages)
    else:  # statement: a line's code row and its Qty row share a page
        for p in pages:
            assert p.count("Service") == p.count("Qty")


@pytest.mark.parametrize("layout", LAYOUTS)
def test_determinism_clean_and_noisy(layout: str) -> None:
    claim = sample_claim()
    pdf = render_bill(claim, layout)
    assert pdf == render_bill(claim, layout)
    assert add_scan_noise(pdf, seed=5) == add_scan_noise(pdf, seed=5)


def test_noisy_pdfs_stay_under_upload_cap() -> None:
    forty = add_scan_noise(render_bill(_big_claim(40), "table"), seed=1)
    assert len(forty) < 1_500_000
    ten_pages = render_bill(_big_claim(170), "statement")
    assert len(pdfium.PdfDocument(ten_pages)) >= 10
    assert len(add_scan_noise(ten_pages, seed=1)) < 4_000_000
