"""Best-effort masking of patient identifiers on text-layer PDF pages, before page images reach the model.

Presidio and spaCy are imported on first use only, so routes that never redact don't pay for loading them.
Callers hold the pdfium lock around find_phi_boxes (it reads the page's text layer).
"""

import bisect
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

# DATE_TIME is deliberately absent: dates of service are billing data.
REDACT_ENTITIES = ("PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN", "LOCATION", "MEMBER_ID")
_PRESIDIO_ENTITIES = ["PERSON", "PHONE_NUMBER", "US_SSN", "LOCATION"]  # the rest are plain regexes below
MIN_TEXT_CHARS = 20  # fewer non-space chars than this: treat the page as a scan
PAD_PX = 2
SCORE_THRESHOLD = 0.5
# A gap between two characters wider than this share of the line height starts a new column. One space is
# ~0.25 of the (loose) char box height, two are ~0.5; table cells leave far more. Tuned by tests.
COLUMN_GAP = 0.4

# Labelled values ("v" group), matched outside Presidio so a wider model span can't swallow them; each value
# is then cut to its column. [ \t], not \s, so a value never starts on the next printed row.
_LABELLED = [
    (
        "MEMBER_ID",
        r"(?i)\b(?:(?:member|subscriber|policy|patient)[ \t]*(?:id|#|no\.?|number)"
        r"|mrn(?:[ \t]*(?:id|#|no\.?|number))?)[ \t]*[:#]?[ \t]*"
        r"(?P<v>[A-Z0-9-]{5,20})(?![A-Z0-9-])",  # longer values: no match rather than half-masked
    ),
    # The small spaCy model misses many names and street lines, so also take the value after the label.
    (
        "PERSON",
        r"(?i)\b(?:patient|guarantor|subscriber|insured)(?:[ \t]+name)?[ \t]*:[ \t]*"
        r"(?P<v>[A-Za-z][A-Za-z.'-]*(?: [A-Za-z][A-Za-z.'-]*)*)(?!\w)",
    ),
    ("LOCATION", r"(?i)\baddress[ \t]*:[ \t]*(?P<v>\S+(?: \S+)*)"),
    # Plain regex instead of Presidio's EmailRecognizer, which fetches the Public Suffix List over HTTP.
    ("EMAIL_ADDRESS", r"(?P<v>\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b)"),
]
_LABELLED_RX = [(entity, re.compile(rx)) for entity, rx in _LABELLED]
_DATE_OR_AMOUNT = re.compile(r"\$|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_DIGIT_WORD = re.compile(r"\S*\d")

_UNMAPPED_SPACY_LABELS = ["CARDINAL", "EVENT", "FAC", "LANGUAGE", "LAW", "MONEY", "ORDINAL", "PERCENT"]
_UNMAPPED_SPACY_LABELS += ["PRODUCT", "QUANTITY", "WORK_OF_ART"]

Box = tuple[float, float, float, float]  # left, bottom, right, top in PDF points, origin bottom-left


class Match(NamedTuple):
    entity_type: str
    start: int
    end: int


@dataclass
class PageRedaction:
    redactable: bool
    boxes: list[Box] = field(default_factory=list)
    entities: dict[str, int] = field(default_factory=dict)
    clipped: bool = False  # some match reached past the page edge; masked up to the edge only


_analyzer: Any = None
_analyzer_lock = threading.Lock()


def analyzer() -> Any:
    """The Presidio engine, built once, on the spaCy small model (not Presidio's default large one)."""
    global _analyzer
    with _analyzer_lock:
        if _analyzer is None:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            nlp = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
                    # spaCy labels with no Presidio entity (silences a warning per match)
                    "ner_model_configuration": {"labels_to_ignore": _UNMAPPED_SPACY_LABELS},
                }
            ).create_engine()
            _analyzer = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
        return _analyzer


def _segment(text: str, breaks: Sequence[int], i: int) -> tuple[int, int]:
    """[start, end) of the column segment holding index i: its printed line, cut at column breaks."""
    start = max(text.rfind("\n", 0, i), text.rfind("\r", 0, i)) + 1
    ends = [j for j in (text.find("\r", i), text.find("\n", i)) if j >= 0]
    end = min(ends, default=len(text))
    k = bisect.bisect_right(breaks, i)
    if k > 0:
        start = max(start, breaks[k - 1])
    if k < len(breaks):
        end = min(end, breaks[k])
    return start, end


def _cut(text: str, breaks: Sequence[int], start: int, end: int) -> tuple[int, int]:
    """The span trimmed of whitespace and ended at its start's segment."""
    while start < end and text[start].isspace():
        start += 1
    end = min(end, _segment(text, breaks, start)[1])
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def matches(text: str, breaks: Sequence[int] = ()) -> list[Match]:
    """Identifier spans, each within one column segment; `breaks` are sorted indices that start a column."""
    found = []
    for res in analyzer().analyze(
        text, language="en", entities=_PRESIDIO_ENTITIES, score_threshold=SCORE_THRESHOLD
    ):
        start, end = _cut(text, breaks, res.start, res.end)
        if res.recognition_metadata.get("recognizer_name") == "SpacyRecognizer":
            # the small model runs names into codes and claim numbers: keep the words before the first digit
            if digit := _DIGIT_WORD.search(text, start, end):
                start, end = _cut(text, breaks, start, digit.start())
        elif res.entity_type == "PHONE_NUMBER":
            seg_start, seg_end = _segment(text, breaks, start)
            if not any(c in text[start:end] for c in "()-.") or _DATE_OR_AMOUNT.search(
                text, seg_start, seg_end
            ):
                continue  # bare digit runs (codes, NPIs, accounts) and columns holding amounts or dates
        found.append(Match(res.entity_type, start, end))
    for entity, rx in _LABELLED_RX:
        for hit in rx.finditer(text):
            found.append(Match(entity, *_cut(text, breaks, *hit.span("v"))))
    kept: list[Match] = []
    for m in sorted(found, key=lambda m: (m.start, -m.end)):
        if m.end > m.start and not any(
            k.entity_type == m.entity_type and k.start <= m.start and m.end <= k.end for k in kept
        ):
            kept.append(m)
    return kept


def _column_breaks(tp: pdfium.PdfTextPage, text: str) -> list[int]:
    """Indices of characters that start a new column (a wide gap after the previous character on the line)."""
    breaks = []
    prev_right: float | None = None
    for i, c in enumerate(text):
        if c in "\r\n":
            prev_right = None
        elif not c.isspace():
            left, bottom, right, top = tp.get_charbox(i, loose=True)
            if prev_right is not None and left - prev_right > COLUMN_GAP * (top - bottom):
                breaks.append(i)
            prev_right = right
    return breaks


def find_phi_boxes(page: pdfium.PdfPage) -> PageRedaction:
    """Boxes (relative to the rendered crop box) covering identifiers in the page's text layer."""
    if page.get_rotation():  # masks assume an unrotated page; don't guess
        return PageRedaction(False)
    tp = page.get_textpage()
    try:
        n = tp.count_chars()
        text = tp.get_text_range() if n > 0 else ""
        # text index == char index is what lets spans map to char boxes; refuse rather than mis-mask
        if len(text) != n or sum(not c.isspace() for c in text) < MIN_TEXT_CHARS:
            return PageRedaction(False)
        x0, y0, x1, y1 = page.get_cropbox()
        boxes: list[Box] = []
        entities: dict[str, int] = {}
        for m in matches(text, _column_breaks(tp, text)):
            entities[m.entity_type] = entities.get(m.entity_type, 0) + 1
            chars = [tp.get_charbox(i) for i in range(m.start, m.end) if not text[i].isspace()]
            boxes.append(
                (
                    min(c[0] for c in chars) - x0,
                    min(c[1] for c in chars) - y0,
                    max(c[2] for c in chars) - x0,
                    max(c[3] for c in chars) - y0,
                )
            )
        w, h = x1 - x0, y1 - y0
        clipped = [(max(b[0], 0), max(b[1], 0), min(b[2], w), min(b[3], h)) for b in boxes]
        return PageRedaction(
            True,
            [b for b in clipped if b[0] < b[2] and b[1] < b[3]],
            entities,
            clipped=clipped != boxes,
        )
    finally:
        tp.close()


def mask(
    image: Image.Image, boxes: list[Box], page_size_pt: tuple[float, float], scale: float
) -> Image.Image:
    """A copy of the rendered page with each box painted black."""
    out = image.copy()
    draw = ImageDraw.Draw(out)
    height = page_size_pt[1]
    for left, bottom, right, top in boxes:
        draw.rectangle(
            (
                left * scale - PAD_PX,
                (height - top) * scale - PAD_PX,
                right * scale + PAD_PX,
                (height - bottom) * scale + PAD_PX,
            ),
            fill=0,
        )
    return out
