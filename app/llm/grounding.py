"""Numbers an explanation may use must come from the evidence it explains.

Tokens are typed: money (dollar sign or exactly 2 decimals) and percents must match a source of the
same type; plain integers match any source number or are small counts (0-10).
"""

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

_TOKEN = re.compile(
    r"(\$?)(\d+(?:[,.]\d+)*)(?:(\s?(?:%|percent\b))|(\s?(?:k|m|bn|thousand|million|billion)\b))?", re.I
)
_GROUPED = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_WORDS = re.compile(r"\b(hundred|thousand|million|billion)\b", re.I)
SMALL_INTEGERS = {str(i) for i in range(11)}  # counts like "2 lines" or "3 times" are allowed


def _norm(raw: str) -> str | None:
    """Normalized value, or None for malformed numbers like 4.000,00 or 1,00,000."""
    if "," in raw and not _GROUPED.match(raw):
        return None
    try:
        return format(Decimal(raw.replace(",", "")).normalize(), "f")
    except InvalidOperation:
        return None


def _tokens(text: str) -> list[tuple[str, str | None, str]]:
    """(raw, normalized, kind) with kind in money / percent / plain / scaled."""
    out = []
    for m in _TOKEN.finditer(text):
        dollar, raw, pct, scale = m.groups()
        decimals = len(raw.rsplit(".", 1)[1]) if "." in raw and "," not in raw.rsplit(".", 1)[1] else 0
        if scale:
            kind = "scaled"
        elif pct:
            kind = "percent"
        elif dollar or ("." in raw and decimals == 2):
            kind = "money"
        else:
            kind = "plain"
        out.append((raw, _norm(raw), kind))
    return out


def numbers_in(text: str) -> set[str]:
    return {n for _, n, _ in _tokens(text) if n is not None}


def unsupported_numbers(text: str, sources: Iterable[str]) -> list[str]:
    money: set[str] = set()
    percent: set[str] = set()
    plain: set[str] = set()
    plain_raw: set[str] = set()
    for source in sources:
        for raw, n, kind in _tokens(source):
            if n is None:
                continue
            if kind == "money":
                money.add(n)
            elif kind == "percent":
                percent.add(n)
            else:
                plain.add(n)
                plain_raw.add(raw)
    bad = {w.lower() for w in _WORDS.findall(text)}
    for raw, n, kind in _tokens(text):
        if n is None or kind == "scaled":
            ok = False
        elif kind == "money":
            ok = n in money
        elif kind == "percent":
            ok = n in percent
        elif raw.startswith("0") and len(raw) > 1 and "." not in raw:
            ok = raw in plain_raw
        else:
            ok = n in plain or ("." not in raw and n in SMALL_INTEGERS)
        if not ok:
            bad.add(n or raw)
    return sorted(bad)
