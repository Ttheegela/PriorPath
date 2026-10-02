"""Best-effort masking of patient identifiers on text-layer PDF pages, before page images reach the model.

Presidio and spaCy are imported on first use only, so routes that never redact don't pay for loading them.
Callers hold the pdfium lock around find_phi_boxes (it reads the page's text layer).
"""

import re
import threading
from dataclasses import dataclass, field
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

# DATE_TIME is deliberately absent: dates of service are billing data.
REDACT_ENTITIES = ("PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN", "LOCATION", "MEMBER_ID")
MIN_TEXT_CHARS = 20  # fewer non-space chars than this: treat the page as a scan
PAD_PX = 2
SCORE_THRESHOLD = 0.5

# Labelled values on the label's own line ([ \t], not \s, so a match never reaches into the next printed row);
# lookbehinds keep the label itself unmasked. Values are words joined by single spaces, so a column gap
# (2+ spaces) ends them. Presidio compiles patterns with `regex`, which allows variable-width lookbehind.
_LABELLED = (
    (
        "MEMBER_ID",
        r"(?i)(?<=\b((member|subscriber|policy|patient)[ \t]*(id|#|no\.?|number)"
        r"|mrn([ \t]*(id|#|no\.?|number))?)[ \t]*[:#]?[ \t]*)"
        r"[A-Z0-9-]{5,20}(?![A-Z0-9-])",  # longer values: no match rather than half-masked
    ),
    # The small spaCy model misses many names and street lines, so also take the value after the label.
    (
        "PERSON",
        r"(?i)(?<=\b(patient|guarantor|subscriber|insured)([ \t]+name)?[ \t]*:[ \t]*)"
        r"[A-Za-z][A-Za-z.'-]*( [A-Za-z][A-Za-z.'-]*)*(?!\w)",
    ),
    ("LOCATION", r"(?i)(?<=\baddress[ \t]*:[ \t]*)\S+( \S+)*"),
    # Plain regex instead of Presidio's EmailRecognizer, which fetches the Public Suffix List over HTTP.
    ("EMAIL_ADDRESS", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
)
_DATE_OR_AMOUNT = re.compile(r"\$|\b\d{1,2}/\d{1,2}/\d{2,4}\b")

_UNMAPPED_SPACY_LABELS = ["CARDINAL", "EVENT", "FAC", "LANGUAGE", "LAW", "MONEY", "ORDINAL", "PERCENT"]
_UNMAPPED_SPACY_LABELS += ["PRODUCT", "QUANTITY", "WORK_OF_ART"]

Box = tuple[float, float, float, float]  # left, bottom, right, top in PDF points, origin bottom-left


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
            from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            nlp = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
                    # spaCy labels with no Presidio entity (silences a warning per match)
                    "ner_model_configuration": {"labels_to_ignore": _UNMAPPED_SPACY_LABELS},
                }
            ).create_engine()
            engine = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
            engine.registry.remove_recognizer("EmailRecognizer")
            for entity, regex in _LABELLED:
                engine.registry.add_recognizer(
                    PatternRecognizer(
                        supported_entity=entity,
                        name=f"labelled_{entity.lower()}",
                        patterns=[Pattern(f"labelled_{entity.lower()}", regex, 0.85)],
                    )
                )
            _analyzer = engine
        return _analyzer


def _row(text: str, i: int) -> tuple[int, int]:
    """[start, end) of the printed line holding index i."""
    start = max(text.rfind("\n", 0, i), text.rfind("\r", 0, i)) + 1
    ends = [j for j in (text.find("\r", i), text.find("\n", i)) if j >= 0]
    return start, min(ends, default=len(text))


def matches(text: str) -> list[Any]:
    """Presidio results cut to one printed line each, minus the kinds of hit that land on billing rows."""
    out = []
    for res in analyzer().analyze(
        text, language="en", entities=list(REDACT_ENTITIES), score_threshold=SCORE_THRESHOLD
    ):
        row_start, row_end = _row(text, res.start)
        res.end = min(res.end, row_end)
        value = text[res.start : res.end].rstrip()
        res.end = res.start + len(value)
        if not value:
            continue
        # spaCy's small model tags codes, dates and claim numbers as names or places
        if res.recognition_metadata.get("recognizer_name") == "SpacyRecognizer" and any(
            c.isdigit() for c in value
        ):
            continue
        if res.entity_type == "PHONE_NUMBER" and (
            not any(c in value for c in "()-.") or _DATE_OR_AMOUNT.search(text[row_start:row_end])
        ):
            continue  # bare digit runs (codes, NPIs, accounts) and rows that carry billing amounts or dates
        out.append(res)
    return out


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
        for res in matches(text):
            entities[res.entity_type] = entities.get(res.entity_type, 0) + 1
            chars = [tp.get_charbox(i) for i in range(res.start, res.end) if not text[i].isspace()]
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
