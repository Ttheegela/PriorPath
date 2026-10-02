from collections import Counter

from app.rules.price import FACILITY_POS
from evals.generate import NEGATIVE_KINDS, PLANTABLE, generate
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


def test_negative_plants_cover_every_kind_and_never_overlap_expected_flags() -> None:
    out = generate(FIXTURE_REF, 200, seed=4)
    kinds = Counter(n.kind for x in out for n in x.negatives)
    assert set(kinds) == set(NEGATIVE_KINDS)
    for x in out:
        expected_lines = {i for _, ids in x.expected for i in ids}
        for n in x.negatives:
            assert n.rule_id == NEGATIVE_KINDS[n.kind]
            assert not n.line_ids & expected_lines


def test_negative_plants_have_the_exempting_shape() -> None:
    for lc in generate(FIXTURE_REF, 200, seed=4):
        by_id = {x.id: x for x in lc.claim.lines}
        for n in lc.negatives:
            lines = [by_id[i] for i in sorted(n.line_ids, key=lambda i: int(i[1:]))]
            if n.kind == "R1-repeat-76-91":
                a, b, c = lines
                assert a.code == b.code == c.code and a.units == b.units == c.units
                assert not a.modifiers and b.modifiers == c.modifiers and b.modifiers[0] in {"76", "91"}
            elif n.kind == "R2-ind1-59-XS":
                a, b = lines
                edit = FIXTURE_REF.ptp(a.code, b.code, a.date_of_service)
                assert edit is not None and edit.modifier_indicator == "1"
                assert not a.modifiers and b.modifiers[0] in {"59", "XS"}
            elif n.kind == "R5-26-TC":
                (x,) = lines
                fee = FIXTURE_REF.fee(x.code, x.date_of_service)
                assert fee is not None and x.charge > 3 * fee.nonfacility * x.units
                assert x.modifiers[0] in {"26", "TC"}
            else:  # R5-office-band: office line priced between 3x facility and 3x non-facility
                (x,) = lines
                fee = FIXTURE_REF.fee(x.code, x.date_of_service)
                assert fee is not None and x.place_of_service not in FACILITY_POS
                assert 3 * fee.facility * x.units < x.charge < 3 * fee.nonfacility * x.units


def test_r5_plants_include_facility_lines_priced_in_the_band() -> None:
    band = 0
    for lc in generate(FIXTURE_REF, 300, seed=6):
        by_id = {x.id: x for x in lc.claim.lines}
        for rule, ids in lc.expected:
            if rule != "R5":
                continue
            (x,) = [by_id[i] for i in ids]
            if x.place_of_service not in FACILITY_POS:
                continue
            fee = FIXTURE_REF.fee(x.code, x.date_of_service)
            assert fee is not None
            assert 3 * fee.facility * x.units < x.charge < 3 * fee.nonfacility * x.units
            band += 1
    assert band > 0
