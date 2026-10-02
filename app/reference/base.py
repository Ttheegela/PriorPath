from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

KINDS = ("ncci", "mue", "pfs")
# D = deleted, I = not valid for Medicare; Medicare uses another code
INVALID_STATUSES = frozenset({"D", "I"})


@dataclass(frozen=True)
class RefVersion:
    ref_version: str
    kind: str
    valid_from: date
    valid_to: date


@dataclass(frozen=True)
class PtpEdit:
    col1: str
    col2: str
    effective: date
    deleted: date | None
    modifier_indicator: str  # "0" never allowed, "1" allowed with modifier, "9" not applicable
    ref_version: str

    def active_on(self, dos: date) -> bool:
        return self.effective <= dos and (self.deleted is None or dos < self.deleted)


@dataclass(frozen=True)
class MueLimit:
    code: str
    max_units: int
    mai: int  # 1 = per line, 2/3 = per date of service
    ref_version: str


@dataclass(frozen=True)
class FeeRate:
    code: str
    nonfacility: Decimal
    facility: Decimal
    ref_version: str


@dataclass(frozen=True)
class CodeStatus:
    code: str
    status: str  # PFS status code; "D" = deleted
    ref_version: str


class Reference(Protocol):
    def version_for(self, kind: str, dos: date) -> RefVersion | None: ...
    def covers(self, dos: date) -> bool: ...
    def ptp(self, col1: str, col2: str, dos: date) -> PtpEdit | None: ...
    def mue(self, code: str, dos: date) -> MueLimit | None: ...
    def fee(self, code: str, dos: date) -> FeeRate | None: ...
    def code_status(self, code: str, dos: date) -> CodeStatus | None: ...


class InMemoryReference:
    def __init__(
        self,
        versions: Iterable[RefVersion],
        ptp_edits: Iterable[PtpEdit],
        mue_limits: Iterable[MueLimit],
        fees: Iterable[FeeRate],
        code_statuses: Iterable[CodeStatus],
    ) -> None:
        self.versions = list(versions)
        self.ptp_edits = list(ptp_edits)
        self.mue_limits = list(mue_limits)
        self.fees = list(fees)
        self.code_statuses = list(code_statuses)
        self._ptp: dict[tuple[str, str, str], list[PtpEdit]] = defaultdict(list)
        for e in self.ptp_edits:
            self._ptp[(e.ref_version, e.col1, e.col2)].append(e)
        self._mue = {(m.ref_version, m.code): m for m in self.mue_limits}
        self._fee = {(f.ref_version, f.code): f for f in self.fees}
        self._status = {(c.ref_version, c.code): c for c in self.code_statuses}

    def version_for(self, kind: str, dos: date) -> RefVersion | None:
        for v in self.versions:
            if v.kind == kind and v.valid_from <= dos <= v.valid_to:
                return v
        return None

    def covers(self, dos: date) -> bool:
        return all(self.version_for(kind, dos) is not None for kind in KINDS)

    def ptp(self, col1: str, col2: str, dos: date) -> PtpEdit | None:
        v = self.version_for("ncci", dos)
        if v is None:
            return None
        for edit in self._ptp.get((v.ref_version, col1, col2), []):
            if edit.active_on(dos):
                return edit
        return None

    def mue(self, code: str, dos: date) -> MueLimit | None:
        v = self.version_for("mue", dos)
        return None if v is None else self._mue.get((v.ref_version, code))

    def fee(self, code: str, dos: date) -> FeeRate | None:
        v = self.version_for("pfs", dos)
        return None if v is None else self._fee.get((v.ref_version, code))

    def code_status(self, code: str, dos: date) -> CodeStatus | None:
        v = self.version_for("pfs", dos)
        return None if v is None else self._status.get((v.ref_version, code))
