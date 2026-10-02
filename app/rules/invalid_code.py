from decimal import Decimal

from app.models import Claim, Evidence, Flag, Severity, make_flag
from app.reference.base import INVALID_STATUSES, Reference
from app.rules import RuleConfig


def check_invalid_code(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    flags = []
    for line in claim.lines:
        if not ref.covers(line.date_of_service):
            continue
        status = ref.code_status(line.code, line.date_of_service)
        if status is None or status.status not in INVALID_STATUSES:
            continue
        dos = line.date_of_service.isoformat()
        severity, amount = Severity.ERROR, line.charge
        if status.status == "D":
            message = f"{line.code} is a deleted code and was not billable on {dos}"
        elif config.payer_type == "medicare":
            message = (
                f"{line.code} is not valid for Medicare billing on {dos}; Medicare requires a different code"
            )
        else:
            # Status I is Medicare-specific: commercial plans often accept these CPT codes.
            severity, amount = Severity.LEAD, Decimal("0")
            message = (
                f"{line.code} is not valid for Medicare billing on {dos}; "
                f"a {config.payer_type} plan may still accept it, "
                " so confirm with the plan before disputing"
            )
        flags.append(
            make_flag(
                claim,
                "R4",
                severity,
                [line],
                Evidence(
                    table="pfs_status",
                    ref_version=status.ref_version,
                    row={"code": line.code, "status": status.status},
                ),
                amount,
                message,
            )
        )
    return flags
