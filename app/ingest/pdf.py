import io

import pypdfium2 as pdfium

MAX_PAGES = 10
RENDER_DPI = 150
MAX_PIXELS = 20_000_000


class PdfError(ValueError):
    pass


UNREADABLE = "This PDF could not be read"


def pdf_page_images(data: bytes) -> list[bytes]:
    if not data.startswith(b"%PDF-"):
        raise PdfError("file is not a PDF")
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise PdfError("PDF is password protected; upload an unencrypted copy") from exc
        raise PdfError(UNREADABLE) from exc
    try:
        n = len(doc)
        if n == 0:
            raise PdfError("PDF has no pages")
        if n > MAX_PAGES:
            raise PdfError(f"PDF has {n} pages; at most {MAX_PAGES} pages are allowed")
        scale = RENDER_DPI / 72
        for i in range(n):  # check every page size before rendering any
            page = doc[i]
            try:
                w, h = page.get_size()
            finally:
                page.close()
            if w * scale * h * scale > MAX_PIXELS:
                raise PdfError(f"page {i + 1} is too large (over 20 megapixels at {RENDER_DPI} DPI)")
        images: list[bytes] = []
        for i in range(n):
            page = doc[i]
            try:
                buf = io.BytesIO()
                page.render(scale=scale).to_pil().save(buf, format="PNG", optimize=True)
                images.append(buf.getvalue())
            finally:
                page.close()
        return images
    except pdfium.PdfiumError as exc:
        raise PdfError(UNREADABLE) from exc
    finally:
        doc.close()
