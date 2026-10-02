from collections import Counter

from evals.generate import PLANTABLE, generate
from tests.helpers import FIXTURE_REF


def test_deterministic_for_seed() -> None:
    a = generate(FIXTURE_REF, 20, seed=1)
    b = generate(FIXTURE_REF, 20, seed=1)
    assert [x.claim.model_dump() for x in a] == [x.claim.model_dump() for x in b]
    assert [x.expected for x in a] == [x.expected for x in b]


def test_line_ids_are_sequential() -> None:
    for lc in generate(FIXTURE_REF, 30, seed=2):
        assert [x.id for x in lc.claim.lines] == [f"L{i + 1}" for i in range(len(lc.claim.lines))]


def test_clean_claims_have_no_expected_flags_and_errors_are_labeled() -> None:
    out = generate(FIXTURE_REF, 100, seed=3, error_rate=0.5)
    clean = [x for x in out if not x.planted]
    dirty = [x for x in out if x.planted]
    assert clean and dirty
    assert all(not x.expected for x in clean)
    assert all(len(x.expected) == len(x.planted) for x in dirty)
    kinds = Counter(k for x in dirty for k in x.planted)
    assert set(kinds) <= set(PLANTABLE)
