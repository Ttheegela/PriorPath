import io

import pypdfium2 as pdfium

MAX_PAGES = 10
RENDER_DPI = 150
MAX_PIXELS = 20_000_000


class PdfError(ValueError):
    pass


def pdf_page_images(data: bytes) -> list[bytes]:
    if not data.startswith(b"%PDF-"):
        raise PdfError("file is not a PDF")
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise PdfError("PDF is password protected; upload an unencrypted copy") from exc
        raise PdfError("PDF could not be read") from exc
    try:
        n = len(doc)
        if n == 0:
            raise PdfError("PDF has no pages")
        if n > MAX_PAGES:
            raise PdfError(f"PDF has {n} pages; at most {MAX_PAGES} pages are allowed")
        images: list[bytes] = []
        for i in range(n):
            page = doc[i]
            try:
                w, h = page.get_size()
                scale = RENDER_DPI / 72
                if w * scale * h * scale > MAX_PIXELS:
                    raise PdfError(f"page {i + 1} is too large (over 20 megapixels at {RENDER_DPI} DPI)")
                buf = io.BytesIO()
                page.render(scale=scale).to_pil().save(buf, format="PNG", optimize=True)
                images.append(buf.getvalue())
            except pdfium.PdfiumError as exc:
                raise PdfError(f"page {i + 1} could not be read") from exc
            finally:
                page.close()
        return images
    finally:
        doc.close()
