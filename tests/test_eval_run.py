from evals.generate import LabeledClaim, Negative, generate
from evals.run import evaluate
from tests.helpers import FIXTURE_REF, claim, line


def test_engine_scores_perfectly_on_fixture_generated_claims() -> None:
    report = evaluate(generate(FIXTURE_REF, 150, seed=5), FIXTURE_REF)
    assert report.parse_errors == 0
    assert report.clean_fp == 0
    for score in report.scores.values():
        assert score.precision == 1.0 and score.recall == 1.0, score
    for rule in ("R1", "R2", "R3", "R4", "R5"):
        assert report.scores[rule].support > 0, rule


def test_false_positive_and_miss_are_counted() -> None:
    dup = claim(line("L1"), line("L2"))
    labeled = [
        LabeledClaim(
            claim=dup, expected=set(), planted=[]
        ),  # R1 will fire: false positive on a "clean" claim
        LabeledClaim(claim=claim(line("L1")), expected={("R5", frozenset({"L1"}))}, planted=["R5"]),  # miss
    ]
    report = evaluate(labeled, FIXTURE_REF, via_fhir=False)
    assert report.clean_fp == 1
    assert report.scores["R1"].fp == 1
    assert report.scores["R5"].fn == 1
    failures = report.gate_failures(min_support=0)
    assert any("R1" in f for f in failures) and any("R5" in f for f in failures)
    assert "| R1 |" in report.to_markdown()


def test_negative_plants_are_never_flagged_on_fixture_claims() -> None:
    report = evaluate(generate(FIXTURE_REF, 200, seed=4), FIXTURE_REF)
    for rule in ("R1", "R2", "R5"):
        assert report.scores[rule].neg_support > 0, rule
    assert all(s.neg_fp == 0 for s in report.scores.values())
    assert not [f for f in report.gate_failures(min_support=0) if "negative" in f]


def test_flagged_negative_plant_fails_the_gate() -> None:
    # labelled "must not flag R1", but neither line carries 76/91, so R1 fires
    dup = claim(line("L1"), line("L2"))
    neg = Negative("R1-repeat-76-91", "R1", frozenset({"L1", "L2"}))
    report = evaluate([LabeledClaim(claim=dup, expected=set(), planted=[], negatives=[neg])], FIXTURE_REF)
    assert report.scores["R1"].neg_support == 1
    assert report.scores["R1"].neg_fp == 1
    assert any("negative" in f and "R1" in f for f in report.gate_failures(min_support=0))
    assert "Neg FP" in report.to_markdown()


def test_min_support_gate() -> None:
    report = evaluate([], FIXTURE_REF)
    assert any("support" in f for f in report.gate_failures(min_support=1))
