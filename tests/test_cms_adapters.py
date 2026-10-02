# ruff: noqa: E501
import csv
import io
from datetime import date
from decimal import Decimal

import pytest

from app.reference.cms_adapters import find_header, parse_mue, parse_pfs, parse_ptp


def rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text.strip())))


PTP = """
"CPT only copyright 2025 American Medical Association. All rights reserved."
Column 1,Column 2,"*=in existence prior to 1996",Effective Date,Deletion Date *=no data,Modifier 0=not allowed 1=allowed 9=not applicable,PTP Edit Rationale
93000,93005,,20000101,*,0,Misuse of column two code with column one code
29881,29880,,20000101,20261115,1,Standards of medical / surgical practice
bad,row
"""

MUE = """
HCPCS/CPT Code,Practitioner Services MUE Values,MUE Adjudication Indicator,MUE Rationale
99213,1,2 Date of Service Edit: Policy,Code Descriptor / CPT Instruction
96372,4,1 Line Edit,Clinical: Data
"""

PFS = """
"2026 National Physician Fee Schedule Relative Value File"
,,,,,,NON-FAC,,FACILITY,,,NON-FACILITY,FACILITY,,,,CONV
HCPCS,MOD,DESCRIPTION,STATUS CODE,NOT USED FOR MEDICARE PAYMENT,WORK RVU,PE RVU,NA INDICATOR,PE RVU,NA INDICATOR,MP RVU,TOTAL,TOTAL,PCTC IND,GLOB DAYS,MULT PROC,FACTOR
99213,,Office o/p est low,A,,1.30,1.33,,0.53,,0.10,2.73,1.93,0,XXX,0,33.7575
93000,26,Electrocardiogram,A,,0.17,0.06,,0.06,,0.01,0.24,0.24,1,XXX,0,33.7575
99201,,Office/outpatient visit new,D,,0,0,,0,,0,0,0,0,XXX,0,33.7575
77061,,Digital tomosynthesis,I,,0.5,1,,1,,0.1,1.6,1.6,0,XXX,0,33.7575
"""


def test_find_header_single_row() -> None:
    start, cols = find_header(
        rows(MUE), {"code": lambda h: "HCPCS" in h, "mai": lambda h: "ADJUDICATION" in h}
    )
    assert start == 1
    assert cols == {"code": 0, "mai": 2}


def test_find_header_two_row_header() -> None:
    start, cols = find_header(
        rows(PFS),
        {
            "nonfac": lambda h: h.startswith("NON-FACILITY") and "TOTAL" in h,
            "fac": lambda h: h.startswith("FACILITY") and "TOTAL" in h,
        },
    )
    assert start == 3
    assert cols == {"nonfac": 11, "fac": 12}


def test_find_header_missing_raises() -> None:
    with pytest.raises(ValueError):
        find_header(rows(MUE), {"x": lambda h: h == "NOPE"})


def test_parse_ptp() -> None:
    edits = parse_ptp(rows(PTP), "NCCI-T")
    assert len(edits) == 2  # malformed row skipped
    first, second = edits
    assert (first.col1, first.col2, first.effective, first.deleted, first.modifier_indicator) == (
        "93000",
        "93005",
        date(2000, 1, 1),
        None,
        "0",
    )
    assert second.deleted == date(2026, 11, 15)
    assert second.modifier_indicator == "1"


def test_parse_mue() -> None:
    limits = parse_mue(rows(MUE), "MUE-T")
    assert [(m.code, m.max_units, m.mai) for m in limits] == [("99213", 1, 2), ("96372", 4, 1)]


def test_parse_pfs_global_rows_only_and_rates() -> None:
    fees, statuses = parse_pfs(rows(PFS), "PFS-T")
    assert [(s.code, s.status) for s in statuses] == [("99213", "A"), ("99201", "D"), ("77061", "I")]
    assert len(fees) == 1  # 93000-26 is a modifier row; 99201 is D and 77061 is I (invalid, no fee)
    assert fees[0].code == "99213"
    assert fees[0].nonfacility == Decimal("92.16")  # 2.73 * 33.7575 = 92.157975
    assert fees[0].facility == Decimal("65.15")  # 1.93 * 33.7575 = 65.151975
