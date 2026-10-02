from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.models import Claim, Flag
from app.reference.base import Reference


@dataclass(frozen=True)
class RuleConfig:
    price_multiplier: Decimal = Decimal("3")


Rule = Callable[[Claim, Reference, RuleConfig], list[Flag]]


def _all_rules() -> list[Rule]:
    from app.rules.coverage import check_coverage
    from app.rules.duplicates import check_duplicates
    from app.rules.mue import check_mue
    from app.rules.ncci import check_ncci

    return [check_coverage, check_duplicates, check_ncci, check_mue]


ALL_RULES: list[Rule] = _all_rules()


def run_rules(claim: Claim, ref: Reference, config: RuleConfig | None = None) -> list[Flag]:
    cfg = config or RuleConfig()
    return [flag for rule in ALL_RULES for flag in rule(claim, ref, cfg)]
