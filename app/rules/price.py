from app.models import Claim, Evidence, Flag, Severity, make_flag
from app.reference.base import Reference
from app.rules import RuleConfig

# CMS place-of-service codes paid at the facility rate.
FACILITY_POS = frozenset(
    {"19", "21", "22", "23", "24", "26", "31", "34", "41", "42", "51", "52", "53", "56", "61"}
)
PRO_TECH_MODIFIERS = frozenset({"26", "TC"})


def check_price(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    k = config.price_multiplier
    flags = []
    for line in claim.lines:
        if not ref.covers(line.date_of_service) or PRO_TECH_MODIFIERS & set(line.modifiers):
            continue
        fee = ref.fee(line.code, line.date_of_service)
        if fee is None:
            continue
        facility = line.place_of_service in FACILITY_POS
        rate = fee.facility if facility else fee.nonfacility
        if rate <= 0:
            continue
        benchmark = k * rate * line.units
        if line.charge > benchmark:
            flags.append(
                make_flag(
                    claim,
                    "R5",
                    Severity.OUTLIER,
                    [line],
                    Evidence(
                        table="pfs_rates",
                        ref_version=fee.ref_version,
                        row={
                            "code": line.code,
                            "rate_type": "facility" if facility else "nonfacility",
                            "medicare_rate": str(rate),
                            "multiplier": str(k),
                            "units": str(line.units),
                        },
                    ),
                    line.charge - benchmark,
                    f"{line.code} charged {line.charge} for {line.units} unit(s); that is more than {k}x the "
                    f"Medicare national rate of {rate} per unit",
                )
            )
    return flags
