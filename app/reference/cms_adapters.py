"""Turn raw CMS rows (already read from CSV/XLSX) into reference dataclasses.

CMS files carry preambles and sometimes multi-row headers, so headers are located by
matching column titles rather than by fixed positions. Malformed data rows are skipped.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

from app.models import money
from app.reference.base import INVALID_STATUSES, CodeStatus, FeeRate, MueLimit, PtpEdit

Matcher = Callable[[str], bool]


def _combined(rows: list[list[str]], start: int, width: int) -> list[str]:
    ncols = max(len(rows[start + k]) for k in range(width))
    out = []
    for c in range(ncols):
        parts = [rows[start + k][c].strip() for k in range(width) if c < len(rows[start + k])]
        out.append(" ".join(p for p in parts if p).upper())
    return out


def find_header(rows: list[list[str]], matchers: dict[str, Matcher]) -> tuple[int, dict[str, int]]:
    for width in (1, 2, 3):
        for start in range(len(rows) - width + 1):
            header = _combined(rows, start, width)
            cols: dict[str, int] = {}
            for key, match in matchers.items():
                hits = [i for i, h in enumerate(header) if match(h)]
                if len(hits) != 1:
                    break
                cols[key] = hits[0]
            else:
                return start + width, cols
    raise ValueError(f"header not found for columns {sorted(matchers)}")


def _cell(row: list[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


def _yyyymmdd(v: str) -> date:
    if len(v) != 8 or not v.isdigit():
        raise ValueError(f"bad date {v!r}")
    return date(int(v[:4]), int(v[4:6]), int(v[6:]))


def parse_ptp(rows: list[list[str]], ref_version: str) -> list[PtpEdit]:
    start, c = find_header(
        rows,
        {
            "col1": lambda h: h.startswith("COLUMN 1"),
            "col2": lambda h: h.startswith("COLUMN 2"),
            "effective": lambda h: "EFFECTIVE" in h,
            "deletion": lambda h: "DELETION" in h,
            "modifier": lambda h: h.startswith("MODIFIER"),
        },
    )
    edits = []
    for row in rows[start:]:
        try:
            deletion = _cell(row, c["deletion"])
            indicator = _cell(row, c["modifier"])[:1]
            if indicator not in {"0", "1", "9"}:
                raise ValueError(f"bad modifier indicator {indicator!r}")
            edits.append(
                PtpEdit(
                    col1=_cell(row, c["col1"]).upper(),
                    col2=_cell(row, c["col2"]).upper(),
                    effective=_yyyymmdd(_cell(row, c["effective"])),
                    deleted=None if deletion in {"", "*"} else _yyyymmdd(deletion),
                    modifier_indicator=indicator,
                    ref_version=ref_version,
                )
            )
        except ValueError:
            continue
    return edits


def parse_mue(rows: list[list[str]], ref_version: str) -> list[MueLimit]:
    start, c = find_header(
        rows,
        {
            "code": lambda h: "HCPCS" in h,
            "value": lambda h: "MUE VALUE" in h,
            "mai": lambda h: "ADJUDICATION INDICATOR" in h,
        },
    )
    limits = []
    for row in rows[start:]:
        try:
            code = _cell(row, c["code"]).upper()
            if not code:
                raise ValueError("empty code")
            limits.append(
                MueLimit(
                    code=code,
                    max_units=int(float(_cell(row, c["value"]))),
                    mai=int(_cell(row, c["mai"])[:1]),
                    ref_version=ref_version,
                )
            )
        except ValueError:
            continue
    return limits


def parse_pfs(rows: list[list[str]], ref_version: str) -> tuple[list[FeeRate], list[CodeStatus]]:
    start, c = find_header(
        rows,
        {
            "code": lambda h: h == "HCPCS",
            "mod": lambda h: h == "MOD",
            "status": lambda h: "STATUS" in h,
            "nonfac": lambda h: h.startswith("NON-FACILITY") and "TOTAL" in h,
            "fac": lambda h: h.startswith("FACILITY") and "TOTAL" in h,
            "cf": lambda h: "CONV" in h or "CONVERSION" in h,
        },
    )
    fees: list[FeeRate] = []
    statuses: list[CodeStatus] = []
    for row in rows[start:]:
        code = _cell(row, c["code"]).upper()
        if not code or _cell(row, c["mod"]):
            continue
        status = _cell(row, c["status"]).upper()
        statuses.append(CodeStatus(code, status, ref_version))
        try:
            nonfac = Decimal(_cell(row, c["nonfac"]) or "0")
            fac = Decimal(_cell(row, c["fac"]) or "0")
            cf = Decimal(_cell(row, c["cf"]))
        except InvalidOperation:
            continue
        if status not in INVALID_STATUSES and nonfac > 0:
            fees.append(FeeRate(code, money(nonfac * cf), money(fac * cf), ref_version))
    return fees, statuses
