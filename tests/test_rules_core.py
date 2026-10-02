from datetime import date
from decimal import Decimal

from app.models import Severity
from app.rules import RuleConfig, run_rules
from app.rules.coverage import check_coverage
from app.rules.duplicates import check_duplicates
from tests.helpers import FIXTURE_REF, claim, line

CFG = RuleConfig()


def test_identical_lines_flagged_once_with_extra_charge() -> None:
    c = claim(line("L1"), line("L2"), line("L3", code="97110", charge="40.00"))
    flags = check_duplicates(c, FIXTURE_REF, CFG)
    assert len(flags) == 1
    assert flags[0].rule_id == "R1" and flags[0].severity is Severity.ERROR
    assert flags[0].line_ids == ["L1", "L2"]
    assert flags[0].est_overcharge == Decimal("150.00")


def test_three_copies_overcharge_is_two_extra_lines() -> None:
    c = claim(line("L1"), line("L2", charge="140.00"), line("L3", charge="160.00"))
    (flag,) = check_duplicates(c, FIXTURE_REF, CFG)
    assert flag.line_ids == ["L1", "L2", "L3"]
    assert flag.est_overcharge == Decimal("300.00")


def test_different_units_or_dates_are_not_duplicates() -> None:
    c = claim(line("L1"), line("L2", units=2), line("L3", dos=date(2026, 10, 16)))
    assert check_duplicates(c, FIXTURE_REF, CFG) == []


def test_modifier_order_does_not_matter() -> None:
    c = claim(line("L1", modifiers=["RT", "LT"]), line("L2", modifiers=["lt", "rt"]))
    assert len(check_duplicates(c, FIXTURE_REF, CFG)) == 1


def test_repeat_procedure_modifiers_are_not_duplicates() -> None:
    for mod in ("76", "77", "91"):
        c = claim(line("L1"), line("L2", modifiers=[mod]))
        assert check_duplicates(c, FIXTURE_REF, CFG) == [], mod


def test_coverage_notice_only_for_uncovered_lines() -> None:
    c = claim(line("L1"), line("L2", dos=date(2027, 1, 5)), line("L3", dos=date(2027, 1, 6)))
    (flag,) = check_coverage(c, FIXTURE_REF, CFG)
    assert flag.rule_id == "R0" and flag.severity is Severity.NOTICE
    assert flag.line_ids == ["L2", "L3"]
    assert flag.est_overcharge == Decimal("0.00")
    assert flag.evidence.row["dates"] == "2027-01-05,2027-01-06"


def test_run_rules_mixed_coverage_still_audits_covered_lines() -> None:
    c = claim(line("L1"), line("L2"), line("L3", dos=date(2027, 1, 5)))
    rule_ids = [f.rule_id for f in run_rules(c, FIXTURE_REF)]
    assert rule_ids[0] == "R0"
    assert "R1" in rule_ids


def test_empty_claim_produces_no_flags() -> None:
    assert run_rules(claim(), FIXTURE_REF) == []
