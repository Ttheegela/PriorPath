import pytest

from app.ingest.pdf import MAX_PAGES, PdfError, pdf_page_images
from evals.pdf_render import render_bill
from tests.api_helpers import sample_claim


def test_renders_one_png_per_page() -> None:
    pages = pdf_page_images(render_bill(sample_claim(), "table"))
    assert len(pages) >= 1 and all(p.startswith(b"\x89PNG") for p in pages)


@pytest.mark.parametrize(
    ("data", "needle"),
    [(b"not a pdf at all", "not a PDF"), (b"%PDF-1.4 garbage", "could not be read")],
)
def test_rejects_non_pdfs_with_a_readable_message(data: bytes, needle: str) -> None:
    with pytest.raises(PdfError, match=needle):
        pdf_page_images(data)


def test_rejects_too_many_pages() -> None:
    from fpdf import FPDF

    pdf = FPDF()
    for _ in range(MAX_PAGES + 1):
        pdf.add_page()
    with pytest.raises(PdfError, match="at most 10 pages"):
        pdf_page_images(bytes(pdf.output()))


def test_rejects_encrypted_pdfs() -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_encryption(owner_password="o", user_password="u")
    pdf.add_page()
    with pytest.raises(PdfError, match="password"):
        pdf_page_images(bytes(pdf.output()))
