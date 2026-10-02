from decimal import Decimal

from app.models import Claim, Evidence, Flag, Severity, make_flag
from app.reference.base import Reference
from app.rules import RuleConfig


def check_coverage(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    uncovered = [line for line in claim.lines if not ref.covers(line.date_of_service)]
    if not uncovered:
        return []
    dates = sorted({line.date_of_service.isoformat() for line in uncovered})
    return [
        make_flag(
            claim,
            "R0",
            Severity.NOTICE,
            uncovered,
            Evidence(table="reference_versions", ref_version=None, row={"dates": ",".join(dates)}),
            Decimal("0"),
            f"Cannot audit {len(uncovered)} line(s): no reference data loaded for {', '.join(dates)}",
        )
    ]
