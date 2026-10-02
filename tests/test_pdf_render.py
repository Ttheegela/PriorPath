import pypdfium2 as pdfium
import pytest

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
    assert claim.provider and claim.provider in text


def test_rendering_is_deterministic() -> None:
    assert render_bill(sample_claim(), "table") == render_bill(sample_claim(), "table")


def test_scan_noise_produces_an_image_only_pdf_of_same_page_count() -> None:
    pdf = render_bill(sample_claim(), "statement")
    noisy = add_scan_noise(pdf, seed=3)
    assert len(pdfium.PdfDocument(noisy)) == len(pdfium.PdfDocument(pdf))
    assert _text(noisy).strip() == ""  # no text layer: the model must read pixels
    assert add_scan_noise(pdf, seed=3) == noisy
