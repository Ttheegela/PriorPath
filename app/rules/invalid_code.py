from app.models import Claim, Evidence, Flag, Severity, make_flag
from app.reference.base import INVALID_STATUSES, Reference
from app.rules import RuleConfig


def check_invalid_code(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    flags = []
    for line in claim.lines:
        if not ref.covers(line.date_of_service):
            continue
        status = ref.code_status(line.code, line.date_of_service)
        if status is not None and status.status in INVALID_STATUSES:
            dos_iso = line.date_of_service.isoformat()
            if status.status == "D":
                message = f"{line.code} is a deleted code and was not billable on {dos_iso}"
            else:  # status == "I"
                message = (
                    f"{line.code} is not valid for Medicare billing on {dos_iso}; "
                    "Medicare requires a different code"
                )
            flags.append(
                make_flag(
                    claim,
                    "R4",
                    Severity.ERROR,
                    [line],
                    Evidence(
                        table="pfs_status",
                        ref_version=status.ref_version,
                        row={"code": line.code, "status": status.status},
                    ),
                    line.charge,
                    message,
                )
            )
    return flags
