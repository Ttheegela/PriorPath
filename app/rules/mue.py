from collections import defaultdict
from datetime import date

from app.models import Claim, Evidence, Flag, LineItem, Severity, make_flag
from app.reference.base import MueLimit, Reference
from app.rules import RuleConfig


def _flag(claim: Claim, lines: list[LineItem], lim: MueLimit, billed: int) -> Flag:
    total_charge = sum((x.charge for x in lines), start=lines[0].charge * 0)
    excess = billed - lim.max_units
    dos = lines[0].date_of_service.isoformat()
    scope = "on one line" if lim.mai == 1 else "on one date of service"
    return make_flag(
        claim,
        "R3",
        Severity.ERROR,
        lines,
        Evidence(
            table="mue",
            ref_version=lim.ref_version,
            row={
                "code": lim.code,
                "max_units": str(lim.max_units),
                "mai": str(lim.mai),
                "billed_units": str(billed),
            },
        ),
        excess * total_charge / billed,
        f"{lim.code}: {billed} units billed {scope} ({dos}); Medicare's unit limit is {lim.max_units}",
    )


def check_mue(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    groups: dict[tuple[str, date], list[LineItem]] = defaultdict(list)
    for line in claim.lines:
        if ref.covers(line.date_of_service):
            groups[(line.code, line.date_of_service)].append(line)
    flags = []
    for (code, dos), lines in groups.items():
        lim = ref.mue(code, dos)
        if lim is None:
            continue
        if lim.mai == 1:
            flags += [_flag(claim, [x], lim, x.units) for x in lines if x.units > lim.max_units]
        else:
            billed = sum(x.units for x in lines)
            if billed > lim.max_units:
                flags.append(_flag(claim, sorted(lines, key=lambda x: x.id), lim, billed))
    return flags
