from concurrent.futures import ThreadPoolExecutor

import pytest

from app.ingest.pdf import MAX_PAGES, PdfError, page_count, pdf_page_images, render_page
from evals.pdf_render import render_bill
from tests.api_helpers import sample_claim


def test_renders_one_deterministic_jpeg_per_page() -> None:
    pdf = render_bill(sample_claim(), "table")
    pages = pdf_page_images(pdf)
    assert len(pages) >= 1 and all(p.startswith(b"\xff\xd8\xff") for p in pages)
    assert pdf_page_images(pdf) == pages and render_page(pdf, 1) == pages[0]


def test_concurrent_renders_do_not_crash() -> None:
    """pdfium is not thread-safe; without the module lock this aborts the process."""
    pdf = render_bill(sample_claim(), "table")
    expected = render_page(pdf, 1)

    def work(i: int) -> bytes | int:
        return render_page(pdf, 1) if i % 2 else page_count(pdf)

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(work, range(32)))
    assert results[1::2] == [expected] * 16 and set(results[0::2]) == {1}


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


def test_rejects_oversized_page_without_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    import pypdfium2 as pdfium
    from fpdf import FPDF

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("rendered")

    monkeypatch.setattr(pdfium.PdfPage, "render", boom)
    pdf = FPDF(format=(3000, 3000))
    pdf.add_page()
    with pytest.raises(PdfError, match="too large"):
        pdf_page_images(bytes(pdf.output()))


def test_rejects_zero_page_pdf() -> None:
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    )
    with pytest.raises(PdfError, match="no pages|could not be read"):
        pdf_page_images(pdf)


def test_corrupt_page_tree_is_a_pdf_error() -> None:
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 a b]/Contents 99 0 R>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF"
    )
    try:
        pages = pdf_page_images(pdf)
    except PdfError:
        return
    assert pages  # pdfium repaired it; the point is no raw PdfiumError escapes
