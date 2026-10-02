"""Numbers an explanation may use must come from the evidence it explains."""

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SMALL_INTEGERS = {str(i) for i in range(11)}  # counts like "2 lines" or "3 times" are allowed


def numbers_in(text: str) -> set[str]:
    out = set()
    for token in _NUMBER.findall(text):
        try:
            value = Decimal(token.replace(",", ""))
        except InvalidOperation:
            continue
        out.add(format(value.normalize(), "f"))
    return out


def unsupported_numbers(text: str, sources: Iterable[str]) -> list[str]:
    allowed = set(SMALL_INTEGERS)
    for source in sources:
        allowed |= numbers_in(source)
    return sorted(numbers_in(text) - allowed)
