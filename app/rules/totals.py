"""Case-level overcharge totals that never count more than a line was billed."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.models import Claim, Flag, Severity, money


@dataclass(frozen=True)
class Totals:
    errors: Decimal
    outliers: Decimal


def _allocate(flag: Flag, charges: dict[str, Decimal]) -> dict[str, Decimal]:
    ids = [i for i in flag.line_ids if i in charges]
    if not ids:
        return {}
    if flag.rule_id == "R1":  # every line after the first is the extra copy
        return {i: charges[i] for i in ids[1:]}
    if flag.rule_id == "R2":  # the column-2 (last) line is the one that shouldn't be billed
        return {ids[-1]: flag.est_overcharge}
    total = sum((charges[i] for i in ids), Decimal("0"))
    if total == 0:
        return {}
    return {i: flag.est_overcharge * charges[i] / total for i in ids}


def case_totals(claim: Claim, flags: Iterable[Flag]) -> Totals:
    charges = {line.id: line.charge for line in claim.lines}
    errors: defaultdict[str, Decimal] = defaultdict(Decimal)
    outliers: defaultdict[str, Decimal] = defaultdict(Decimal)
    for f in flags:
        bucket = (
            errors if f.severity is Severity.ERROR else outliers if f.severity is Severity.OUTLIER else None
        )
        if bucket is None:
            continue
        for line_id, amount in _allocate(f, charges).items():
            bucket[line_id] += amount
    total_errors = total_outliers = Decimal("0")
    for line_id, charge in charges.items():
        e = min(errors[line_id], charge)
        total_errors += e
        total_outliers += min(outliers[line_id], charge - e)
    return Totals(money(total_errors), money(total_outliers))
