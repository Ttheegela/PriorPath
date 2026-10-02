from collections import defaultdict
from datetime import date

from app.models import Claim, Evidence, Flag, LineItem, Severity, make_flag
from app.reference.base import Reference
from app.rules import RuleConfig

REPEAT_MODIFIERS = frozenset({"76", "77", "91"})


def check_duplicates(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    groups: dict[tuple[str, tuple[str, ...], date, int], list[LineItem]] = defaultdict(list)
    for line in claim.lines:
        if REPEAT_MODIFIERS & set(line.modifiers):
            continue
        groups[(line.code, tuple(sorted(line.modifiers)), line.date_of_service, line.units)].append(line)
    flags = []
    for (code, mods, dos, units), lines in groups.items():
        if len(lines) < 2:
            continue
        lines = sorted(lines, key=lambda x: x.id)
        extra = sum((x.charge for x in lines[1:]), start=lines[0].charge * 0)
        flags.append(
            make_flag(
                claim,
                "R1",
                Severity.ERROR,
                lines,
                Evidence(
                    table="claim_lines",
                    ref_version=None,
                    row={
                        "code": code,
                        "modifiers": "+".join(mods),
                        "date_of_service": dos.isoformat(),
                        "units": str(units),
                    },
                ),
                extra,
                f"{code} billed {len(lines)} times on {dos.isoformat()} with identical units and modifiers",
            )
        )
    return flags
