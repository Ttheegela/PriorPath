from datetime import date
from decimal import Decimal

from app.rules import RuleConfig
from app.rules.mue import check_mue
from tests.helpers import FIXTURE_REF, claim, line

CFG = RuleConfig()


def test_line_level_limit() -> None:
    c = claim(line("L1", code="96372", units=6, charge="90.00"))
    (flag,) = check_mue(c, FIXTURE_REF, CFG)
    assert flag.rule_id == "R3" and flag.line_ids == ["L1"]
    assert flag.est_overcharge == Decimal("30.00")  # 2 excess units x 15.00
    assert flag.evidence.row == {"code": "96372", "max_units": "4", "mai": "1", "billed_units": "6"}


def test_line_level_limit_not_summed_across_lines() -> None:
    c = claim(line("L1", code="96372", units=3), line("L2", code="96372", units=3))
    assert check_mue(c, FIXTURE_REF, CFG) == []


def test_day_level_limit_sums_lines() -> None:
    c = claim(
        line("L1", code="97110", units=4, charge="120.00"), line("L2", code="97110", units=3, charge="90.00")
    )
    (flag,) = check_mue(c, FIXTURE_REF, CFG)
    assert flag.line_ids == ["L1", "L2"]
    assert flag.est_overcharge == Decimal("30.00")  # 1 excess unit x (210 / 7)


def test_day_level_limit_separate_dates() -> None:
    c = claim(line("L1", code="97110", units=4), line("L2", code="97110", units=3, dos=date(2026, 10, 16)))
    assert check_mue(c, FIXTURE_REF, CFG) == []


def test_at_limit_is_fine_and_unknown_code_skipped() -> None:
    assert check_mue(claim(line("L1", code="96372", units=4)), FIXTURE_REF, CFG) == []
    assert check_mue(claim(line("L1", code="0001U", units=50)), FIXTURE_REF, CFG) == []
