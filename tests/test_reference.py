import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

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


def _with_versions(tmp_path: Path, *rows: str) -> Path:
    shutil.copytree(FIXTURE_DIR, tmp_path, dirs_exist_ok=True)
    (tmp_path / "versions.csv").write_text("ref_version,kind,valid_from,valid_to\n" + "\n".join(rows) + "\n")
    return tmp_path


def test_overlapping_versions_of_one_kind_are_rejected(tmp_path: Path) -> None:
    d = _with_versions(
        tmp_path,
        "NCCI-Q3,ncci,2026-07-01,2026-10-01",
        "NCCI-TEST,ncci,2026-10-01,2026-12-31",
        "MUE-TEST,mue,2026-10-01,2026-12-31",
        "PFS-TEST,pfs,2026-01-01,2026-12-31",
    )
    with pytest.raises(ValueError, match="NCCI-Q3.*NCCI-TEST.*overlap"):
        load_normalized(d)


def test_adjacent_versions_load_and_resolve_by_date(tmp_path: Path) -> None:
    d = _with_versions(
        tmp_path,
        "NCCI-Q3,ncci,2026-07-01,2026-09-30",
        "NCCI-TEST,ncci,2026-10-01,2026-12-31",
        "MUE-TEST,mue,2026-10-01,2026-12-31",
        "PFS-TEST,pfs,2026-01-01,2026-12-31",
    )
    ref = load_normalized(d)
    q3, q4 = ref.version_for("ncci", date(2026, 9, 30)), ref.version_for("ncci", date(2026, 10, 1))
    assert q3 is not None and q3.ref_version == "NCCI-Q3"
    assert q4 is not None and q4.ref_version == "NCCI-TEST"


def test_committed_subset_covers_july_to_december_2026() -> None:
    ref = load_normalized(Path("data/reference/subset"))
    for dos in (date(2026, 7, 1), date(2026, 9, 30), date(2026, 10, 1), date(2026, 12, 31)):
        assert ref.covers(dos), dos
    assert not ref.covers(date(2026, 6, 30)) and not ref.covers(date(2027, 1, 1))
    q3 = {v.ref_version for v in ref.versions if v.valid_from == date(2026, 7, 1)}
    assert q3 == {"NCCI-2026Q3", "MUE-2026Q3", "PFS-2026C"}
