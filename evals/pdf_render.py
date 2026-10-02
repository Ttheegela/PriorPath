"""Render synthetic itemized-bill PDFs (three layouts) and degrade them with scan noise."""

import io
import random
from datetime import UTC, datetime

import pypdfium2 as pdfium
from fpdf import FPDF
from PIL import Image, ImageChops

from app.models import Claim, LineItem

LAYOUTS = ("table", "statement", "compact")


class _BillPdf(FPDF):
    """Repeats the active table's column header at the top of every continuation page."""

    col_header: tuple[list[str], list[int], int] | None = None

    def header(self) -> None:
        if self.col_header:
            self.draw_col_header()

    def draw_col_header(self) -> None:
        assert self.col_header
        names, widths, size = self.col_header
        h = size * 0.5 + 2
        self.set_font("Helvetica", "B", size)
        for name, w in zip(names, widths, strict=True):
            self.cell(w, h, name, border=1)
        self.ln(h)


def _new_pdf() -> _BillPdf:
    pdf = _BillPdf(unit="mm", format="Letter")
    pdf.set_creation_date(datetime(2026, 1, 1, tzinfo=UTC))
    pdf.set_title("Itemized statement")
    pdf.set_author("PriorPath synthetic bills")
    pdf.set_creator("evals.pdf_render")
    pdf.set_producer("fpdf2")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    return pdf


def _date(line: LineItem) -> str:
    return line.date_of_service.strftime("%m/%d/%Y")


def _mods(line: LineItem) -> str:
    return ",".join(line.modifiers) or "-"


def _header(pdf: FPDF, claim: Claim, patient_name: str) -> None:
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, claim.provider or "", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    for text in (
        "Itemized statement",
        f"Payer: {claim.payer or ''}",
        f"Claim / account number: {claim.id}",
        f"Patient: {patient_name}",
    ):
        pdf.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)


def _table(pdf: _BillPdf, header: list[str], rows: list[list[str]], widths: list[int], size: int) -> None:
    h = size * 0.5 + 2
    pdf.col_header = (header, widths, size)
    pdf.draw_col_header()
    pdf.set_font("Helvetica", "", size)
    for row in rows:
        for text, w in zip(row, widths, strict=True):
            pdf.cell(w, h, text, border=1)
        pdf.ln(h)


def render_bill(claim: Claim, layout: str, patient_name: str = "Alex Example") -> bytes:
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}")
    pdf = _new_pdf()
    _header(pdf, claim, patient_name)
    total = f"Total: ${sum(ln.charge for ln in claim.lines):.2f}"
    if layout == "table":
        rows = [[_date(ln), ln.code, _mods(ln), str(ln.units), f"${ln.charge:.2f}"] for ln in claim.lines]
        _table(pdf, ["Date", "Code", "Modifiers", "Units", "Charge"], rows, [35, 35, 35, 25, 35], 11)
    elif layout == "statement":
        pdf.set_font("Helvetica", "", 11)
        for ln in claim.lines:
            code = ln.code + (f"-{'-'.join(ln.modifiers)}" if ln.modifiers else "")
            if pdf.will_page_break(14):  # keep a line's two rows on one page
                pdf.add_page()  # (pdf.unbreakable() drops the block in fpdf2 2.8.9)
            pdf.cell(0, 6, f"{_date(ln)}  Service {code}", new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 6, f"Qty {ln.units}   ${ln.charge:.2f}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
    else:
        rows = [[ln.code, _date(ln), str(ln.units), _mods(ln), f"${ln.charge:.2f}"] for ln in claim.lines]
        _table(pdf, ["Code", "Date", "Units", "Mod", "Amount"], rows, [25, 30, 15, 20, 25], 8)
    pdf.col_header = None
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, total, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def add_scan_noise(pdf: bytes, seed: int) -> bytes:
    """Rasterize at 150 dpi, skew slightly, add noise; return an image-only PDF."""
    rng = random.Random(seed)
    doc = pdfium.PdfDocument(pdf)
    out = FPDF(unit="pt")
    out.set_creation_date(datetime(2026, 1, 1, tzinfo=UTC))
    out.set_auto_page_break(False)
    for i in range(len(doc)):
        img = doc[i].render(scale=150 / 72).to_pil().convert("L")
        img = img.rotate(rng.uniform(-1.5, 1.5), expand=True, fillcolor=255)
        # Image.effect_noise is unseeded, so build seeded noise: the mean of two uniform
        # fields is triangular, close enough to gaussian for scan grain. Generated at 1/3
        # resolution and upscaled so the grain is blotchy and the JPEG stays small.
        small = (img.width // 3 + 1, img.height // 3 + 1)
        n = small[0] * small[1]
        a = Image.frombytes("L", small, rng.randbytes(n))
        b = Image.frombytes("L", small, rng.randbytes(n))
        noise = ImageChops.add(a, b, scale=2).resize(img.size, Image.Resampling.NEAREST)
        img = Image.blend(img, noise, 0.1)
        w_pt, h_pt = img.width * 72 / 150, img.height * 72 / 150
        out.add_page(format=(w_pt, h_pt))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        out.image(buf, x=0, y=0, w=w_pt, h=h_pt)
    return bytes(out.output())
