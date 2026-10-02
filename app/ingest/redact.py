"""Best-effort masking of patient identifiers on text-layer PDF pages, before page images reach the model.

Presidio and spaCy are imported on first use only, so routes that never redact don't pay for loading them.
Callers hold the pdfium lock around find_phi_boxes (it reads the page's text layer).
"""

import threading
from dataclasses import dataclass, field
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

# DATE_TIME is deliberately absent: dates of service are billing data.
REDACT_ENTITIES = ("PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN", "LOCATION", "MEMBER_ID")
MIN_TEXT_CHARS = 20  # fewer non-space chars than this: treat the page as a scan
PAD_PX = 2

# Labelled values on the label's own line ([ \t], not \s, so a match never reaches into the next printed row);
# lookbehinds keep the label itself unmasked.
# Presidio compiles patterns with the `regex` module, which allows variable-width lookbehind.
_LABELLED = (
    (
        "MEMBER_ID",
        r"(?i)(?<=\b(member|subscriber|policy|mrn|patient)[ \t]*(id|#|no\.?|number)[ \t]*[:#]?[ \t]*)"
        r"[A-Z0-9-]{5,20}",
    ),
    # The small spaCy model misses many names and street lines, so also take the value after the label.
    (
        "PERSON",
        r"(?i)(?<=\b(patient|guarantor|subscriber|insured)([ \t]+name)?[ \t]*:[ \t]*)"
        r"[A-Za-z][A-Za-z.' -]*[A-Za-z.]",
    ),
    ("LOCATION", r"(?i)(?<=\baddress[ \t]*:[ \t]*)[^\r\n]*[^\s]"),
)

_UNMAPPED_SPACY_LABELS = ["CARDINAL", "EVENT", "FAC", "LANGUAGE", "LAW", "MONEY", "ORDINAL", "PERCENT"]
_UNMAPPED_SPACY_LABELS += ["PRODUCT", "QUANTITY", "WORK_OF_ART"]

Box = tuple[float, float, float, float]  # left, bottom, right, top in PDF points, origin bottom-left


@dataclass
class PageRedaction:
    redactable: bool
    boxes: list[Box] = field(default_factory=list)
    entities: dict[str, int] = field(default_factory=dict)


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
        for res in analyzer().analyze(text, language="en", entities=list(REDACT_ENTITIES)):
            entities[res.entity_type] = entities.get(res.entity_type, 0) + 1
            line: list[Box] = []
            for i in range(res.start, res.end + 1):  # one box per printed line of the span
                if i < res.end and not text[i].isspace():
                    line.append(tp.get_charbox(i))
                elif line and (i == res.end or text[i] in "\r\n"):
                    boxes.append(
                        (
                            min(b[0] for b in line) - x0,
                            min(b[1] for b in line) - y0,
                            max(b[2] for b in line) - x0,
                            max(b[3] for b in line) - y0,
                        )
                    )
                    line = []
        w, h = x1 - x0, y1 - y0
        if any(b[0] < 0 or b[1] < 0 or b[2] > w or b[3] > h for b in boxes):
            return PageRedaction(False)
        return PageRedaction(True, boxes, entities)
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
