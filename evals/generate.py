"""Synthetic claims with planted, labeled billing errors (real codes, fake patients)."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models import Claim, LineItem, money
from app.reference.base import INVALID_STATUSES, KINDS, InMemoryReference

PLANTABLE = ("R1", "R2", "R3", "R4", "R5")
Expected = tuple[str, frozenset[str]]


@dataclass
class LabeledClaim:
    claim: Claim
    expected: set[Expected]
    planted: list[str]


def coverage_window(ref: InMemoryReference) -> tuple[date, date]:
    starts, ends = [], []
    for kind in KINDS:
        vs = [v for v in ref.versions if v.kind == kind]
        if not vs:
            raise ValueError(f"reference has no {kind} version")
        starts.append(min(v.valid_from for v in vs))
        ends.append(max(v.valid_to for v in vs))
    lo, hi = max(starts), min(ends)
    if lo > hi:
        raise ValueError("reference versions do not overlap")
    return lo, hi


def _pair_edit(ref: InMemoryReference, a: str, b: str, dos: date) -> bool:
    return ref.ptp(a, b, dos) is not None or ref.ptp(b, a, dos) is not None


def _conflicts(ref: InMemoryReference, code: str, lines: list[LineItem], dos: date) -> bool:
    return any(code == x.code or _pair_edit(ref, code, x.code, dos) for x in lines)


def _safe_codes(ref: InMemoryReference, dos: date) -> list[str]:
    out = []
    for f in sorted(ref.fees, key=lambda f: f.code):
        mue = ref.mue(f.code, dos)
        status = ref.code_status(f.code, dos)
        if (
            ref.fee(f.code, dos) is f
            and f.nonfacility > 0
            and mue is not None
            and mue.max_units >= 2
            and (status is None or status.status not in INVALID_STATUSES)
        ):
            out.append(f.code)
    return out


def _mult(rng: random.Random, lo: float, hi: float) -> Decimal:
    return Decimal(str(round(rng.uniform(lo, hi), 2)))


def _line(
    ref: InMemoryReference,
    rng: random.Random,
    dos: date,
    code: str,
    idx: int,
    units: int = 1,
    mult: tuple[float, float] = (1.2, 2.5),
) -> LineItem:
    fee = ref.fee(code, dos)
    if fee is not None and fee.nonfacility > 0:
        charge = money(fee.nonfacility * units * _mult(rng, *mult))
    else:
        charge = Decimal("100.00") * units
    return LineItem(
        id=f"L{idx}", code=code, units=units, charge=charge, date_of_service=dos, place_of_service="11"
    )


def _plant_r1(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    src = rng.choice(lines)
    dup = src.model_copy(update={"id": f"L{len(lines) + 1}"})
    lines.append(dup)
    return ("R1", frozenset({src.id, dup.id}))


def _plantable(ref: InMemoryReference, code: str, dos: date) -> bool:
    status = ref.code_status(code, dos)
    mue = ref.mue(code, dos)
    return (status is None or status.status not in INVALID_STATUSES) and (mue is None or mue.max_units >= 1)


def _plant_r2(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    edits = [
        e for e in ref.ptp_edits if e.modifier_indicator in {"0", "1"} and ref.ptp(e.col1, e.col2, dos) is e
    ]
    rng.shuffle(edits)
    for e in edits:
        if not (_plantable(ref, e.col1, dos) and _plantable(ref, e.col2, dos)):
            continue
        if _conflicts(ref, e.col1, lines, dos) or _conflicts(ref, e.col2, lines, dos):
            continue
        a = _line(ref, rng, dos, e.col1, len(lines) + 1)
        lines.append(a)
        b = _line(ref, rng, dos, e.col2, len(lines) + 1)
        lines.append(b)
        return ("R2", frozenset({a.id, b.id}))
    return None


def _plant_r3(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    cands = [c for c in safe if not _conflicts(ref, c, lines, dos)]
    if not cands:
        return None
    code = rng.choice(cands)
    mue = ref.mue(code, dos)
    assert mue is not None
    new = _line(ref, rng, dos, code, len(lines) + 1, units=mue.max_units + rng.randint(1, 3))
    lines.append(new)
    return ("R3", frozenset({new.id}))


def _plant_r4(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    invalid = sorted(
        c.code
        for c in ref.code_statuses
        if c.status in INVALID_STATUSES and ref.code_status(c.code, dos) is c
    )
    rng.shuffle(invalid)
    for code in invalid:
        mue = ref.mue(code, dos)
        if (mue is not None and mue.max_units < 1) or _conflicts(ref, code, lines, dos):
            continue
        new = LineItem(
            id=f"L{len(lines) + 1}",
            code=code,
            units=1,
            charge=Decimal("100.00"),
            date_of_service=dos,
            place_of_service="11",
        )
        lines.append(new)
        return ("R4", frozenset({new.id}))
    return None


def _plant_r5(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], lines: list[LineItem]
) -> Expected | None:
    cands = [c for c in safe if not _conflicts(ref, c, lines, dos)]
    if not cands:
        return None
    new = _line(ref, rng, dos, rng.choice(cands), len(lines) + 1, mult=(3.5, 6.0))
    lines.append(new)
    return ("R5", frozenset({new.id}))


Planter = Callable[[InMemoryReference, random.Random, date, list[str], list[LineItem]], Expected | None]
PLANTERS: dict[str, Planter] = {
    "R1": _plant_r1,
    "R2": _plant_r2,
    "R3": _plant_r3,
    "R4": _plant_r4,
    "R5": _plant_r5,
}


def _clean_lines(
    ref: InMemoryReference, rng: random.Random, dos: date, safe: list[str], count: int
) -> list[LineItem]:
    lines: list[LineItem] = []
    pool = safe[:]
    rng.shuffle(pool)
    for code in pool:
        if len(lines) == count:
            break
        if not _conflicts(ref, code, lines, dos):
            lines.append(_line(ref, rng, dos, code, len(lines) + 1))
    return lines


def generate(ref: InMemoryReference, n: int, seed: int, error_rate: float = 0.6) -> list[LabeledClaim]:
    rng = random.Random(seed)
    lo, hi = coverage_window(ref)
    out = []
    for i in range(n):
        dos = lo + timedelta(days=rng.randrange((hi - lo).days + 1))
        safe = _safe_codes(ref, dos)
        if not safe:
            raise ValueError("reference has no codes with fee + MUE >= 2; cannot generate claims")
        lines = _clean_lines(ref, rng, dos, safe, rng.randint(2, 5))
        expected: set[Expected] = set()
        planted: list[str] = []
        if rng.random() < error_rate:
            kinds = sorted(rng.sample(PLANTABLE, rng.choice([1, 1, 2])), key=PLANTABLE.index)
            for kind in kinds:
                got = PLANTERS[kind](ref, rng, dos, safe, lines)
                if got is not None:
                    expected.add(got)
                    planted.append(kind)
        out.append(
            LabeledClaim(
                claim=Claim(
                    id=f"C{i:04d}",
                    patient_pseudonym=f"P-{i:04d}",
                    provider="Synthetic Clinic",
                    payer="Synthetic Health Plan",
                    lines=lines,
                ),
                expected=expected,
                planted=planted,
            )
        )
    return out
