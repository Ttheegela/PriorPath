from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models import Claim, Evidence, LineItem, Severity, make_flag, money


def _line(**kw: object) -> LineItem:
    base: dict[str, object] = {
        "id": "L1",
        "code": "99213",
        "units": 1,
        "charge": Decimal("150.00"),
        "date_of_service": date(2026, 10, 15),
    }
    base.update(kw)
    return LineItem(**base)  # type: ignore[arg-type]


def test_code_and_modifiers_are_normalized() -> None:
    line = _line(code=" g0008 ", modifiers=[" xs", "rt ", ""])
    assert line.code == "G0008"
    assert line.modifiers == ["XS", "RT"]


def test_units_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _line(units=0)


def test_charge_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        _line(charge=Decimal("-1.00"))


def test_empty_code_rejected() -> None:
    with pytest.raises(ValidationError):
        _line(code="   ")


def test_unit_price() -> None:
    assert _line(units=4, charge=Decimal("90.00")).unit_price == Decimal("22.5")


def test_money_rounds_to_cents() -> None:
    assert money(Decimal("10.005")) == Decimal("10.01")


def test_make_flag_builds_deterministic_id_and_rounds() -> None:
    l1, l2 = _line(id="L1"), _line(id="L2")
    claim = Claim(id="C9", patient_pseudonym="P-1", lines=[l1, l2])
    flag = make_flag(
        claim,
        "R1",
        Severity.ERROR,
        [l1, l2],
        Evidence(table="claim_lines", ref_version=None, row={"code": "99213"}),
        Decimal("150.004"),
        "Line L2 duplicates line L1",
    )
    assert flag.id == "C9:R1:L1+L2"
    assert flag.line_ids == ["L1", "L2"]
    assert flag.est_overcharge == Decimal("150.00")
    assert flag.status == "open"
