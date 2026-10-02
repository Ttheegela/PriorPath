from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models import Claim, LineItem
from app.reference.normalized import load_normalized

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "reference"
FIXTURE_REF = load_normalized(FIXTURE_DIR)
DOS = date(2026, 10, 15)


def line(
    id: str = "L1",
    code: str = "99213",
    units: int = 1,
    charge: str = "150.00",
    dos: date = DOS,
    modifiers: list[str] | None = None,
    pos: str | None = "11",
) -> LineItem:
    return LineItem(
        id=id,
        code=code,
        units=units,
        charge=Decimal(charge),
        date_of_service=dos,
        modifiers=modifiers or [],
        place_of_service=pos,
    )


def claim(*lines: LineItem) -> Claim:
    return Claim(id="C1", patient_pseudonym="P-1", lines=list(lines))
