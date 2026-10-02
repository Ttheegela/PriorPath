import io
import threading

import pypdfium2 as pdfium

from app.ingest.redact import PageRedaction, analyzer, find_phi_boxes, mask

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


def _encode(page: pdfium.PdfPage, scale: float, redaction: PageRedaction | None = None) -> bytes:
    # Annotation and form-field appearances aren't in the text layer redaction reads, so model-bound
    # renders (redaction is not None) leave them out; the reviewer's original keeps them.
    bitmap = page.render(scale=scale, draw_annots=redaction is None)
    try:
        img = bitmap.to_pil()
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")
        if redaction and redaction.boxes:
            img = mask(img, redaction.boxes, page.get_size(), scale)
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


def pdf_page_images(data: bytes, *, redact: bool = False) -> list[bytes]:
    """Every page as JPEG, in order; with redact, identifiers on text-layer pages are blacked out."""
    return _pages(data, redact)[0]


def pdf_page_images_with_redaction(data: bytes) -> tuple[list[bytes], list[PageRedaction]]:
    """Model-bound page images, masked where the page has a text layer, plus what was done per page."""
    return _pages(data, True)


def _pages(data: bytes, redact: bool) -> tuple[list[bytes], list[PageRedaction]]:
    if redact:
        analyzer()  # load spaCy before taking the pdfium lock, not while holding it
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
            redactions: list[PageRedaction] = []
            for i in range(n):
                page = doc[i]
                try:
                    # ponytail: Presidio runs under the pdfium lock (~15 ms/page warm); split text
                    # extraction from analysis if concurrent PDF uploads start queueing on it
                    r = find_phi_boxes(page) if redact else None
                    images.append(_encode(page, scale, r))
                    if r is not None:
                        redactions.append(r)
                finally:
                    page.close()
            return images, redactions
        except pdfium.PdfiumError as exc:
            raise PdfError(UNREADABLE) from exc
        finally:
            doc.close()
