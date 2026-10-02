import io
import threading

import pypdfium2 as pdfium

MAX_PAGES = 10
RENDER_DPI = 150
MAX_PIXELS = 20_000_000
JPEG_QUALITY = 85
# ponytail: global pdfium lock, process isolation if throughput matters
_PDFIUM_LOCK = threading.Lock()  # pdfium is not thread-safe; every open/render/close holds this


class PdfError(ValueError):
    pass


UNREADABLE = "This PDF could not be read"


def _open(data: bytes) -> pdfium.PdfDocument:
    if not data.startswith(b"%PDF-"):
        raise PdfError("file is not a PDF")
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise PdfError("PDF is password protected; upload an unencrypted copy") from exc
        raise PdfError(UNREADABLE) from exc
    n = len(doc)
    if n == 0 or n > MAX_PAGES:
        doc.close()
        raise PdfError(
            "PDF has no pages" if n == 0 else f"PDF has {n} pages; at most {MAX_PAGES} pages are allowed"
        )
    return doc


def page_count(data: bytes) -> int:
    with _PDFIUM_LOCK:
        doc = _open(data)
        try:
            return len(doc)
        finally:
            doc.close()


def render_page(data: bytes, page_no: int) -> bytes:
    """Render one 1-based page as JPEG; same limits as pdf_page_images, size for this page only."""
    with _PDFIUM_LOCK:
        doc = _open(data)
        try:
            if not 1 <= page_no <= len(doc):
                raise PdfError(f"page {page_no} does not exist")
            return _render(doc, page_no - 1)
        except pdfium.PdfiumError as exc:
            raise PdfError(UNREADABLE) from exc
        finally:
            doc.close()


def _encode(page: pdfium.PdfPage, scale: float) -> bytes:
    bitmap = page.render(scale=scale)
    try:
        img = bitmap.to_pil()
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        return buf.getvalue()
    finally:
        bitmap.close()  # explicitly, under the lock, not later by the garbage collector


def _render(doc: pdfium.PdfDocument, i: int) -> bytes:
    scale = RENDER_DPI / 72
    page = doc[i]
    try:
        w, h = page.get_size()
        if w * scale * h * scale > MAX_PIXELS:
            raise PdfError(f"page {i + 1} is too large (over 20 megapixels at {RENDER_DPI} DPI)")
        return _encode(page, scale)
    finally:
        page.close()


def pdf_page_images(data: bytes) -> list[bytes]:
    """Every page as JPEG, in order."""
    with _PDFIUM_LOCK:
        doc = _open(data)
        try:
            n = len(doc)
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
                    images.append(_encode(page, scale))
                finally:
                    page.close()
            return images
        except pdfium.PdfiumError as exc:
            raise PdfError(UNREADABLE) from exc
        finally:
            doc.close()
