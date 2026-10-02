from decimal import Decimal

from app.models import Evidence, Flag, Severity, make_flag
from app.rules.totals import case_totals
from tests.helpers import claim, line

EV = Evidence(table="t", ref_version=None, row={})


def flag(c, rule: str, sev: Severity, ids: list[str], amount: str) -> Flag:  # type: ignore[no-untyped-def]
    lines = [x for x in c.lines if x.id in ids]
    return make_flag(c, rule, sev, sorted(lines, key=lambda x: ids.index(x.id)), EV, Decimal(amount), "m")


def test_error_and_outlier_on_same_line_are_capped_at_its_charge() -> None:
    c = claim(line("L1", code="93000", charge="50.00"), line("L2", code="93005", charge="30.00"))
    flags = [
        flag(c, "R2", Severity.ERROR, ["L1", "L2"], "30.00"),
        flag(c, "R5", Severity.OUTLIER, ["L2"], "20.00"),
    ]
    t = case_totals(c, flags)
    assert (t.errors, t.outliers) == (Decimal("30.00"), Decimal("0.00"))


def test_two_errors_on_one_line_never_exceed_the_line() -> None:
    c = claim(line("L1", code="99201", charge="100.00"), line("L2", code="93005", charge="100.00"))
    flags = [
        flag(c, "R4", Severity.ERROR, ["L2"], "100.00"),
        flag(c, "R2", Severity.ERROR, ["L1", "L2"], "100.00"),
    ]
    assert case_totals(c, flags).errors == Decimal("100.00")


def test_duplicates_count_each_extra_line() -> None:
    c = claim(line("L1", charge="100.00"), line("L2", charge="140.00"), line("L3", charge="160.00"))
    assert case_totals(c, [flag(c, "R1", Severity.ERROR, ["L1", "L2", "L3"], "300.00")]).errors == Decimal(
        "300.00"
    )


def test_day_level_mue_spreads_by_charge() -> None:
    c = claim(
        line("L1", code="97110", units=4, charge="120.00"), line("L2", code="97110", units=3, charge="90.00")
    )
    assert case_totals(c, [flag(c, "R3", Severity.ERROR, ["L1", "L2"], "30.00")]).errors == Decimal("30.00")


def test_outliers_counted_separately_and_leads_notices_ignored() -> None:
    c = claim(line("L1", charge="300.00"), line("L2", code="77061", charge="80.00"))
    flags = [
        flag(c, "R5", Severity.OUTLIER, ["L1"], "23.55"),
        flag(c, "R4", Severity.LEAD, ["L2"], "0"),
        flag(c, "R0", Severity.NOTICE, ["L2"], "0"),
    ]
    t = case_totals(c, flags)
    assert (t.errors, t.outliers) == (Decimal("0.00"), Decimal("23.55"))
