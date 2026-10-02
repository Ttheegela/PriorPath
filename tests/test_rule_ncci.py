from datetime import date
from decimal import Decimal

from app.rules import RuleConfig
from app.rules.ncci import check_ncci
from tests.helpers import FIXTURE_REF, claim, line

CFG = RuleConfig()


def test_indicator_zero_pair_flagged_with_column_two_charge() -> None:
    c = claim(line("L1", code="93000", charge="50.00"), line("L2", code="93005", charge="30.00"))
    (flag,) = check_ncci(c, FIXTURE_REF, CFG)
    assert flag.rule_id == "R2"
    assert flag.line_ids == ["L1", "L2"]
    assert flag.est_overcharge == Decimal("30.00")
    assert flag.evidence.ref_version == "NCCI-TEST"
    assert flag.evidence.row["modifier_indicator"] == "0"


def test_line_order_does_not_matter() -> None:
    c = claim(line("L1", code="93005", charge="30.00"), line("L2", code="93000", charge="50.00"))
    (flag,) = check_ncci(c, FIXTURE_REF, CFG)
    assert flag.line_ids == ["L2", "L1"]  # column 1 first


def test_indicator_one_needs_ncci_modifier() -> None:
    assert len(check_ncci(claim(line("L1", code="45380"), line("L2", code="45378")), FIXTURE_REF, CFG)) == 1
    with_59 = claim(line("L1", code="45380"), line("L2", code="45378", modifiers=["59"]))
    assert check_ncci(with_59, FIXTURE_REF, CFG) == []
    lowercase_xs = claim(line("L1", code="45380"), line("L2", code="45378", modifiers=["xs"]))
    assert check_ncci(lowercase_xs, FIXTURE_REF, CFG) == []


def test_indicator_zero_ignores_modifiers() -> None:
    c = claim(line("L1", code="93000"), line("L2", code="93005", modifiers=["59"]))
    assert len(check_ncci(c, FIXTURE_REF, CFG)) == 1


def test_indicator_nine_is_not_applicable() -> None:
    assert check_ncci(claim(line("L1", code="20610"), line("L2", code="20611")), FIXTURE_REF, CFG) == []


def test_deleted_edit_not_applied_on_deletion_date() -> None:
    def pair(d: date) -> list[object]:
        return list(
            check_ncci(
                claim(line("L1", code="29881", dos=d), line("L2", code="29880", dos=d)), FIXTURE_REF, CFG
            )
        )

    assert len(pair(date(2026, 11, 14))) == 1
    assert pair(date(2026, 11, 15)) == []


def test_different_dates_not_paired() -> None:
    c = claim(line("L1", code="93000"), line("L2", code="93005", dos=date(2026, 10, 16)))
    assert check_ncci(c, FIXTURE_REF, CFG) == []


def test_uncovered_dates_skipped() -> None:
    d = date(2027, 1, 5)
    assert (
        check_ncci(claim(line("L1", code="93000", dos=d), line("L2", code="93005", dos=d)), FIXTURE_REF, CFG)
        == []
    )
