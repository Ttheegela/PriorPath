"""Dispute letters are templates over accepted flags: the reviewer, not a model, decides what goes in."""

from datetime import date

from app.db.models import FlagRow
from app.models import Claim
from app.rules.totals import case_totals
from app.services.cases import flag_from_row

LETTER_SEVERITIES = ("error", "outlier")


def build_letter(claim: Claim, rows: list[FlagRow], ref_versions: list[str], today: date) -> str:
    accepted = [r for r in rows if r.status == "accepted" and r.severity in LETTER_SEVERITIES]
    errors = [r for r in accepted if r.severity == "error"]
    outliers = [r for r in accepted if r.severity == "outlier"]
    totals = case_totals(claim, [flag_from_row(r, claim.id) for r in accepted])
    parts = [
        today.isoformat(),
        f"To: {claim.provider or 'Provider'} billing department",
        f"Re: review of itemized charges, claim {claim.id}",
        "",
        "We reviewed the itemized charges on this claim against Medicare's public billing rules "
        f"({', '.join(ref_versions)}) and found the following.",
    ]
    if errors:
        parts += ["", "Billing errors:"]
        for i, r in enumerate(errors, start=1):
            parts.append(f"{i}. {r.message}. Estimated overcharge: ${r.est_overcharge}.")
            if r.explanation:
                parts.append(f"   {r.explanation}")
    if outliers:
        parts += ["", "Charges well above the Medicare benchmark (please provide an itemized justification):"]
        for i, r in enumerate(outliers, start=1):
            parts.append(f"{i}. {r.message}.")
    parts += ["", "Requested action: correct the billing errors above and send a revised itemized bill."]
    if errors:
        parts.append(f"Total estimated overcharge from billing errors: ${totals.errors}.")
    parts += [
        "",
        "These findings come from automated rule checks and were reviewed by a person before sending.",
    ]
    return "\n".join(parts)
