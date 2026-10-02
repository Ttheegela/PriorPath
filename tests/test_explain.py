from decimal import Decimal

from app.llm.explain import build_prompt, explain_flag
from app.llm.grounding import numbers_in, unsupported_numbers
from app.models import Evidence, Severity, make_flag
from tests.fakes import FakeLLM
from tests.helpers import claim, line

C = claim(line("L1", code="99213", charge="300.00"))
FLAG = make_flag(
    C,
    "R5",
    Severity.OUTLIER,
    C.lines,
    Evidence(
        table="pfs_rates",
        ref_version="PFS-TEST",
        row={"code": "99213", "medicare_rate": "92.15", "multiplier": "3", "units": "1"},
    ),
    Decimal("23.55"),
    "99213 charged 300.00 for 1 unit(s); that is more than 3x the Medicare national rate of 92.15 per unit",
)
GROUNDED = (
    "This visit was billed at $300.00, more than 3 times Medicare's $92.15 rate, about $23.55 "
    "above that benchmark."
)


def test_numbers_are_normalized() -> None:
    assert numbers_in("$1,200.50 and 92.150 on 2026-10-15") == {"1200.5", "92.15", "2026", "10", "15"}


def test_unsupported_numbers() -> None:
    sources = ["rate 92.15", "overcharge 23.55"]
    assert unsupported_numbers("about $23.55 over 92.15", sources) == []
    assert unsupported_numbers("about $999 over 92.15", sources) == ["999"]
    assert unsupported_numbers("billed 2 times", sources) == []  # small integers are allowed


def test_prompt_contains_no_patient_or_provider_identity() -> None:
    prompt, sources = build_prompt(FLAG)
    assert "P-1" not in prompt and "C1" not in prompt
    assert "92.15" in prompt and any("23.55" in s for s in sources)


def test_grounded_first_draft_is_returned() -> None:
    llm = FakeLLM([GROUNDED])
    assert explain_flag(FLAG, llm) == GROUNDED
    assert len(llm.calls) == 1


def test_ungrounded_draft_is_retried_with_feedback() -> None:
    llm = FakeLLM(["The overcharge is $4,000.", GROUNDED])
    assert explain_flag(FLAG, llm) == GROUNDED
    assert len(llm.calls) == 2 and "4000" in llm.calls[1]


def test_still_ungrounded_after_retry_returns_none() -> None:
    llm = FakeLLM(["The overcharge is $4,000.", "It is $5,000."])
    assert explain_flag(FLAG, llm) is None
    assert len(llm.calls) == 2


def test_empty_reply_and_llm_errors_return_none() -> None:
    assert explain_flag(FLAG, FakeLLM(["", ""])) is None
    assert explain_flag(FLAG, FakeLLM(error=TimeoutError("slow"))) is None
