from collections import defaultdict
from datetime import date
from itertools import combinations

from app.models import Claim, Evidence, Flag, LineItem, Severity, make_flag
from app.reference.base import Reference
from app.rules import RuleConfig

# CMS "NCCI-associated modifiers" that can bypass an edit with modifier indicator 1.
NCCI_MODIFIERS = frozenset(
    {
        "E1",
        "E2",
        "E3",
        "E4",
        "FA",
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
        "LC",
        "LD",
        "LM",
        "LT",
        "RC",
        "RI",
        "RT",
        "TA",
        "T1",
        "T2",
        "T3",
        "T4",
        "T5",
        "T6",
        "T7",
        "T8",
        "T9",
        "24",
        "25",
        "27",
        "57",
        "58",
        "59",
        "78",
        "79",
        "91",
        "XE",
        "XS",
        "XP",
        "XU",
    }
)


def check_ncci(claim: Claim, ref: Reference, config: RuleConfig) -> list[Flag]:
    by_dos: dict[date, list[LineItem]] = defaultdict(list)
    for line in claim.lines:
        if ref.covers(line.date_of_service):
            by_dos[line.date_of_service].append(line)
    flags = []
    for dos, lines in by_dos.items():
        for a, b in combinations(lines, 2):
            if a.code == b.code:
                continue
            for c1, c2 in ((a, b), (b, a)):
                edit = ref.ptp(c1.code, c2.code, dos)
                if edit is None:
                    continue
                if edit.modifier_indicator == "9":
                    break
                if edit.modifier_indicator == "1" and NCCI_MODIFIERS & (
                    set(c1.modifiers) | set(c2.modifiers)
                ):
                    break
                flags.append(
                    make_flag(
                        claim,
                        "R2",
                        Severity.ERROR,
                        [c1, c2],
                        Evidence(
                            table="ncci_ptp",
                            ref_version=edit.ref_version,
                            row={
                                "column_1": edit.col1,
                                "column_2": edit.col2,
                                "effective": edit.effective.isoformat(),
                                "deleted": edit.deleted.isoformat() if edit.deleted else "",
                                "modifier_indicator": edit.modifier_indicator,
                            },
                        ),
                        c2.charge,
                        f"{c2.code} is bundled into {c1.code} on {dos.isoformat()} "
                        f"(NCCI edit, modifier indicator {edit.modifier_indicator})",
                    )
                )
                break
    return flags
