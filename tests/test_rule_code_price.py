from decimal import Decimal

from app.models import Severity
from app.rules import RuleConfig
from app.rules.invalid_code import check_invalid_code
from app.rules.price import check_price
from tests.helpers import FIXTURE_REF, claim, line

CFG = RuleConfig()


def test_deleted_code_flagged_full_charge() -> None:
    (flag,) = check_invalid_code(claim(line("L1", code="99201", charge="120.00")), FIXTURE_REF, CFG)
    assert flag.rule_id == "R4" and flag.est_overcharge == Decimal("120.00")
    assert flag.evidence.row == {"code": "99201", "status": "D"}


def test_not_valid_for_medicare_code_flagged() -> None:
    (flag,) = check_invalid_code(claim(line("L1", code="77061", charge="80.00")), FIXTURE_REF, CFG)
    assert flag.rule_id == "R4" and flag.est_overcharge == Decimal("80.00")
    assert flag.evidence.row == {"code": "77061", "status": "I"}
    assert "not valid for Medicare" in flag.message


def test_active_and_unknown_codes_not_flagged() -> None:
    assert (
        check_invalid_code(claim(line("L1", code="99213"), line("L2", code="0001U")), FIXTURE_REF, CFG) == []
    )


def test_price_outlier_nonfacility() -> None:
    (flag,) = check_price(claim(line("L1", code="99213", charge="300.00", pos="11")), FIXTURE_REF, CFG)
    assert flag.rule_id == "R5" and flag.severity is Severity.OUTLIER
    assert flag.est_overcharge == Decimal("23.55")  # 300 - 3 x 92.15
    assert flag.evidence.row["rate_type"] == "nonfacility"


def test_price_outlier_uses_facility_rate_in_hospital() -> None:
    (flag,) = check_price(claim(line("L1", code="99213", charge="200.00", pos="22")), FIXTURE_REF, CFG)
    assert flag.est_overcharge == Decimal("3.80")  # 200 - 3 x 65.40
    assert flag.evidence.row["rate_type"] == "facility"


def test_price_at_threshold_not_flagged_and_units_scale() -> None:
    assert check_price(claim(line("L1", code="99213", charge="276.45")), FIXTURE_REF, CFG) == []
    assert check_price(claim(line("L1", code="97110", units=4, charge="360.00")), FIXTURE_REF, CFG) == []


def test_multiplier_is_configurable() -> None:
    (flag,) = check_price(
        claim(line("L1", code="99213", charge="200.00")),
        FIXTURE_REF,
        RuleConfig(price_multiplier=Decimal("2")),
    )
    assert flag.est_overcharge == Decimal("15.70")  # 200 - 2 x 92.15


def test_professional_technical_modifiers_and_missing_rates_skipped() -> None:
    assert (
        check_price(claim(line("L1", code="99213", charge="900.00", modifiers=["26"])), FIXTURE_REF, CFG)
        == []
    )
    assert check_price(claim(line("L1", code="0001U", charge="900.00")), FIXTURE_REF, CFG) == []


def test_status_i_is_a_lead_for_non_medicare_payers() -> None:
    for payer in ("commercial", "unknown"):
        (flag,) = check_invalid_code(
            claim(line("L1", code="77061", charge="80.00")), FIXTURE_REF, RuleConfig(payer_type=payer)
        )
        assert flag.severity is Severity.LEAD, payer
        assert flag.est_overcharge == Decimal("0.00")
        assert "confirm" in flag.message


def test_status_d_stays_an_error_for_any_payer() -> None:
    (flag,) = check_invalid_code(
        claim(line("L1", code="99201")), FIXTURE_REF, RuleConfig(payer_type="commercial")
    )
    assert flag.severity is Severity.ERROR


def test_status_i_lead_message_is_exact() -> None:
    (flag,) = check_invalid_code(
        claim(line("L1", code="77061", charge="80.00")), FIXTURE_REF, RuleConfig(payer_type="commercial")
    )
    assert flag.message == (
        "77061 is not valid for Medicare billing on 2026-10-15; "
        "a commercial plan may still accept it, so confirm with the plan before disputing"
    )
