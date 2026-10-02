# ruff: noqa: E501, E741
from datetime import date
from decimal import Decimal

from app.llm.extract import extract_page, merge, needs_review, parse_page
from app.llm.vision import VisionError
from tests.fakes import FakeVision


def f(value, confidence=0.99):  # type: ignore[no-untyped-def]
    return {"value": value, "confidence": confidence}


def row(code="99213", units=1, charge="120.00", dos="2026-11-03", mods=None, conf=0.99):  # type: ignore[no-untyped-def]
    return {
        "code": f(code, conf),
        "modifiers": f(mods or []),
        "units": f(units),
        "charge": f(charge),
        "date_of_service": f(dos),
    }


def page(*rows, claim_id="ACC-1"):  # type: ignore[no-untyped-def]
    return {"claim_id": claim_id, "provider": "Clinic", "payer": "Medicare", "lines": list(rows)}


def test_valid_rows_become_extracted_line_items() -> None:
    res = extract_page(b"png", 1, FakeVision([page(row(), row(code="96372", mods=["59"], charge="30"))]))
    assert [l.id for l in res.lines] == ["P1-L1", "P1-L2"]
    l2 = res.lines[1]
    assert (l2.code, l2.modifiers, l2.units, l2.charge, l2.date_of_service) == (
        "96372",
        ["59"],
        1,
        Decimal("30.00"),
        date(2026, 11, 3),
    )
    assert l2.source == "extracted" and l2.confidence == 0.99
    assert res.claim_id == "ACC-1" and not res.errors


def test_bad_rows_are_dropped_with_page_and_row_named() -> None:
    res = extract_page(
        b"png",
        2,
        FakeVision([page(row(), row(code=""), row(units=-1), row(dos="2026-13-40"), row(charge="abc"))]),
    )
    assert [l.id for l in res.lines] == ["P2-L1"]
    assert len(res.errors) == 4 and all(e.startswith("page 2, row ") for e in res.errors)


def test_low_confidence_field_marks_review_and_is_kept_per_field() -> None:
    res = extract_page(b"png", 1, FakeVision([page(row(conf=0.6))]))
    assert res.lines[0].field_confidence["code"] == 0.6
    assert res.lines[0].confidence == 0.6
    assert needs_review(res.lines)


def test_model_failure_raises() -> None:
    import pytest

    with pytest.raises(VisionError):
        extract_page(b"png", 1, FakeVision([VisionError("timeout")]))


def test_merge_keeps_first_non_null_header_and_all_lines() -> None:
    a = extract_page(b"p", 1, FakeVision([page(row(), claim_id=None)]))
    b = extract_page(b"p", 2, FakeVision([page(row(code="96372"), claim_id="ACC-9")]))
    m = merge([a, b])
    assert m.claim_id == "ACC-9" and [l.id for l in m.lines] == ["P1-L1", "P2-L1"]


def test_no_lines_at_all_needs_review() -> None:
    assert needs_review([])


def test_parse_page_is_the_pure_half_of_extract_page() -> None:
    res = parse_page(page(row(conf=1.7)), 3)
    assert (
        res.lines[0].id == "P3-L1"
        and res.lines[0].field_confidence["code"] == 1.0
        and res.provider == "Clinic"
    )
