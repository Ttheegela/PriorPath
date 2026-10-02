import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.reference.base import CodeStatus, FeeRate, InMemoryReference, MueLimit, PtpEdit, RefVersion


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _date_or_none(v: str) -> date | None:
    return date.fromisoformat(v) if v else None


def load_normalized(directory: Path) -> InMemoryReference:
    return InMemoryReference(
        versions=[
            RefVersion(
                r["ref_version"],
                r["kind"],
                date.fromisoformat(r["valid_from"]),
                date.fromisoformat(r["valid_to"]),
            )
            for r in _rows(directory / "versions.csv")
        ],
        ptp_edits=[
            PtpEdit(
                r["col1"],
                r["col2"],
                date.fromisoformat(r["effective"]),
                _date_or_none(r["deleted"]),
                r["modifier_indicator"],
                r["ref_version"],
            )
            for r in _rows(directory / "ptp.csv")
        ],
        mue_limits=[
            MueLimit(r["code"], int(r["max_units"]), int(r["mai"]), r["ref_version"])
            for r in _rows(directory / "mue.csv")
        ],
        fees=[
            FeeRate(r["code"], Decimal(r["nonfacility_rate"]), Decimal(r["facility_rate"]), r["ref_version"])
            for r in _rows(directory / "fees.csv")
        ],
        code_statuses=[
            CodeStatus(r["code"], r["status"], r["ref_version"]) for r in _rows(directory / "codes.csv")
        ],
    )


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_normalized(ref: InMemoryReference, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _write(
        directory / "versions.csv",
        ["ref_version", "kind", "valid_from", "valid_to"],
        [[v.ref_version, v.kind, v.valid_from.isoformat(), v.valid_to.isoformat()] for v in ref.versions],
    )
    _write(
        directory / "ptp.csv",
        ["col1", "col2", "effective", "deleted", "modifier_indicator", "ref_version"],
        [
            [
                e.col1,
                e.col2,
                e.effective.isoformat(),
                e.deleted.isoformat() if e.deleted else "",
                e.modifier_indicator,
                e.ref_version,
            ]
            for e in ref.ptp_edits
        ],
    )
    _write(
        directory / "mue.csv",
        ["code", "max_units", "mai", "ref_version"],
        [[m.code, str(m.max_units), str(m.mai), m.ref_version] for m in ref.mue_limits],
    )
    _write(
        directory / "fees.csv",
        ["code", "nonfacility_rate", "facility_rate", "ref_version"],
        [[f.code, str(f.nonfacility), str(f.facility), f.ref_version] for f in ref.fees],
    )
    _write(
        directory / "codes.csv",
        ["code", "status", "ref_version"],
        [[c.code, c.status, c.ref_version] for c in ref.code_statuses],
    )
