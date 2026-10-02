from datetime import date
from decimal import Decimal
from pathlib import Path

from app.reference.normalized import load_normalized, write_normalized
from tests.helpers import FIXTURE_DIR, FIXTURE_REF

D = date(2026, 10, 15)


def test_coverage_requires_all_three_kinds() -> None:
    assert FIXTURE_REF.covers(D)
    assert not FIXTURE_REF.covers(date(2026, 9, 30))  # PFS covers it, NCCI/MUE don't
    assert FIXTURE_REF.version_for("pfs", date(2026, 9, 30)) is not None


def test_ptp_lookup_is_directional() -> None:
    assert FIXTURE_REF.ptp("93000", "93005", D) is not None
    assert FIXTURE_REF.ptp("93005", "93000", D) is None


def test_ptp_deletion_date_is_exclusive() -> None:
    assert FIXTURE_REF.ptp("29881", "29880", date(2026, 11, 14)) is not None
    assert FIXTURE_REF.ptp("29881", "29880", date(2026, 11, 15)) is None


def test_ptp_outside_coverage_returns_none() -> None:
    assert FIXTURE_REF.ptp("93000", "93005", date(2027, 1, 5)) is None


def test_mue_fee_status_lookups() -> None:
    mue = FIXTURE_REF.mue("96372", D)
    assert mue is not None and mue.max_units == 4 and mue.mai == 1
    fee = FIXTURE_REF.fee("99213", D)
    assert fee is not None and fee.nonfacility == Decimal("92.15") and fee.facility == Decimal("65.40")
    status = FIXTURE_REF.code_status("99201", D)
    assert status is not None and status.status == "D"
    assert FIXTURE_REF.code_status("0001U", D) is None


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    write_normalized(FIXTURE_REF, tmp_path)
    again = load_normalized(tmp_path)
    assert again.versions == FIXTURE_REF.versions
    assert again.ptp_edits == FIXTURE_REF.ptp_edits
    assert again.mue_limits == FIXTURE_REF.mue_limits
    assert again.fees == FIXTURE_REF.fees
    assert again.code_statuses == FIXTURE_REF.code_statuses
    assert {p.name for p in tmp_path.iterdir()} == {p.name for p in FIXTURE_DIR.iterdir()}
