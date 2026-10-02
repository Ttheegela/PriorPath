from decimal import Decimal

import pytest

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


SRC = [*build_prompt(FLAG)[1], "service date 2026-10-15"]


@pytest.mark.parametrize(
    "text",
    [
        "$7.00",
        "5%",
        "10 percent",
        "$4k",
        "4.000,00",
        "4 000",
        "$99213",
        "$2026",
        "four thousand dollars",
        "$1,00,000",
        "7 dollars",
        "USD 7",
        "US$ 7",
        "$ 7",
        "7 bucks",
        "50 cents",
        "seven dollars",
        "eighty percent",
        "ten dollars",
        "$7\u066b00",
    ],
)
def test_bypass_attempts_are_rejected(text: str) -> None:
    assert unsupported_numbers(text, SRC) != []


@pytest.mark.parametrize(
    "text",
    [
        "billed 2 times",
        "3 times the rate",
        "$92.15",
        "92.15",
        "$23.55",
        "on 2026-10-15",
        "$23.55 above the benchmark",
        "about 23.55 dollars",
    ],
)
def test_grounded_phrases_are_accepted(text: str) -> None:
    assert unsupported_numbers(text, SRC) == []


def test_prompt_excludes_provider_and_payer() -> None:
    c = claim(line("L1", code="99213", charge="300.00")).model_copy(
        update={"provider": "Acme Clinic", "payer": "Aetna"}
    )
    prompt, sources = build_prompt(
        make_flag(c, "R5", Severity.OUTLIER, c.lines, FLAG.evidence, Decimal("23.55"), FLAG.message)
    )
    assert "Acme" not in prompt and "Aetna" not in prompt
    assert not any("Acme" in x or "Aetna" in x for x in sources)


def test_detailed_distinguishes_failure_kinds() -> None:
    from app.llm.explain import LLM_FAILED, UNGROUNDED, explain_flag_detailed

    assert explain_flag_detailed(FLAG, FakeLLM(error=TimeoutError("slow"))) == (None, LLM_FAILED)
    assert explain_flag_detailed(FLAG, FakeLLM(["It is $5,000.", "It is $6,000."])) == (None, UNGROUNDED)
    text, reason = explain_flag_detailed(FLAG, FakeLLM(["This line repeats a charge."]))
    assert text and reason is None


def test_llm_failure_is_logged_without_content(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="app.llm.explain"):
        assert explain_flag(FLAG, FakeLLM(error=TimeoutError("secret prompt text"))) is None
    assert "explanation LLM call failed: TimeoutError" in caplog.text
    assert "secret prompt text" not in caplog.text


def test_drafts_with_urls_are_rejected() -> None:
    llm = FakeLLM(["See https://example.com for details.", "Visit www.example.com now."])
    assert explain_flag(FLAG, llm) is None
    assert len(llm.calls) == 2
